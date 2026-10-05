"""Two-phase indexing with bounded concurrent metadata reads."""

from __future__ import annotations

import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterator

from .config import EXTENSIONS, access_path, canonical, visible_name
from .db import Database
from .metadata import natural_key, read_metadata

META_COLUMNS = (
    "width", "height", "pixels", "aspect", "orient", "taken", "camera", "lens", "iso", "fnumber",
    "exposure", "focal", "gps_lat", "gps_lon", "exif_orientation", "format", "animated", "frames",
)


def walk_images(path: Path, recursive: bool, skipped: list[str] | None = None) -> Iterator[tuple[Path, os.stat_result]]:
    """Walk visible files without following symlinks or junctions.

    A subdirectory that cannot be listed (permissions, a dead reparse point) is
    recorded in ``skipped`` and the walk continues; only an unreadable root raises.
    """
    stack = [path]
    while stack:
        current = stack.pop()
        extended = os.name == "nt" and len(str(current)) > 240
        try:
            entries = os.scandir(access_path(current))
        except OSError:
            if current == path:
                raise
            if skipped is not None:
                skipped.append(str(current))
            continue
        with entries:
            for entry in entries:
                if not visible_name(entry.name) or entry.is_symlink() or entry.is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        stack.append(current / entry.name if extended else Path(entry.path))
                elif os.path.splitext(entry.name)[1].lower().lstrip(".") in EXTENSIONS:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            yield current / entry.name if extended else Path(entry.path), entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue


def basic_record(root_id: int, path: Path, stat: os.stat_result | None = None, identity: str | None = None) -> dict:
    """Prepare cheap index fields without opening an image."""
    stat = stat or access_path(path).stat()
    display_path = os.fspath(path) if identity is not None else os.path.abspath(path)
    identity = identity or canonical(display_path)
    name = path.name
    return {
        "root_id": root_id, "path": identity, "display_path": display_path,
        "dir": os.path.dirname(identity), "name": name, "name_key": natural_key(name),
        "ext": os.path.splitext(name)[1].lower().lstrip("."), "size": stat.st_size,
        "mtime": stat.st_mtime, "ctime": getattr(stat, "st_birthtime", stat.st_ctime),
        "sort_date": stat.st_mtime, "rnd": random.random(), "indexed_at": time.time(),
    }


