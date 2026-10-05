"""Exact duplicate detection with fingerprint reuse and journaled resolution."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import access_path, canonical, validate_directory
from .fileops import ConflictError
from .transfers import Names

log = logging.getLogger(__name__)


def sha256(path: Path) -> str:
    """Hash file content in bounded one-MiB chunks."""
    digest = hashlib.sha256()
    with access_path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class Duplicates:
    """Keep one scan per root active and store inspectable duplicate groups."""

    def __init__(self, db, scanner, operations):
        self.db, self.scanner, self.operations = db, scanner, operations
        self.guard = threading.RLock()
        self.active: dict[int, int] = {}
        self.stopping = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="duplicates")
        self.db.execute("UPDATE dup_runs SET status='failed',error='Interrupted scan',finished=? WHERE status='running'", (time.time(),))

    def start(self, root_id: int, mode: str = "exact", threshold: int = 6) -> dict:
        """Schedule exact hashing; near mode is deliberately not advertised yet."""
        if mode != "exact":
            raise ValueError("Only exact duplicate detection is available in this version")
        with self.guard:
            if root_id in self.active:
                return {"run_id": self.active[root_id]}
            run_id = self.db.execute("INSERT INTO dup_runs(root_id,mode,threshold,started) VALUES (?,?,?,?)",
                                     (root_id, mode, threshold, time.time()))
            self.active[root_id] = run_id
            self.scanner.update(root_id, dup_done=0, dup_total=0, dup_running=True, dup_run_id=run_id, dup_error=None)
            self.pool.submit(self._scan, root_id, run_id)
            return {"run_id": run_id}

    def _scan(self, root_id: int, run_id: int) -> None:
        log.info("Duplicate scan start root=%s run=%s", root_id, run_id)
        error = None
        count = 0
        try:
            with self.scanner.root_lock(root_id):
                sizes = defaultdict(list)
                for row in self.db.all("SELECT * FROM images WHERE root_id=? ORDER BY id", (root_id,)):
                    sizes[row["size"]].append(row)
                candidates = [row for rows in sizes.values() if len(rows) > 1 for row in rows]
                self.scanner.update(root_id, dup_total=len(candidates))
                digests = defaultdict(list)
                failures = []
                for index, row in enumerate(candidates):
                    if self.stopping.is_set():
                        raise ValueError("Duplicate scan cancelled during shutdown")
                    try:
                        path = access_path(row["display_path"])
                        before = path.stat()
                        if (before.st_size, before.st_mtime) != (row["size"], row["mtime"]):
                            raise ConflictError("Image changed; rescan the library")
                        saved = self.db.one("SELECT * FROM hashes WHERE image_id=?", (row["id"],))
                        if saved and (saved["size"], saved["mtime"]) == (row["size"], row["mtime"]) and saved["sha256"]:
                            digest = saved["sha256"]
                        else:
                            digest = sha256(path)
                            after = path.stat()
                            if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                                raise ConflictError("Image changed while hashing; rescan the library")
                            self.db.execute("INSERT OR REPLACE INTO hashes(image_id,size,mtime,sha256,computed) VALUES (?,?,?,?,?)",
                                            (row["id"], row["size"], row["mtime"], digest, time.time()))
                        digests[digest].append(row["id"])
                    except OSError as exc:
                        failures.append(f"{row['name']}: {exc}")
                    if (index + 1) % 25 == 0 or index + 1 == len(candidates):
                        self.scanner.update(root_id, dup_done=index + 1)
                with self.db.connect() as connection:
                    for members in digests.values():
                        if len(members) < 2:
                            continue
                        group_id = connection.execute("INSERT INTO dup_groups(run_id,kind) VALUES (?,'exact')", (run_id,)).lastrowid
                        connection.executemany("INSERT INTO dup_members(group_id,image_id,distance) VALUES (?,?,0)",
                                               [(group_id, image_id) for image_id in members])
                        count += 1
                if failures:
                    error = f"{len(failures)} unreadable files; " + "; ".join(failures[:5])
        except Exception as exc:
            error = str(exc)
        finally:
            self.db.execute("UPDATE dup_runs SET groups=?,finished=?,status=?,error=? WHERE id=?",
                            (count, time.time(), "failed" if error else "done", error, run_id))
            self.scanner.update(root_id, dup_running=False, dup_error=error)
            log.info("Duplicate scan finish root=%s run=%s groups=%s status=%s",
                     root_id, run_id, count, "failed" if error else "done")
            with self.guard:
                self.active.pop(root_id, None)

    def groups(self, root_id: int, run_id: int | None, offset: int, limit: int) -> dict:
        """Read one page of groups and compact member records."""
        run = self.db.one("SELECT * FROM dup_runs WHERE root_id=?" + (" AND id=?" if run_id else "") + " ORDER BY id DESC LIMIT 1",
                          (root_id, run_id) if run_id else (root_id,))
        if not run:
            return {"run": None, "total": 0, "groups": []}
        groups = self.db.all("SELECT * FROM dup_groups WHERE run_id=? ORDER BY id LIMIT ? OFFSET ?", (run["id"], limit, offset))
        for group in groups:
            group["members"] = self.db.all(
                "SELECT i.id,i.name,i.ext,i.size,i.width,i.height,i.mtime,i.ctime,i.dir,i.path,i.display_path,m.distance "
                "FROM dup_members m JOIN images i ON i.id=m.image_id WHERE m.group_id=? ORDER BY i.path", (group["id"],))
        return {"run": run, "total": run["groups"], "groups": groups}

    def plan(self, group_ids: list[int], keep: str | dict[str, int], action: str, dest: str | None) -> tuple[int, list[dict]]:
        """Revalidate members and keep one per group before planning resolution."""
        entries = []
        runs = set()
        root_id = None
        names = Names(validate_directory(dest or "")) if action == "move" else None
        for group_id in dict.fromkeys(group_ids):
            group = self.db.one("SELECT g.*,r.root_id,r.status FROM dup_groups g JOIN dup_runs r ON r.id=g.run_id WHERE g.id=?", (group_id,))
            if not group or group["status"] != "done":
                raise ConflictError("Choose completed duplicate groups")
            runs.add(group["run_id"])
            if len(runs) > 1:
                raise ValueError("Resolve groups from one scan at a time")
            root_id = group["root_id"]
            rows = self.db.all("SELECT i.*,h.sha256,h.size hash_size,h.mtime hash_mtime FROM dup_members m "
                               "JOIN images i ON i.id=m.image_id JOIN hashes h ON h.image_id=i.id WHERE m.group_id=?", (group_id,))
            if len(rows) < 2:
                continue
            digests = set()
            for row in rows:
                stat = access_path(row["display_path"]).stat()
                if (stat.st_size, stat.st_mtime) != (row["hash_size"], row["hash_mtime"]):
                    raise ConflictError("Duplicate files changed; rescan before resolving")
                digest = sha256(Path(row["display_path"]))
                if digest != row["sha256"]:
                    raise ConflictError("Duplicate bytes changed; rescan before resolving")
                digests.add(digest)
            if len(digests) != 1:
                raise ConflictError("These files are no longer exact duplicates")
            if isinstance(keep, dict):
                keeper = keep.get(str(group_id))
                if keeper not in {row["id"] for row in rows}:
                    raise ValueError("Choose a keeper belonging to each group")
            else:
                strategies = {"largest": lambda row: (-row["size"], row["path"]),
                              "newest": lambda row: (-row["mtime"], row["path"]),
                              "oldest": lambda row: (row["mtime"], row["path"]),
                              "smallest_path": lambda row: row["path"]}
                if keep not in strategies:
                    raise ValueError("Unknown keeper strategy")
                keeper = min(rows, key=strategies[keep])["id"]
            for row in rows:
                if row["id"] == keeper:
                    continue
                src = Path(row["display_path"])
                target = None
                note = ""
                if names:
                    if canonical(src.parent) == canonical(names.dest):
                        raise ValueError("A duplicate already lives in the destination")
                    target, _, note = names.choose(src, row["name"])
                entries.append({"src": str(src), "dest": str(target) if target else None,
                                "action": "moved" if action == "move" else "trashed", "image_id": row["id"],
                                "size": row["size"], "mtime": row["mtime"], "note": note})
        return root_id, entries

    def close(self) -> None:
        self.stopping.set()
        self.pool.shutdown(wait=True)
