"""Measure stat-only indexing and time to the first committed page."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from photo_explorer.config import canonical
from photo_explorer.db import Database
from photo_explorer.scanner import Scanner


def main() -> None:
    """Create disposable candidates under .work and measure phase one only."""
    workspace = Path(__file__).resolve().parents[1]
    scratch = workspace / ".work"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scan-benchmark-", dir=scratch) as temporary:
        folder = Path(temporary) / "images"
        home = Path(temporary) / "home"
        folder.mkdir()
        home.mkdir()
        for index in range(20000):
            (folder / f"img{index}.jpg").touch()
        os.environ["PHOTO_EXPLORER_HOME"] = str(home)
        db = Database(home)
        now = time.time()
        root_id = db.execute("INSERT INTO roots(path,recursive,created,opened) VALUES (?,1,?,?)", (canonical(folder), now, now))
        scanner = Scanner(db)
        scanner.jobs[root_id] = scanner.status(root_id)
        first_batch = None
        original_update = scanner.update
        start = time.perf_counter()

        def progress(root_id: int, **fields):
            nonlocal first_batch
            if first_batch is None and fields.get("seen", 0) >= 500:
                first_batch = time.perf_counter() - start
            original_update(root_id, **fields)

        scanner.update = progress
        try:
            scanner.phase_one(db.one("SELECT * FROM roots WHERE id=?", (root_id,)))
            elapsed = time.perf_counter() - start
            total = db.one("SELECT COUNT(*) n FROM images")["n"]
            assert total == 20000
            print(f"Phase 1: {total:,} actual directory entries indexed in {elapsed:.3f} s")
            print(f"First 500 rows committed after {first_batch:.3f} s")
            print("Candidates were zero-byte JPEG filenames; no image decoding, browser or thumbnail timing is included.")
        finally:
            scanner.close()


if __name__ == "__main__":
    main()