def upsert_records(db: Database, records: list[dict]) -> None:
    """Upsert file identities while preserving ratings and stable shuffle keys."""
    if not records:
        return
    columns = tuple(records[0])
    updates = [f"{column}=excluded.{column}" for column in columns if column not in {"rnd", "root_id"}]
    updates.extend(f"{column}=NULL" for column in META_COLUMNS if column != "animated")
    updates.extend(["animated=0", "meta_state=0", "meta_error=NULL"])
    sql = (
        f"INSERT INTO images ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
        f"ON CONFLICT(path) DO UPDATE SET {','.join(updates)} "
        "WHERE images.size != excluded.size OR images.mtime != excluded.mtime"
    )
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        reserved = connection.execute("SELECT MAX(image_id) FROM operation_images").fetchone()[0]
        if reserved is not None:
            highest = connection.execute("SELECT MAX(id) FROM images").fetchone()[0] or 0
            start = max(highest, reserved) + 1
            insert_columns = ("id", *columns)
            sql = sql.replace(f"({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                              f"({','.join(insert_columns)}) VALUES ({','.join('?' for _ in insert_columns)})", 1)
            connection.executemany(sql, [(start + index, *(record[column] for column in columns)) for index, record in enumerate(records)])
        else:
            connection.executemany(sql, [tuple(record[column] for column in columns) for record in records])


def metadata_result(row: dict) -> tuple[dict, dict]:
    """Convert an individual decode failure into an indexed error record."""
    try:
        metadata = read_metadata(row["display_path"])
        metadata.pop("exif", None)
        metadata["sort_date"] = metadata.get("taken") or row["mtime"]
    except Exception as exc:
        metadata = {"meta_state": 2, "meta_error": str(exc)[:1500], "sort_date": row["mtime"]}
    return row, metadata


def write_metadata(db: Database, results: list[tuple[dict, dict]]) -> None:
    """Batch results, ignoring reads that no longer match the indexed file."""
    with db.connect() as connection:
        for row, metadata in results:
            columns = tuple(metadata)
            connection.execute(
                f"UPDATE images SET {','.join(f'{column}=?' for column in columns)} "
                "WHERE id=? AND size=? AND mtime=?",
                [*(metadata[column] for column in columns), row["id"], row["size"], row["mtime"]],
            )


class Scanner:
    """Own background scan jobs; at most one scan runs for each root."""

    def __init__(self, db: Database):
        self.db = db
        self.jobs: dict[int, dict] = {}
        self.locks: dict[int, threading.RLock] = {}
        self.guard = threading.RLock()
        self.stopping = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scan")
        self.on_complete = lambda root_id: None

    def root_lock(self, root_id: int) -> threading.RLock:
        """Share a root mutation lock with file actions and forgetting roots."""
        with self.guard:
            return self.locks.setdefault(root_id, threading.RLock())

    def status(self, root_id: int) -> dict:
        """Return a consistent copy suitable for JSON responses."""
        with self.guard:
            return dict(self.jobs.get(root_id, {
                "running": False, "phase": "idle", "seen": 0, "indexed": 0, "removed": 0, "skipped": 0,
                "meta_done": 0, "meta_total": 0, "started": None, "finished": None, "error": None,
            }))

    def update(self, root_id: int, **fields) -> None:
        """Publish job progress."""
        with self.guard:
            self.jobs.setdefault(root_id, self.status(root_id)).update(fields)

    def start(self, root_id: int) -> dict:
        """Start a scan or return the job already running for this root."""
        with self.guard:
            if self.jobs.get(root_id, {}).get("running"):
                return self.status(root_id)
            if self.stopping.is_set():
                raise ValueError("Scanner is shutting down")
            auxiliary = {key: value for key, value in self.jobs.get(root_id, {}).items() if key.startswith(("dup_", "prewarm_"))}
            self.jobs[root_id] = {
                **auxiliary,
                "running": True, "phase": "files", "seen": 0, "indexed": 0, "removed": 0, "skipped": 0,
                "meta_done": 0, "meta_total": 0, "started": time.time(), "finished": None, "error": None,
            }
            self.executor.submit(self._run, root_id)
            return self.status(root_id)

    def _run(self, root_id: int) -> None:
        try:
            with self.root_lock(root_id):
                root = self.db.one("SELECT * FROM roots WHERE id=?", (root_id,))
                if root is None:
                    raise ValueError("Root was forgotten")
                self.phase_one(root)
                if not self.stopping.is_set():
                    self.phase_two(root_id)
                    from .explorer import refresh_counts
                    refresh_counts(self.db, root)
                    self.db.execute("UPDATE roots SET last_scan=?,revision=revision+1 WHERE id=?", (time.time(), root_id))
                    self.on_complete(root_id)
            self.update(root_id, phase="done" if not self.stopping.is_set() else "cancelled")
        except Exception as exc:
            self.update(root_id, error=str(exc), phase="failed")
        finally:
            self.update(root_id, running=False, finished=time.time())

    def phase_one(self, root: dict) -> None:
        """Index stat-only batches, then prune missing files after a complete walk."""
        root_id = root["id"]
        old = {row["path"]: row for row in self.db.all("SELECT path,size,mtime FROM images WHERE root_id=?", (root_id,))}
        seen: set[str] = set()
        batch: list[dict] = []
        batch_size = 500
        changed = 0
        skipped: list[str] = []
        for path, stat in walk_images(Path(root["path"]).absolute(), bool(root["recursive"]), skipped):
            if self.stopping.is_set():
                return
            identity = os.path.normcase(os.fspath(path))
            seen.add(identity)
            previous = old.get(identity)
            if previous is None or previous["size"] != stat.st_size or previous["mtime"] != stat.st_mtime:
                batch.append(basic_record(root_id, path, stat, identity))
                changed += 1
            if len(batch) >= batch_size:
                upsert_records(self.db, batch)
                batch.clear()
                batch_size = 2000
            if len(seen) % 500 == 0:
                self.update(root_id, seen=len(seen), indexed=changed)
        upsert_records(self.db, batch)
        # Files under a directory that could not be listed may still exist, so they are never pruned.
        hidden_prefixes = tuple(canonical(directory) + os.sep for directory in skipped)
        missing = [path for path in old.keys() - seen if not path.startswith(hidden_prefixes)]
        if missing:
            with self.db.connect() as connection:
                connection.executemany("DELETE FROM images WHERE root_id=? AND path=?", [(root_id, path) for path in missing])
        self.update(root_id, seen=len(seen), indexed=changed, removed=len(missing), skipped=len(skipped))

    def phase_two(self, root_id: int) -> None:
        """Resolve metadata in bounded work batches, newest files first."""
        rows = self.db.all("SELECT * FROM images WHERE root_id=? AND meta_state=0 ORDER BY mtime DESC,id", (root_id,))
        self.update(root_id, phase="metadata", meta_total=len(rows))
        workers = min(8, os.cpu_count() or 1)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="metadata") as pool:
            for start in range(0, len(rows), 64):
                if self.stopping.is_set():
                    return
                results = list(pool.map(metadata_result, rows[start:start + 64]))
                write_metadata(self.db, results)
                self.update(root_id, meta_done=start + len(results))

    def close(self) -> None:
        """Stop indexing at the next batch boundary and join owned threads."""
        self.stopping.set()
        self.executor.shutdown(wait=True, cancel_futures=False)
