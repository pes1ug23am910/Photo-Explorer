"""Exact duplicate scan, group browsing, and resolution endpoints."""

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field

from .routes_roots import require_root
from .rules import StrictModel

router = APIRouter(prefix="/api")


class ScanBody(StrictModel):
    mode: Literal["exact", "near"] = "exact"
    threshold: int = Field(6, ge=0, le=16)


class ResolveBody(StrictModel):
    group_ids: list[int] = Field(min_length=1)
    keep: str | dict[str, int] = "largest"
    action: Literal["move", "trash"] = "move"
    dest: str | None = None
    confirm: bool = False


@router.post("/roots/{root_id}/duplicates/scan")
def scan(root_id: int, request: Request, body: ScanBody | None = None) -> dict:
    require_root(request, root_id)
    body = body or ScanBody()
    return request.app.state.duplicates.start(root_id, body.mode, body.threshold)


@router.get("/roots/{root_id}/duplicates")
def groups(root_id: int, request: Request, run_id: int | None = None,
           offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)) -> dict:
    require_root(request, root_id)
    return request.app.state.duplicates.groups(root_id, run_id, offset, limit)


@router.post("/duplicates/preview")
def preview(body: ResolveBody, request: Request) -> dict:
    _, entries = request.app.state.duplicates.plan(body.group_ids, body.keep, body.action, body.dest)
    return {"total": len(entries), "bytes": sum(entry["size"] for entry in entries), "entries": entries[:2000]}


@router.post("/duplicates/resolve")
def resolve(body: ResolveBody, request: Request) -> dict:
    if body.action == "trash" and not body.confirm:
        raise ValueError("Recycle Bin resolution requires confirm: true")
    root_id, entries = request.app.state.duplicates.plan(body.group_ids, body.keep, body.action, body.dest)
    return request.app.state.operations.run("duplicates", f"Resolve {len(body.group_ids)} duplicate groups", root_id,
                                             body.model_dump(), entries, undoable=body.action != "trash")
