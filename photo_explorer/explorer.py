"""Cached direct folder counts and the plain-text structure exporter."""

from __future__ import annotations

import os
from pathlib import Path

from .config import EXTENSIONS, access_path, canonical, inside, validate_directory, visible_name
from .db import Database


def refresh_counts(db: Database, root: dict, directories: set[Path] | None = None) -> None:
    """Cache a scandir inventory independently of image metadata."""
    pending = list(directories) if directories is not None else [Path(root["path"])]
    rows = []
    visited = set()
    while pending:
        path = pending.pop()
        images = files = folders = 0
        try:
            with os.scandir(access_path(path)) as entries:
                for entry in entries:
                    if not visible_name(entry.name) or entry.is_symlink() or entry.is_junction():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        folders += 1
                        if root["recursive"] and directories is None:
                            pending.append(path / entry.name)
                    elif entry.is_file(follow_symlinks=False):
                        if Path(entry.name).suffix.lower().lstrip(".") in EXTENSIONS:
                            images += 1
                        else:
                            files += 1
        except OSError:
            continue
        visited.add(canonical(path))
        rows.append((root["id"], canonical(path), images, files, folders))
    with db.connect() as connection:
        connection.executemany("INSERT OR REPLACE INTO folder_counts VALUES (?,?,?,?,?)", rows)
        old = connection.execute("SELECT path FROM folder_counts WHERE root_id=?", (root["id"],)).fetchall() if directories is None else []
        for row in old:
            if row[0] not in visited and not access_path(row[0]).exists():
                connection.execute("DELETE FROM folder_counts WHERE root_id=? AND path=?", (root["id"], row[0]))


def folder_tree(db: Database, root: dict) -> list[dict]:
    """Combine the filesystem inventory with recursive indexed image counts."""
    root_path = root["path"]
    cached = db.all("SELECT * FROM folder_counts WHERE root_id=?", (root["id"],))
    if not cached:
        refresh_counts(db, root)
        cached = db.all("SELECT * FROM folder_counts WHERE root_id=?", (root["id"],))
    nodes = {}

    def node(path: str) -> dict:
        if path not in nodes:
            nodes[path] = {"path": path, "name": Path(path).name or path, "count": 0,
                           "images_direct": 0, "files_direct": 0, "subfolders": 0, "children": []}
            if path != root_path:
                node(str(Path(path).parent))["children"].append(nodes[path])
        return nodes[path]

    node(root_path)
    for row in cached:
        node(row["path"]).update({key: row[key] for key in ("images_direct", "files_direct", "subfolders")})
    for row in db.all("SELECT dir,COUNT(*) count FROM images WHERE root_id=? GROUP BY dir", (root["id"],)):
        path = row["dir"]
        while inside(path, root_path):
            node(path)["count"] += row["count"]
            if path == root_path:
                break
            path = str(Path(path).parent)
    for item in nodes.values():
        item["children"].sort(key=lambda child: child["name"].casefold())
    return [nodes[root_path]]


def structure(path: str, files: bool = True, skip: str | list[str] = "") -> str:
    """Export sorted files before directories with the original tree connectors."""
    root = validate_directory(path)
    parts = skip.split(",") if isinstance(skip, str) else skip
    names = {"directory structure"}
    paths = []
    for value in parts:
        value = value.strip()
        if os.path.isabs(value):
            paths.append(value)
        elif value:
            names.add(value.casefold())
    display = str(root)
    lines = [f"Structure for: {display}", "=" * (len(display) + 16), "", f"{root.name or display}/"]

    def walk(folder: Path, indent: str) -> None:
        try:
            with os.scandir(access_path(folder)) as scanned:
                entries = list(scanned)
            entries.sort(key=lambda entry: (entry.is_dir(follow_symlinks=False), entry.name))
        except OSError as exc:
            error = "Permission Denied" if isinstance(exc, PermissionError) else "Directory Not Found" if isinstance(exc, FileNotFoundError) else f"Unreadable directory: {exc.strerror or str(exc)}"
            lines.append(f"{indent}|-- [Error: {error}]")
            return
        kept = []
        for entry in entries:
            directory = entry.is_dir(follow_symlinks=False)
            current = folder / entry.name
            if entry.is_symlink() or entry.is_junction():
                continue
            if directory and (entry.name.casefold() in names or any(inside(current, value) for value in paths)):
                continue
            if directory or files:
                kept.append((entry, directory, current))
        for index, (entry, directory, current) in enumerate(kept):
            last = index == len(kept) - 1
            lines.append(f"{indent}{'`-- ' if last else '|-- '}{entry.name}{'/' if directory else ''}")
            if directory:
                walk(current, indent + ("    " if last else "|   "))

    walk(root, "")
    return "\n".join(lines) + "\n"
