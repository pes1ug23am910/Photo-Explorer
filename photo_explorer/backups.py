"""Daily consistent SQLite backups with bounded retention."""

import logging
import sqlite3
import threading
from datetime import datetime

from .db import Database


class Backups:
    def __init__(self, db: Database):
        self.db = db
        self.directory = db.path.parent / "backups"
        self.directory.mkdir(exist_ok=True)
        self.last_backup: str | None = None
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def backup(self) -> None:
        """Use SQLite's online backup API and keep the newest seven dates."""
        destination = self.directory / f"index-{datetime.now():%Y%m%d}.db"
        if not destination.exists():
            temporary = destination.with_suffix(".tmp")
            try:
                with self.db.connect() as source:
                    target = sqlite3.connect(temporary)
                    try:
                        source.backup(target)
                    finally:
                        target.close()
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        self.last_backup = str(destination)
        for path in sorted(self.directory.glob("index-????????.db"), reverse=True)[7:]:
            path.unlink()

    def start(self) -> None:
        try:
            self.backup()
        except OSError:
            logging.getLogger(__name__).exception("Index backup failed")
        self.thread = threading.Thread(target=self._worker, name="index-backup", daemon=True)
        self.thread.start()

    def _worker(self) -> None:
        while not self.stop.wait(60):
            try:
                self.backup()
            except (OSError, sqlite3.Error):
                logging.getLogger(__name__).exception("Daily index backup failed")

    def close(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=10)
