"""Deferred cache touches, protected LRU eviction, and paced thumbnail warming."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from .config import load_settings
from .db import Database

log = logging.getLogger(__name__)


class CachePolicy:
    """Own one maintenance worker; request cache hits never write to SQLite."""

    def __init__(self, db: Database, cache, scanner):
        self.db, self.cache, self.scanner = db, cache, scanner
        self.guard = threading.RLock()
        self.touched: dict[tuple, float] = {}
        self.protected: set[str] = set()
        self.pending: set[int] = set()
        self.last_request = time.time()
        self.last_eviction = None
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        cache.policy = self
        self.adopt_legacy()

    def adopt_legacy(self) -> None:
        """Account for derivative files retained from older cache registries."""
        for row in self.db.all("SELECT root_id,key FROM cache_keys WHERE kind='legacy'"):
            key = row["key"]
            for path in (self.cache.directory / key[:2]).glob(f"{key}_*"):
                if path.suffix not in {".jpg", ".png"}:
                    continue
                self.db.execute("INSERT OR IGNORE INTO cache_keys VALUES (?,?,?,?,?)",
                                (row["root_id"], key, path.name[len(key) + 1:], path.stat().st_size, path.stat().st_mtime))

    def touch(self, root_id: int, key: str, kind: str) -> None:
        """Record hits only in memory until the next maintenance flush."""
        with self.guard:
            self.touched[(root_id, key, kind)] = time.time()

    def flush(self) -> None:
        """Persist a batch of recent access times outside the request path."""
        with self.guard:
            touches, self.touched = self.touched, {}
        if touches:
            with self.db.connect() as connection:
                connection.executemany("UPDATE cache_keys SET last_used=? WHERE root_id=? AND key=? AND kind=?",
                                       [(stamp, *key) for key, stamp in touches.items()])

    def stats(self) -> dict:
        """Read registered derivative usage and the configured cap."""
        row = self.db.one("SELECT COALESCE(SUM(bytes),0) bytes,COUNT(*) files FROM cache_keys WHERE kind!='legacy'")
        return {**row, "cap_bytes": self.cap(), "last_eviction": self.last_eviction}

    def cap(self) -> int:
        try:
            return max(0, int(load_settings(self.db).get("cacheMaxMB", 2048))) * 1024**2
        except (ValueError, TypeError):
            return 2048 * 1024**2

    def path(self, key: str, kind: str) -> Path:
        return self.cache.directory / key[:2] / f"{key}_{kind}"

    def evict(self, now: float | None = None) -> None:
        """Evict oldest unprotected derivatives and remove unknown orphan files."""
        now = time.time() if now is None else now
        self.flush()
        total = self.stats()["bytes"]
        cap = self.cap()
        before = total
        removed = 0
        rows = self.db.all("SELECT * FROM cache_keys WHERE kind!='legacy' ORDER BY last_used,key,kind")
        known = {self.path(row["key"], row["kind"]) for row in rows}
        for row in rows:
            if total <= cap:
                break
            key = row["key"]
            with self.cache.locks[int(key[:4], 16) % len(self.cache.locks)], self.guard:
                last_used = max(row["last_used"] or 0, self.touched.get((row["root_id"], key, row["kind"]), 0))
                if last_used > now - 600 or key in self.protected:
                    continue
                self.path(key, row["kind"]).unlink(missing_ok=True)
                self.db.execute("DELETE FROM cache_keys WHERE root_id=? AND key=? AND kind=?", (row["root_id"], key, row["kind"]))
                total -= row["bytes"] or 0
                removed += 1
        for path in self.cache.directory.glob("*/*"):
            if path not in known and path.suffix in {".jpg", ".png"}:
                key = path.name.split("_", 1)[0]
                if len(key) != 40 or any(char not in "0123456789abcdef" for char in key):
                    continue
                with self.cache.locks[int(key[:4], 16) % len(self.cache.locks)]:
                    # A render may have appeared after the inventory above.
                    if not self.db.one("SELECT key FROM cache_keys WHERE key=? AND kind=?", (key, path.name[41:])):
                        path.unlink(missing_ok=True)
        self.last_eviction = now
        # Routine passes that remove nothing stay out of the INFO log, which would otherwise fill with them.
        if removed:
            log.info("Cache eviction removed %s derivatives: bytes %s -> %s (cap %s)", removed, before, total, cap)
        else:
            log.debug("Cache eviction pass: bytes=%s cap=%s, nothing to remove", total, cap)

    def clear(self, root_id: int | None = None) -> dict:
        """Clear derivative files; original images and annotations are untouched."""
        from contextlib import ExitStack
        with ExitStack() as stack:
            for lock in self.cache.locks:
                stack.enter_context(lock)
            rows = self.db.all("SELECT * FROM cache_keys" + (" WHERE root_id=?" if root_id is not None else ""),
                               (root_id,) if root_id is not None else ())
            for row in rows:
                for path in (self.cache.directory / row["key"][:2]).glob(f"{row['key']}_*"):
                    path.unlink(missing_ok=True)
            if root_id is None:
                for path in self.cache.directory.glob("*/*"):
                    if path.is_file():
                        path.unlink(missing_ok=True)
            self.db.execute("DELETE FROM cache_keys" + (" WHERE root_id=?" if root_id is not None else ""),
                            (root_id,) if root_id is not None else ())
        return self.stats()

    def protect(self, ids: list[int]) -> None:
        """Keep the viewer's current neighbourhood until it closes or moves."""
        keys = set()
        for image_id in ids[:31]:
            row = self.db.one("SELECT * FROM images WHERE id=?", (image_id,))
            if row:
                try:
                    keys.add(self.cache.key(row))
                except OSError:
                    pass
        with self.guard:
            self.protected = keys

    def schedule(self, root_id: int) -> None:
        """Queue a library after its metadata scan finishes."""
        with self.guard:
            self.pending.add(root_id)

    def warm_batch(self, rows: list[dict]) -> int:
        """Render at most 32 missing thumbs, pausing under interactive load."""
        done = 0
        if not load_settings(self.db).get("prewarm", True):
            return done
        for row in rows[:32]:
            if self.stopping.is_set() or time.time() - self.last_request < 1:
                break
            try:
                key = self.cache.key(row)
                if self.path(key, "256.jpg").exists():
                    done += 1
                    continue
                # Reserve a generous JPEG bound; never render past the cap.
                if self.stats()["bytes"] + 256 * 256 * 3 + 65536 > self.cap():
                    break
                self.cache.render(row, 256)
                done += 1
            except OSError:
                done += 1
        return done

    def start(self) -> None:
        if self.thread:
            return
        self.thread = threading.Thread(target=self._worker, name="cache-maintenance", daemon=True)
        self.thread.start()

    def _worker(self) -> None:
        flushed = evicted = time.time()
        jobs: dict[int, list[dict]] = {}
        progress: dict[int, int] = {}
        while not self.stopping.wait(.25):
            try:
                now = time.time()
                if now - flushed >= 60:
                    self.flush()
                    flushed = now
                with self.guard:
                    pending, self.pending = self.pending, set()
                if pending or now - evicted >= 3600:
                    self.evict()
                    evicted = now
                for root_id in pending:
                    rows = self.db.all("SELECT * FROM images WHERE root_id=? ORDER BY mtime DESC,id", (root_id,))
                    jobs[root_id] = rows
                    progress[root_id] = 0
                    self.scanner.update(root_id, prewarm_done=0, prewarm_total=len(rows))
                for root_id in list(jobs)[:1]:
                    done = self.warm_batch(jobs[root_id])
                    if done:
                        del jobs[root_id][:done]
                        progress[root_id] += done
                        self.scanner.update(root_id, prewarm_done=progress[root_id])
                        # A full eviction pass scans every cache row and file, so during pre-warm it runs
                        # only when the cap is exceeded or once a minute, not after every batch.
                        if time.time() - evicted >= 60 or self.stats()["bytes"] > self.cap():
                            self.evict()
                            evicted = time.time()
                    if not jobs[root_id] or (not done and time.time() - self.last_request >= 1):
                        del jobs[root_id]
            except Exception:
                log.exception("Cache maintenance failed; interactive rendering remains available")

    def close(self) -> None:
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=30)
        self.flush()
