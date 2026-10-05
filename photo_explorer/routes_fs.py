"""Local directory browsing and a thread-safe native folder picker."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys

from fastapi import APIRouter

from .config import validate_directory, visible_name

router = APIRouter(prefix="/api/fs")
PICK_SCRIPT = """
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
try:
    print(filedialog.askdirectory(parent=root, title='Open a photo folder', mustexist=True) or '')
finally:
    root.destroy()
"""


@router.get("/browse")
def browse(path: str | None = None) -> list[dict]:
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
                    result.append({"name": entry.name, "path": entry.path})
            except OSError:
                continue
    return sorted(result, key=lambda item: item["name"].lower())


@router.post("/pick")
def pick() -> dict:
    """Run Tk in an isolated child process, with a five-minute lifetime limit."""
    result = subprocess.run([sys.executable, "-c", PICK_SCRIPT], capture_output=True, text=True,
                            encoding="utf-8", timeout=300, check=True,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    path = result.stdout.strip()
    if path:
        path = str(validate_directory(path))
    return {"path": path or None}
