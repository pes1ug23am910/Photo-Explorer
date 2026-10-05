"""Deterministic flatten planning and reversible empty-folder cleanup."""

from __future__ import annotations

import fnmatch
import os
import re
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import access_path, canonical, inside, validate_directory
from .transfers import Names


class FlattenOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str
    dest: str | None = None
    mode: Literal["move", "copy"] = "move"
    prefix: bool = False
    sep: str = "_"
    strip_tags: bool = False
    max_prefix: int = Field(0, ge=0, le=240)
    ext: str = ""
    exclude: str = ""
    skip_duplicates: bool = False
    remove_empty_dirs: bool = True
    expected_total: int | None = Field(None, ge=0)

    @field_validator("sep")
    @classmethod
    def separator(cls, value: str) -> str:
        if len(value) > 20 or re.search(r'[<>:"/\\|?*\x00-\x1f]', value):
            raise ValueError("Separator must contain only filename-safe characters")
        return value


def strip_tags(value: str) -> str:
    """Peel nested bracket groups, retaining an all-tag original component."""
    previous = None
    cleaned = value
    while cleaned != previous:
        previous = cleaned
        cleaned = re.sub(r"\[[^\[\]]*\]|\{[^{}]*\}|\([^()]*\)", "", cleaned)
    return " ".join(cleaned.split()) or value


def prefix_name(parts: tuple[str, ...], name: str, options: FlattenOptions) -> str:
    """Construct the optional cleaned, capped folder-chain prefix."""
    if not options.prefix or not parts:
        return name
    components = [strip_tags(part) if options.strip_tags else part for part in parts]
    prefix = options.sep.join(components)
    if options.max_prefix:
        prefix = prefix[:options.max_prefix].rstrip(" .") or components[-1][:1].rstrip(" .") or "x"
    return prefix + options.sep + name


def plan(options: FlattenOptions) -> dict:
    """Plan from live files; no directories or files are changed."""
    target = validate_directory(options.target)
    dest = validate_directory(options.dest or options.target)
    if canonical(dest) != canonical(target) and inside(dest.resolve(), target.resolve()):
        raise ValueError("Destination cannot be inside the target subtree")
    extensions = {value.lower().lstrip(".") for value in re.split(r"[,;\s]+", options.ext) if value}
    excludes = [value.strip().casefold() for value in re.split(r"[,;]", options.exclude) if value.strip()]
    names = Names(dest)
    entries = []
    summary = {"take": 0, "skip": 0, "filtered": 0, "conflicts_renamed": 0, "bytes": 0}

    def fail(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(access_path(target), onerror=fail, followlinks=False):
        # Build plain paths even when the walk uses Windows extended paths.
        relative = Path(directory).relative_to(access_path(target))
        folder = target / relative
        dirs[:] = sorted(name for name in dirs if not (access_path(folder / name).is_symlink() or access_path(folder / name).is_junction())
                         and canonical(folder / name) != canonical(dest))
        # Files already at the top level of the target stay where they are, as in the original
        # flatten scripts; only subfolder contents are gathered, even for an external destination.
        if canonical(folder) in {canonical(dest), canonical(target)}:
            continue
        for name in sorted(files):
            src = folder / name
            if access_path(src).is_symlink():
                continue
            stat = access_path(src).stat()
            filtered = (extensions and src.suffix.lower().lstrip(".") not in extensions) or any(fnmatch.fnmatchcase(name.casefold(), pattern) for pattern in excludes)
            if filtered:
                entries.append({"src": str(src), "dest": None, "action": "filtered", "note": "extension or exclude filter"})
                summary["filtered"] += 1
                continue
            new_name = prefix_name(relative.parts, name, options)
            if len(new_name) > 255:
                raise ValueError("A generated filename exceeds 255 characters; reduce the prefix limit")
            destination, decision, note = names.choose(src, new_name, "skip_identical" if options.skip_duplicates else "rename")
            action = "skipped" if decision == "skipped" else "moved" if options.mode == "move" else "copied"
            entries.append({"src": str(src), "dest": str(destination), "action": action, "note": note,
                            "size": stat.st_size, "mtime": stat.st_mtime})
            summary["skip" if decision == "skipped" else "take"] += 1
            if decision != "skipped":
                summary["bytes"] += stat.st_size
                summary["conflicts_renamed"] += int(bool(note))
    return {"summary": summary, "entries": entries, "total": len(entries)}


def remove_empty(params: dict) -> None:
    """Remove only empty descendants, remembering them for undo."""
    params["removed_dirs"] = []
    if params["mode"] != "move" or not params["remove_empty_dirs"]:
        return
    target = Path(params["target"])
    for directory, _, _ in os.walk(access_path(target), topdown=False, followlinks=False):
        folder = target / Path(directory).relative_to(access_path(target))
        if canonical(folder) in {canonical(target), canonical(params.get("dest") or target)}:
            continue
        # A folder that was just emptied can still be held open briefly by a watcher or
        # thumbnail reader on Windows, so an empty-looking folder gets a few short retries.
        for attempt in range(5):
            try:
                access_path(folder).rmdir()
                params["removed_dirs"].append(str(folder))
                break
            except OSError:
                if attempt == 4 or any(access_path(folder).iterdir()):
                    break
                time.sleep(.05)
