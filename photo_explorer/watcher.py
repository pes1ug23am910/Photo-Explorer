"""Debounced filesystem watching with targeted index updates and safe fallback."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from . import fileops
from .config import EXTENSIONS, access_path, canonical, inside, load_settings, visible_name
from .explorer import refresh_counts

log = logging.getLogger(__name__)


class Handler(FileSystemEventHandler):
    """Forward only file-changing events; reads must never trigger indexing."""

    def __init__(self, owner: Watcher, root_id: int):
        self.owner, self.root_id = owner, root_id

    def on_any_event(self, event) -> None:
        if event.event_type in {"created", "modified", "deleted", "moved"}:
            self.owner.handle(self.root_id, event)


class Watcher:
    """One observer and a coalescing worker for all registered roots."""

    def __init__(self, db, scanner, policy):
        self.db, self.scanner, self.policy = db, scanner, policy
        self.home = db.path.parent
        self.guard = threading.RLock()
        self.config_lock = threading.RLock()
        self.pending: dict[int, dict] = {}
        self.self_changes: dict[str, float] = {}
        self.watches: dict[int, tuple] = {}
        self.observer = None
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.error: str | None = None

    def enabled(self) -> bool:
        return bool(load_settings(self.db).get("watchFolders", True))

    def mark_self(self, paths: list[str]) -> None:
        """Ignore delayed notifications for files currently written by the app."""
        with self.guard:
            now = time.monotonic()
            self.self_changes = {path: expiry for path, expiry in self.self_changes.items() if expiry > now}
            for path in paths:
                self.self_changes[canonical(path)] = now + 4

    def ignored(self, path: str, root: dict) -> bool:
        identity = canonical(path)
        if not inside(identity, root["path"]) or inside(identity, self.home):
            return True
        relative = Path(identity).relative_to(Path(canonical(root["path"])))
        if any(not visible_name(part) or part.startswith("~") or part.lower().endswith(".tmp") for part in relative.parts):
            return True
        # Do not follow a link or junction into a different filesystem scope.
        current = Path(root["path"])
        for part in relative.parts:
            current /= part
            if access_path(current).is_symlink() or access_path(current).is_junction():
                return True
        with self.guard:
            return self.self_changes.get(identity, 0) > time.monotonic()

    def handle(self, root_id: int, event) -> None:
        """Accept synthetic or native events and debounce them for 1.5 seconds."""
        if event.is_directory and event.event_type == "modified":
            return
        root = self.db.one("SELECT * FROM roots WHERE id=?", (root_id,))
        if not root or not self.enabled():
            return
        src = str(event.src_path)
        dest = str(getattr(event, "dest_path", "") or "")
        source_ok = not self.ignored(src, root)
        dest_ok = bool(dest) and not self.ignored(dest, root)
        if not source_ok and not dest_ok:
            return
        if not root["recursive"] and not event.is_directory:
            source_ok = source_ok and canonical(Path(src).parent) == canonical(root["path"])
            dest_ok = dest_ok and canonical(Path(dest).parent) == canonical(root["path"])
        with self.guard:
            job = self.pending.setdefault(root_id, {"paths": set(), "moves": [], "full": False, "at": 0})
            job["at"] = time.monotonic()
            job["full"] |= event.is_directory
            if source_ok:
                job["paths"].add(src)
            if dest_ok:
                job["paths"].add(dest)
            if event.event_type == "moved" and source_ok and dest_ok and not event.is_directory:
                job["moves"].append((src, dest))

    def apply(self, root_id: int) -> None:
        """Apply a coalesced set immediately, also usable by deterministic tests."""
        with self.guard:
            job = self.pending.pop(root_id, None)
        if not job:
            return
        if job["full"] or len(job["paths"]) > 200:
            self.scanner.start(root_id)
            return
        with self.scanner.root_lock(root_id):
            root = self.db.one("SELECT * FROM roots WHERE id=?", (root_id,))
            if not root:
                return
            moved_paths = set()
            for src, dest in job["moves"]:
                if self.ignored(src, root) or self.ignored(dest, root):
                    continue
                row = self.db.one("SELECT * FROM images WHERE path=?", (canonical(src),))
                if row and access_path(dest).is_file() and not access_path(src).exists() and Path(dest).suffix.lower().lstrip(".") in EXTENSIONS:
                    existing = self.db.one("SELECT id FROM images WHERE path=?", (canonical(dest),))
                    if not existing:
                        fileops.repoint(self.db, row, Path(dest), root_id)
                        moved_paths.update((src, dest))
            for value in sorted(job["paths"] - moved_paths):
                path = Path(value)
                if self.ignored(value, root):
                    continue
                if not access_path(path).is_file():
                    self.db.execute("DELETE FROM images WHERE root_id=? AND path=?", (root_id, canonical(path)))
                elif path.suffix.lower().lstrip(".") in EXTENSIONS:
                    fileops.index_file(self.db, root_id, path)
            refresh_counts(self.db, root, {Path(path).parent for path in job["paths"]})
            self.db.execute("UPDATE roots SET revision=revision+1 WHERE id=?", (root_id,))
        self.policy.schedule(root_id)

    def start(self) -> None:
        if self.thread:
            return
        self.configure()
        self.thread = threading.Thread(target=self._worker, name="folder-events", daemon=True)
        self.thread.start()

    def configure(self) -> None:
        """Reconcile schedules after settings, open, or forget changes."""
        with self.config_lock:
            if not self.enabled():
                for root_id in list(self.watches):
                    self.forget(root_id)
                return
            if self.observer is None:
                for implementation in (Observer, PollingObserver):
                    observer = implementation()
                    try:
                        observer.start()
                        self.observer = observer
                        self.error = None
                        log.info("Folder observer started: %s", type(observer).__name__)
                        break
                    except Exception:
                        observer.stop()
                        if observer.is_alive():
                            observer.join(timeout=5)
                        log.exception("Folder observer failed to start")
                        log.warning("Trying polling fallback if available")
                        self.error = "Folder watching unavailable; use Rescan"
            if self.observer is None:
                return
            roots = self.db.all("SELECT * FROM roots")
            for root in roots:
                previous = self.watches.get(root["id"])
                if previous and previous[1] == bool(root["recursive"]):
                    continue
                self.forget(root["id"])
                try:
                    watch = self.observer.schedule(Handler(self, root["id"]), root["path"], recursive=bool(root["recursive"]))
                    self.watches[root["id"]] = (watch, bool(root["recursive"]))
                except Exception:
                    log.exception("Cannot watch folder %s", root["path"])
                    self.error = "Some folders cannot be watched; use Rescan"

    def forget(self, root_id: int) -> None:
        with self.config_lock:
            previous = self.watches.pop(root_id, None)
            with self.guard:
                self.pending.pop(root_id, None)
            if previous and self.observer:
                try:
                    self.observer.unschedule(previous[0])
                except Exception:
                    log.exception("Could not remove folder watch")

    def _worker(self) -> None:
        while not self.stopping.wait(.25):
            with self.guard:
                ready = [root_id for root_id, job in self.pending.items() if time.monotonic() - job["at"] >= 1.5]
            for root_id in ready:
                try:
                    self.apply(root_id)
                except Exception:
                    log.exception("Folder event update failed")
                    self.error = "Folder update failed; use Rescan"

    def close(self) -> None:
        self.stopping.set()
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=10)
        if self.thread:
            self.thread.join(timeout=30)
