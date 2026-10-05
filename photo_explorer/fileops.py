"""Validated, non-overwriting local file actions."""

from __future__ import annotations

import os
import ctypes
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from send2trash import send2trash
from PIL import Image, ImageOps

from .config import EXTENSIONS, canonical, inside, validate_directory
from .db import Database
from .scanner import basic_record, metadata_result, upsert_records, write_metadata


class ConflictError(ValueError):
    """A destination already exists or the action would overwrite a file."""


def validate_name(name: str) -> str:
    """Validate a portable Windows filename, including reserved device names."""
    device_digits = "123456789¹²³"
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in device_digits), *(f"LPT{i}" for i in device_digits)}
    if (not name or name != name.strip() or name.endswith(".") or name in {".", ".."}
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.split(".")[0].rstrip(" .").upper() in reserved or len(name) > 240):
        raise ValueError("Use a valid basename without separators or reserved device names")
    if Path(name).suffix.lower().lstrip(".") not in EXTENSIONS:
        raise ValueError("Keep a supported image extension")
    return name


def require_target(directory: str, name: str) -> Path:
    """Resolve an existing destination and refuse any overwrite."""
    target = validate_directory(directory) / name
    if target.exists():
        raise ConflictError("A file with that name already exists in the destination")
    return target


def require_unindexed_target(db: Database, target: Path) -> None:
    """Refuse a stale indexed destination before changing anything on disk."""
    if db.one("SELECT id FROM images WHERE path=?", (canonical(target),)):
        raise ConflictError("The destination name is still indexed. Rescan its library before retrying.")


def index_file(db: Database, root_id: int, path: Path) -> dict:
    """Immediately index a new copy or refresh an existing file."""
    upsert_records(db, [basic_record(root_id, path)])
    row = db.one("SELECT * FROM images WHERE path=?", (canonical(path),))
    if row is None:
        raise FileNotFoundError("Image could not be indexed")
    write_metadata(db, [metadata_result(row)])
    return db.one("SELECT * FROM images WHERE id=?", (row["id"],))


def containing_root(db: Database, path: Path) -> dict | None:
    """Find the most specific root whose indexing rules include this file."""
    roots = db.all("SELECT * FROM roots ORDER BY length(path) DESC")
    for root in roots:
        if inside(path, root["path"]) and (root["recursive"] or canonical(path.parent) == canonical(root["path"])):
            return root
    return None


def repoint(db: Database, row: dict, target: Path, root_id: int) -> dict:
    """Preserve identity and annotations when an image changes its path."""
    record = basic_record(root_id, target)
    record.pop("rnd")
    record.pop("sort_date")
    columns = tuple(record)
    db.execute(f"UPDATE images SET {','.join(f'{key}=?' for key in columns)} WHERE id=?",
               [*(record[key] for key in columns), row["id"]])
    return index_file(db, root_id, target)


def trash(db: Database, row: dict) -> dict:
    """Move one indexed image to the system Recycle Bin and remove its record."""
    send2trash(row["display_path"])
    db.execute("DELETE FROM images WHERE id=?", (row["id"],))
    return {"ok": True, "next_hint": None}


def transfer(db: Database, row: dict, dest: str, move: bool) -> dict:
    """Move or copy with exclusive destination reservation and recent tracking."""
    target = require_target(dest, row["name"])
    require_unindexed_target(db, target)
    # Reserving a filename closes the check-then-overwrite race on all platforms.
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        if move:
            try:
                # Same-volume moves rename over the reservation; a cross-volume move falls back to copy and delete.
                os.replace(row["display_path"], target)
            except OSError:
                shutil.move(row["display_path"], str(target))
        else:
            shutil.copy2(row["display_path"], target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    root = db.one("SELECT * FROM roots WHERE id=?", (row["root_id"],))
    included = root and inside(target, root["path"]) and (root["recursive"] or canonical(target.parent) == canonical(root["path"]))
    updated = None
    if move:
        if included:
            updated = repoint(db, row, target, row["root_id"])
        else:
            db.execute("DELETE FROM images WHERE id=?", (row["id"],))
    else:
        target_root = containing_root(db, target)
        if target_root:
            updated = index_file(db, target_root["id"], target)
    db.execute("INSERT OR REPLACE INTO destinations(path,last_used) VALUES (?,?)", (str(target.parent), time.time()))
    return {"ok": True, "path": str(target), "image": updated}


def rename(db: Database, row: dict, name: str) -> dict:
    """Rename within the current directory while retaining the image id."""
    validate_name(name)
    target = require_target(str(Path(row["display_path"]).parent), name)
    require_unindexed_target(db, target)
    # Windows rename refuses overwrite; exclusive reservation covers other hosts.
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        os.replace(row["display_path"], target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return repoint(db, row, target, row["root_id"])


def reveal(row: dict) -> None:
    """Ask Explorer to select the indexed file using an argument vector."""
    subprocess.run(["explorer.exe", f"/select,{row['display_path']}"], timeout=15, check=False)


def open_default(row: dict) -> None:
    """Open the indexed image with its registered Windows application."""
    os.startfile(row["display_path"])


def rotate(db: Database, row: dict, degrees: int) -> dict:
    """Atomically replace a still image with rotated pixels and reset EXIF orientation."""
    if degrees not in {90, 180, 270}:
        raise ValueError("Rotation must be 90, 180 or 270 degrees clockwise")
    if row["animated"] or row["ext"] == "svg":
        raise ValueError("Animated images and SVG cannot be rotated on disk")
    source = Path(row["display_path"])
    before = source.stat()
    temporary: Path | None = None
    try:
        with Image.open(source) as opened:
            if getattr(opened, "is_animated", False) or getattr(opened, "n_frames", 1) > 1:
                raise ValueError("Multi-frame images cannot be rotated on disk")
            format_name = opened.format
            exif = opened.getexif()
            oriented = ImageOps.exif_transpose(opened)
            exif[274] = 1
            rotated = oriented.rotate(-degrees, expand=True)
            oriented.close()
            try:
                descriptor, name = tempfile.mkstemp(prefix=".photo-explorer-", suffix=source.suffix, dir=source.parent)
                os.close(descriptor)
                temporary = Path(name)
                options = {"exif": exif.tobytes()}
                if opened.info.get("icc_profile"):
                    options["icc_profile"] = opened.info["icc_profile"]
                if format_name == "JPEG":
                    options.update(quality=95, optimize=True)
                    converted = rotated.convert("RGB")
                    rotated.close()
                    rotated = converted
                rotated.save(temporary, format=format_name, **options)
            finally:
                rotated.close()
        # An external editor may have changed the original while pixels decoded.
        current = source.stat()
        if (current.st_size, current.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise ConflictError("Image changed during rotation; rescan and retry")
        os.chmod(temporary, before.st_mode)
        os.replace(temporary, source)
        return index_file(db, row["root_id"], source)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def wallpaper(row: dict, cache) -> None:
    """Set a compatible image or cached PNG as the Windows desktop wallpaper."""
    if row["meta_state"] == 2:
        raise ValueError("A broken image cannot be used as wallpaper")
    path = row["display_path"]
    if row["ext"] not in {"jpg", "jpeg", "jpe", "jfif", "png", "bmp"}:
        if row["ext"] == "svg":
            raise ValueError("SVG wallpaper is unavailable because no SVG rasterizer is installed")
        rendered, _ = cache.render(row, "full")
        path = str(rendered)
    result = ctypes.windll.user32.SystemParametersInfoW(20, 0, path, 3)
    if not result:
        raise OSError("Windows could not set the desktop wallpaper")
