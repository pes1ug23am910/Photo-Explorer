"""Durable operation history, per-file outcomes, and conservative undo."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from typing import Callable

from . import fileops
from .config import EXTENSIONS, access_path, canonical, inside
from .db import Database
from .transfers import transfer_file


class AppliedError(RuntimeError):
    """The filesystem action completed but index reconciliation failed."""


class Operations:
    """Serialize journaled mutations and retain identities for moves outside roots."""

    def __init__(self, db: Database, scanner):
        self.db = db
        self.scanner = scanner
        self.directory = db.path.parent / "operations"
        self.directory.mkdir(exist_ok=True)
        self.guard = threading.RLock()
        self.cancelled: set[int] = set()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="operations")
        self.self_change: Callable[[list[str]], None] = lambda paths: None
        self.db.execute("UPDATE operations SET status='failed',finished=? WHERE status='running'", (time.time(),))

    def get(self, op_id: int, offset: int = 0, limit: int = 200) -> dict:
        """Read one run and a bounded page of entries."""
        row = self.db.one("SELECT * FROM operations WHERE id=?", (op_id,))
        if row is None:
            raise FileNotFoundError("Operation not found")
        row["params"] = json.loads(row["params"])
        row["operation_id"] = row["id"]
        row["counts"] = {item["action"]: item["n"] for item in self.db.all(
            "SELECT action,COUNT(*) n FROM operation_entries WHERE op_id=? GROUP BY action", (op_id,))}
        row["entries"] = self.db.all("SELECT * FROM operation_entries WHERE op_id=? ORDER BY seq LIMIT ? OFFSET ?",
                                     (op_id, limit, offset))
        return row

    def manifest(self, op_id: int) -> None:
        """Atomically write the human-readable journal beside the index."""
        result = self.get(op_id, limit=2**31 - 1)
        path = self.directory / f"{op_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(.025 * (attempt + 1))

    def run(self, kind: str, label: str, root_id: int | None, params: dict,
            plan: list[dict], executor: Callable | None = None, *, undoable: bool = True,
            undoes: int | None = None, finalize: Callable | None = None,
            background: bool | None = None) -> dict:
        """Record a run before executing it; large plans return immediately."""
        op_id = self.db.execute(
            "INSERT INTO operations(kind,label,root_id,params,started,status,total,undoable,undoes) "
            "VALUES (?,?,?,?,?,'running',?,?,?)",
            (kind, label, root_id, json.dumps(params), time.time(), len(plan), int(undoable), undoes))
        self.manifest(op_id)
        args = (op_id, params, plan, executor or self.execute, finalize)
        asynchronous = len(plan) > 200 if background is None else background
        if asynchronous:
            self.pool.submit(self._execute, *args)
            return {"operation_id": op_id}
        self._execute(*args)
        return self.get(op_id)

    def _execute(self, op_id: int, params: dict, plan: list[dict], executor: Callable,
                 finalize: Callable | None) -> None:
        done = failed = 0
        fatal = None
        try:
            with self.guard, ExitStack() as locks:
                for root in self.db.all("SELECT id FROM roots ORDER BY id"):
                    locks.enter_context(self.scanner.root_lock(root["id"]))
                for seq, original in enumerate(plan):
                    if op_id in self.cancelled:
                        break
                    entry = dict(original)
                    try:
                        self._snapshot(op_id, seq, entry)
                        self.self_change([entry["src"], entry.get("dest") or entry["src"]])
                        entry.update(executor(entry))
                        done += 1
                    except Exception as exc:
                        if not isinstance(exc, AppliedError):
                            entry["action"] = "error"
                        entry["error"] = str(exc)
                        failed += 1
                    finally:
                        self.self_change([entry["src"], entry.get("dest") or entry["src"]])
                    self.db.execute(
                        "INSERT INTO operation_entries(op_id,seq,action,src,dest,image_id,size,mtime,error) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (op_id, seq, entry["action"], entry["src"], entry.get("dest"), entry.get("image_id"),
                         entry.get("size"), entry.get("mtime"), entry.get("error")))
                    if (seq + 1) % 25 == 0:
                        self.db.execute("UPDATE operations SET done=?,failed=? WHERE id=?", (done, failed, op_id))
                if finalize:
                    finalize(params)
                self.db.execute("UPDATE operations SET params=? WHERE id=?", (json.dumps(params), op_id))
        except Exception as exc:
            fatal = str(exc)
            params["error"] = fatal
        finally:
            status = "failed" if fatal else "cancelled" if op_id in self.cancelled else "done"
            self.db.execute("UPDATE operations SET done=?,failed=?,finished=?,status=?,params=? WHERE id=?",
                            (done, failed, time.time(), status, json.dumps(params), op_id))
            self.manifest(op_id)
            self.cancelled.discard(op_id)
            kind = self.db.one("SELECT kind FROM operations WHERE id=?", (op_id,))["kind"]
            if len(plan) > 1 or kind in {"flatten", "rule", "duplicates", "undo"}:
                for root in self.db.all("SELECT id FROM roots"):
                    root_path = self.db.one("SELECT path FROM roots WHERE id=?", (root["id"],))["path"]
                    affected = any(inside(entry["src"], root_path) or (entry.get("dest") and inside(entry["dest"], root_path)) for entry in plan)
                    if affected and not self.scanner.stopping.is_set():
                        self.scanner.start(root["id"])

    def _snapshot(self, op_id: int, seq: int, entry: dict) -> None:
        row = self.db.one("SELECT * FROM images WHERE path=?", (canonical(entry["src"]),))
        if row:
            entry["image_id"] = row["id"]
            self.db.execute("INSERT INTO operation_images(op_id,seq,image_id,body) VALUES (?,?,?,?)",
                            (op_id, seq, row["id"], json.dumps(row)))

    def execute(self, entry: dict) -> dict:
        """Apply a planned file action and immediately reconcile its indexed identity."""
        action = entry["action"]
        if action in {"skipped", "filtered"}:
            return {"action": "skipped", "error": entry.get("note")}
        src = Path(entry["src"])
        stat = access_path(src).stat()
        if entry.get("size") is not None and (stat.st_size, stat.st_mtime) != (entry["size"], entry["mtime"]):
            raise fileops.ConflictError("Source changed since planning; preview again")
        row = self.db.one("SELECT * FROM images WHERE path=?", (canonical(src),))
        if action == "trashed":
            fileops.send2trash(str(access_path(src)))
            if row:
                self.db.execute("DELETE FROM images WHERE id=?", (row["id"],))
            return {"size": stat.st_size, "mtime": stat.st_mtime}
        dest = Path(entry["dest"])
        fileops.require_unindexed_target(self.db, dest)
        transfer_file(src, dest, action in {"moved", "renamed", "restored"})
        try:
            self.reconcile(row, dest, action != "copied")
            self.db.execute("INSERT OR REPLACE INTO destinations(path,last_used) VALUES (?,?)", (str(dest.parent), time.time()))
        except Exception as exc:
            entry.update(size=stat.st_size, mtime=stat.st_mtime)
            raise AppliedError(f"File action completed; index update failed: {exc}") from exc
        return {"size": stat.st_size, "mtime": stat.st_mtime}

    def reconcile(self, row: dict | None, dest: Path, move: bool) -> dict | None:
        """Repoint moves, remove rows outside roots, or index newly arriving files."""
        if dest.suffix.lower().lstrip(".") not in EXTENSIONS:
            return None
        root = fileops.containing_root(self.db, dest)
        if row and move:
            if root:
                return fileops.repoint(self.db, row, dest, root["id"])
            self.db.execute("DELETE FROM images WHERE id=?", (row["id"],))
        elif root:
            return fileops.index_file(self.db, root["id"], dest)
        return None

    def undo(self, op_id: int) -> dict:
        """Reverse completed entries once, preserving conflicts as per-entry errors."""
        with self.guard:
            original = self.get(op_id, limit=2**31 - 1)
            if original["undone_by"]:
                return self.get(original["undone_by"])
            if original["status"] != "done" or not original["undoable"]:
                raise fileops.ConflictError("This operation cannot be undone; use the Recycle Bin for trashed files")
            plan = []
            for entry in reversed(original["entries"]):
                if entry["action"] not in {"moved", "renamed", "copied"}:
                    continue
                saved = self.db.one("SELECT body FROM operation_images WHERE op_id=? AND seq=?", (op_id, entry["seq"]))
                plan.append({**entry, "src": entry["dest"], "dest": entry["src"],
                             "snapshot": json.loads(saved["body"]) if saved else None,
                             "action": "deleted_copy" if entry["action"] == "copied" else "restored"})

            def restore(entry: dict) -> dict:
                src, dest = Path(entry["src"]), Path(entry["dest"])
                stat = access_path(src).stat()
                if (stat.st_size, stat.st_mtime) != (entry["size"], entry["mtime"]):
                    raise fileops.ConflictError("The operated file has changed; undo refused")
                current = self.db.one("SELECT * FROM images WHERE path=?", (canonical(src),))
                if entry["action"] == "deleted_copy":
                    fileops.send2trash(str(access_path(src)))
                    if current:
                        self.db.execute("DELETE FROM images WHERE id=?", (current["id"],))
                else:
                    if access_path(dest).exists():
                        raise fileops.ConflictError("Both source and destination exist; undo refused")
                    snapshot = entry["snapshot"]
                    target_row = self.db.one("SELECT * FROM images WHERE path=?", (canonical(dest),))
                    if target_row:
                        if not snapshot or target_row["id"] != snapshot["id"] or (current and current["id"] != target_row["id"]):
                            raise fileops.ConflictError("The original path belongs to another indexed image")
                        current = current or target_row
                    root = fileops.containing_root(self.db, dest)
                    if snapshot and root and not current and self.db.one("SELECT id FROM images WHERE id=?", (snapshot["id"],)):
                        raise fileops.ConflictError("Original image id is now in use")
                    access_path(dest.parent).mkdir(parents=True, exist_ok=True)
                    transfer_file(src, dest, True)
                    if current:
                        self.reconcile(current, dest, True)
                    elif snapshot and root:
                        snapshot["root_id"] = root["id"]
                        columns = tuple(snapshot)
                        self.db.execute(f"INSERT INTO images({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                                        [snapshot[column] for column in columns])
                        fileops.repoint(self.db, snapshot, dest, root["id"])
                    else:
                        self.reconcile(None, dest, True)
                return {}

            def folders(params: dict) -> None:
                for folder in original["params"].get("removed_dirs", []):
                    access_path(folder).mkdir(parents=True, exist_ok=True)

            result = self.run("undo", f"Undo {original['label']}", original["root_id"], {"original": op_id},
                              plan, restore, undoable=False, undoes=op_id, finalize=folders)
            self.db.execute("UPDATE operations SET undone_by=? WHERE id=?", (result["operation_id"], op_id))
            self.manifest(op_id)
            return result

    def cancel(self, op_id: int) -> dict:
        """Request cancellation at the next entry boundary."""
        result = self.get(op_id)
        if result["status"] == "running":
            self.cancelled.add(op_id)
        return {"operation_id": op_id, "status": result["status"]}

    def close(self) -> None:
        """Wait for owned workers before closing the index services."""
        for row in self.db.all("SELECT id FROM operations WHERE status='running'"):
            self.cancelled.add(row["id"])
        self.pool.shutdown(wait=True)
