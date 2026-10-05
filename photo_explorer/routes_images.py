"""Indexed image metadata, derivatives, and file actions."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import fileops
from .config import BROWSER_FORMATS, MIME_TYPES
from .metadata import read_metadata

router = APIRouter(prefix="/api")


class RatingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: StrictInt | None = Field(None, ge=0, le=5)
    favorite: bool | None = None


class DestinationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dest: str


class RenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class RotateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    degrees: StrictInt


def require_image(request: Request, image_id: int) -> dict:
    """Resolve file targets exclusively through indexed image ids."""
    row = request.app.state.db.one("SELECT * FROM images WHERE id=?", (image_id,))
    if row is None:
        raise HTTPException(404, "Image not found")
    return row


def media_response(request: Request, row: dict, mode: int | str) -> Response:
    """Serve an immutable derivative or a browser-safe original."""
    source = Path(row["display_path"])
    if not source.is_file():
        request.app.state.db.execute("DELETE FROM images WHERE id=?", (row["id"],))
        raise HTTPException(404, "The image file no longer exists. Rescan the folder.")
    original = row["ext"] == "svg" or (mode == "file" and row["ext"] in BROWSER_FORMATS)
    headers = {"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"}
    if original:
        key = request.app.state.cache.key(row)
        path = source
        media_type = MIME_TYPES.get(row["ext"], "application/octet-stream")
        if row["ext"] == "svg":
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
    else:
        render_mode = "full" if mode == "file" else mode
        path, key = request.app.state.cache.render(row, render_mode)
        media_type = "image/png" if render_mode == "full" else "image/jpeg"
        if mode == "file":
            headers["X-Rendered"] = "1"
    headers["ETag"] = f'"{key}"'
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type=media_type, headers=headers)


@router.get("/images/{image_id}")
def image_metadata(image_id: int, request: Request) -> dict:
    """Return all indexed fields, readable EXIF, and derivative-cache details."""
    row = require_image(request, image_id)
    try:
        row["exif"] = read_metadata(row["display_path"]).get("exif", {})
    except Exception:
        row["exif"] = {}
    try:
        row["cache"] = request.app.state.cache.info(row)
    except OSError:
        row["cache"] = {"key": None, "files": []}
    return row


@router.patch("/images/{image_id}")
def annotate(image_id: int, body: RatingBody, request: Request) -> dict:
    """Store ratings and favourites in SQLite without changing the source."""
    require_image(request, image_id)
    values = body.model_dump(exclude_none=True)
    if values:
        request.app.state.db.execute(
            f"UPDATE images SET {','.join(f'{key}=?' for key in values)} WHERE id=?", [*values.values(), image_id])
    return require_image(request, image_id)


@router.get("/images/{image_id}/thumb")
def thumb(image_id: int, request: Request, s: int = 256) -> Response:
    """Return a small aspect-preserving oriented image."""
    if s not in {160, 256, 400}:
        raise ValueError("Thumbnail size must be 160, 256, or 400")
    return media_response(request, require_image(request, image_id), s)


@router.get("/images/{image_id}/preview")
def preview(image_id: int, request: Request) -> Response:
    """Return an oriented preview bounded by 2048 pixels."""
    return media_response(request, require_image(request, image_id), "p")


@router.get("/images/{image_id}/file")
def original_file(image_id: int, request: Request) -> Response:
    """Return original bytes or a full PNG for formats browsers cannot decode."""
    return media_response(request, require_image(request, image_id), "file")


def action_lock(request: Request, image_id: int) -> threading.RLock:
    """Serialize file mutations against scans of their owning root."""
    row = require_image(request, image_id)
    return request.app.state.scanner.root_lock(row["root_id"])


@router.post("/images/{image_id}/trash")
def trash(image_id: int, request: Request) -> dict:
    """Move an image to the Recycle Bin."""
    with action_lock(request, image_id):
        return fileops.trash(request.app.state.db, require_image(request, image_id))


@router.post("/images/{image_id}/move")
def move(image_id: int, body: DestinationBody, request: Request) -> dict:
    """Move an indexed image to an existing directory without overwriting."""
    with action_lock(request, image_id):
        return fileops.transfer(request.app.state.db, require_image(request, image_id), body.dest, True)


@router.post("/images/{image_id}/copy")
def copy(image_id: int, body: DestinationBody, request: Request) -> dict:
    """Copy an indexed image and index the copy when its root is open."""
    with action_lock(request, image_id):
        return fileops.transfer(request.app.state.db, require_image(request, image_id), body.dest, False)


@router.post("/images/{image_id}/rename")
def rename(image_id: int, body: RenameBody, request: Request) -> dict:
    """Rename without overwriting another file."""
    with action_lock(request, image_id):
        return fileops.rename(request.app.state.db, require_image(request, image_id), body.name)


@router.post("/images/{image_id}/reveal")
def reveal(image_id: int, request: Request) -> dict:
    """Select a file in Windows Explorer."""
    fileops.reveal(require_image(request, image_id))
    return {"ok": True}


@router.post("/images/{image_id}/open")
def open_image(image_id: int, request: Request) -> dict:
    """Open with the default system application."""
    fileops.open_default(require_image(request, image_id))
    return {"ok": True}


@router.get("/destinations")
def destinations(request: Request) -> list[dict]:
    """Return recent move and copy folders."""
    return request.app.state.db.all("SELECT * FROM destinations ORDER BY last_used DESC LIMIT 20")


@router.post("/images/{image_id}/rotate")
def rotate(image_id: int, body: RotateBody, request: Request) -> dict:
    """Rotate an indexed still image on disk after the client confirms."""
    with action_lock(request, image_id):
        return fileops.rotate(request.app.state.db, require_image(request, image_id), body.degrees)


@router.post("/images/{image_id}/wallpaper")
def wallpaper(image_id: int, request: Request) -> dict:
    """Set desktop wallpaper using an indexed image."""
    with action_lock(request, image_id):
        fileops.wallpaper(require_image(request, image_id), request.app.state.cache)
    return {"ok": True}
