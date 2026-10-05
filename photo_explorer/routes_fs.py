"""Local directory browsing and a thread-safe native folder picker."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .config import EXTENSIONS, access_path, validate_directory, visible_name
from .explorer import structure

router = APIRouter(prefix="/api/fs")


@router.get("/browse")
def browse(path: str | None = None, counts: bool = False) -> list[dict]:
    """List drives or visible child folders of an absolute existing directory."""
    if not path:
        if os.name == "nt":
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            return [{"name": f"{chr(65 + index)}:", "path": f"{chr(65 + index)}:\\"}
                    for index in range(26) if mask & (1 << index)]
        return [{"name": "/", "path": "/"}]
    directory = validate_directory(path)
    result = []
    with os.scandir(directory) as entries:
        for entry in entries:
            try:
                attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
                if visible_name(entry.name) and not attributes & 6 and entry.is_dir(follow_symlinks=False):
                    item = {"name": entry.name, "path": entry.path}
                    if counts:
                        try:
                            with os.scandir(entry.path) as children:
                                item["images_direct"] = sum(1 for child in children if visible_name(child.name)
                                    and child.is_file(follow_symlinks=False) and Path(child.name).suffix.lower().lstrip(".") in EXTENSIONS)
                        except OSError:
                            item["images_direct"] = None
                    result.append(item)
            except OSError:
                continue
    return sorted(result, key=lambda item: item["name"].lower())


@router.post("/pick")
def pick() -> dict:
    """Run Tk in an isolated child process, with a five-minute lifetime limit."""
    args = [sys.executable]
    if not getattr(sys, "frozen", False):
        args.extend(["-m", "photo_explorer"])
    args.append("--pick-folder")
    result = subprocess.run(args, capture_output=True, text=True,
                            encoding="utf-8", timeout=300, check=True,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    path = result.stdout.strip()
    if path:
        path = str(validate_directory(path))
    return {"path": path or None}


@router.get("/structure", response_class=PlainTextResponse)
def export_structure(path: str, files: bool = True, skip: str = "") -> str:
    """Return the complete folder tree as plain text."""
    return structure(path, files, skip)


class StructureBody(BaseModel):
    path: str
    files: bool = True
    skip: str | list[str] = ""
    dest_dir: str | None = None


@router.post("/structure/save")
def save_structure(body: StructureBody) -> dict:
    """Save with the original Directory Structure naming convention."""
    root = validate_directory(body.path)
    text = structure(str(root), body.files, body.skip)
    folder = validate_directory(body.dest_dir or body.path) / "Directory Structure"
    access_path(folder).mkdir(exist_ok=True)
    path = folder / f"{root.name or 'Root'} Structure.txt"
    access_path(path).write_text(text, encoding="utf-8", newline="\n")
    return {"path": str(path)}


class RevealBody(BaseModel):
    path: str


@router.post("/reveal")
def reveal_path(body: RevealBody) -> dict:
    """Reveal an existing absolute folder or exported structure file."""
    path = Path(body.path)
    if not path.is_absolute() or not access_path(path).exists():
        raise ValueError("Choose an existing absolute path")
    args = ["explorer.exe", str(path)] if path.is_dir() else ["explorer.exe", f"/select,{path}"]
    subprocess.run(args, timeout=15, check=False)
    return {"ok": True}
