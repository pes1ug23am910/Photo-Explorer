"""Shared collision planning and exclusive file transfers."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import access_path, canonical
from .fileops import ConflictError


def identical(first: Path, second: Path) -> bool:
    """Compare bytes in bounded chunks, avoiding false duplicates from equal sizes."""
    first, second = access_path(first), access_path(second)
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as left, second.open("rb") as right:
        while True:
            chunk = left.read(1024 * 1024)
            if chunk != right.read(1024 * 1024):
                return False
            if not chunk:
                return True


class Names:
    """Reserve case-insensitive names, including planned destinations."""

    def __init__(self, dest: Path):
        self.dest = dest
        self.claims = {entry.name.casefold(): dest / entry.name for entry in access_path(dest).iterdir()} if access_path(dest).exists() else {}

    def choose(self, src: Path, name: str, collision: str = "rename") -> tuple[Path, str, str]:
        """Return destination, take/skip decision, and a readable collision reason."""
        stem, ext = os.path.splitext(name)
        candidate = name
        number = 0
        while candidate.casefold() in self.claims:
            previous = self.claims[candidate.casefold()]
            if collision == "skip":
                return self.dest / candidate, "skipped", "name already present"
            if collision == "skip_identical" and access_path(previous).is_file() and identical(src, previous):
                return self.dest / candidate, "skipped", "identical, skipped"
            number += 1
            candidate = f"{stem} ({number}){ext}"
        # The source supplies bytes for duplicate comparison before execution.
        self.claims[candidate.casefold()] = src
        return self.dest / candidate, "take", f"renamed ({number})" if number else ""


def transfer_file(src: Path, dest: Path, move: bool) -> None:
    """Reserve the destination exclusively; never replace another user's file."""
    if canonical(src) == canonical(dest):
        raise ConflictError("Source and destination are the same file")
    source, target = access_path(src), access_path(dest)
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        if move:
            try:
                os.replace(source, target)
            except OSError:
                shutil.copy2(source, target)
                source.unlink()
        else:
            shutil.copy2(source, target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
