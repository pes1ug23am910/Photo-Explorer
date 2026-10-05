"""Root registration, scan progress, ordered lists, and aggregates."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictInt

from .config import canonical, inside, validate_directory
from .db import build_query

router = APIRouter(prefix="/api/roots")


class OpenRoot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    recursive: bool = True


class ShuffleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: StrictInt | None = None


def require_root(request: Request, root_id: int) -> dict:
    """Look up a registered root or produce a consistent 404."""
    root = request.app.state.db.one("SELECT * FROM roots WHERE id=?", (root_id,))
    if root is None:
        raise HTTPException(404, "Root not found")
    return root


@router.get("")
def roots(request: Request) -> list[dict]:
    """List recently opened libraries with their indexed image counts."""
    return request.app.state.db.all(
        "SELECT r.*, (SELECT COUNT(*) FROM images WHERE root_id=r.id) AS count FROM roots r ORDER BY opened DESC,id DESC")


@router.post("")
def open_root(body: OpenRoot, request: Request) -> dict:
    """Validate and register a directory, then start its background scan."""
    path = validate_directory(body.path)
    db = request.app.state.db
    identity = canonical(path)
    with request.app.state.scanner.guard:
        existing = db.one("SELECT * FROM roots WHERE path=?", (identity,))
        if existing:
            root_id = existing["id"]
            if request.app.state.scanner.status(root_id)["running"]:
                db.execute("UPDATE roots SET opened=? WHERE id=?", (time.time(), root_id))
            else:
                db.execute("UPDATE roots SET recursive=?,opened=? WHERE id=?", (int(body.recursive), time.time(), root_id))
        else:
            for root in db.all("SELECT * FROM roots"):
                if inside(identity, root["path"]) or inside(root["path"], identity):
                    raise HTTPException(409, "This folder overlaps an existing library. Use its folder tree, or forget that library first.")
            now = time.time()
            root_id = db.execute("INSERT INTO roots(path,recursive,created,opened) VALUES (?,?,?,?)",
                                 (identity, int(body.recursive), now, now))
        job = request.app.state.scanner.start(root_id)
    return {"root": require_root(request, root_id), "job": job}


@router.post("/{root_id}/rescan")
def rescan(root_id: int, request: Request) -> dict:
    """Schedule incremental indexing."""
    require_root(request, root_id)
    return {"job": request.app.state.scanner.start(root_id)}


@router.get("/{root_id}/scan")
def scan_status(root_id: int, request: Request) -> dict:
    """Read the latest scan's phase and counters."""
    require_root(request, root_id)
    return request.app.state.scanner.status(root_id)


@router.delete("/{root_id}")
def forget_root(root_id: int, request: Request) -> dict:
    """Forget only index rows and thumbnails; leave source files untouched."""
    require_root(request, root_id)
    with request.app.state.scanner.root_lock(root_id):
        rows = request.app.state.db.all("SELECT * FROM images WHERE root_id=?", (root_id,))
        request.app.state.cache.forget(rows, root_id)
        request.app.state.db.execute("DELETE FROM roots WHERE id=?", (root_id,))
    return {"ok": True}


@router.get("/{root_id}/images")
def images(root_id: int, request: Request) -> dict:
    """Page through the server's canonical sorted and filtered image sequence."""
    require_root(request, root_id)
    return request.app.state.db.listing(root_id, request.query_params)


@router.get("/{root_id}/images/position")
def position(root_id: int, image_id: int, request: Request) -> dict:
    """Locate an image within the same sequence used by navigation."""
    require_root(request, root_id)
    index = request.app.state.db.position(root_id, image_id, request.query_params)
    if index is None:
        raise HTTPException(404, "Image is not in this result set")
    return {"index": index}


@router.post("/{root_id}/shuffle")
def shuffle(root_id: int, request: Request, body: ShuffleBody | None = None) -> dict:
    """Generate and persist one reproducible shuffle sequence."""
    require_root(request, root_id)
    with request.app.state.scanner.root_lock(root_id):
        seed = request.app.state.db.shuffle(root_id, body.seed if body else None)
    return {"seed": seed}


@router.get("/{root_id}/stats")
def stats(root_id: int, request: Request) -> dict:
    """Return aggregate counts and sizes for the active filters."""
    require_root(request, root_id)
    return request.app.state.db.stats(root_id, request.query_params)


@router.get("/{root_id}/dirs")
def directories(root_id: int, request: Request) -> list[dict]:
    """Build a folder tree with inclusive descendant image counts."""
    root = require_root(request, root_id)
    root_path = root["path"]
    nodes: dict[str, dict] = {}

    def node(path: str) -> dict:
        if path not in nodes:
            nodes[path] = {"path": path, "name": Path(path).name or path, "count": 0, "children": []}
            if path != root_path:
                node(str(Path(path).parent))["children"].append(nodes[path])
        return nodes[path]

    node(root_path)
    for row in request.app.state.db.all("SELECT dir,COUNT(*) count FROM images WHERE root_id=? GROUP BY dir ORDER BY dir", (root_id,)):
        current = row["dir"]
        while inside(current, root_path):
            node(current)["count"] += row["count"]
            if current == root_path:
                break
            current = str(Path(current).parent)
    return [nodes[root_path]]


@router.get("/{root_id}/groups")
def date_groups(root_id: int, request: Request) -> list[dict]:
    """Describe date header runs in the same ordered, filtered image sequence."""
    require_root(request, root_id)
    sort = request.query_params.get("sort", "mtime")
    if sort not in {"mtime", "ctime", "taken"}:
        return []
    where, values, _ = build_query(root_id, request.query_params)
    column = "sort_date" if sort == "taken" else sort
    direction = request.query_params.get("order", "desc").upper()
    records = request.app.state.db.all(
        f"SELECT strftime('%Y-%m-%d',{column},'unixepoch','localtime') date, COUNT(*) count "
        f"FROM images WHERE {where} GROUP BY date ORDER BY date {direction}", values)
    offset = 0
    for record in records:
        record["start"] = offset
        offset += record["count"]
    return records
