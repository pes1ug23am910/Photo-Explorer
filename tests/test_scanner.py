"""Two scan phases and incremental changes."""

import os
from pathlib import Path

from PIL import Image

from photo_explorer.config import EXTENSIONS, canonical
from photo_explorer.scanner import Scanner
from conftest import wait_scan


def test_index_counts_and_broken_files(client, indexed, library):
    rows = client.get(f"/api/roots/{indexed}/images?limit=500").json()["items"]
    expected = [path for path in library.rglob("*") if path.suffix.lower().lstrip(".") in EXTENSIONS and ".hidden" not in path.parts]
    assert len(rows) == len(expected)
    assert {row["ext"] for row in rows} >= {"jpg", "png", "gif", "webp", "tiff", "svg", "avif", "ico"}
    assert {row["name"] for row in rows if row["meta_state"] == 2} == {"corrupt.jpg", "empty.png"}
    assert all(row["meta_state"] in {1, 2} for row in rows)
    record = client.app.state.db.one("SELECT * FROM images WHERE name='img1.jpg'")
    stat = Path(record["display_path"]).stat()
    assert record["ctime"] == getattr(stat, "st_birthtime", stat.st_ctime)


def test_incremental_add_modify_delete(client, indexed, library):
    db = client.app.state.db
    row = db.one("SELECT * FROM images WHERE name='img1.jpg'")
    client.patch(f"/api/images/{row['id']}", json={"rating": 4, "favorite": True})
    rnd = row["rnd"]
    with Image.new("RGB", (73, 92), "green") as image:
        image.save(library / "img1.jpg")
        image.save(library / "added.jpg")
    (library / "img2.jpg").unlink()
    os.utime(library / "img1.jpg", (row["mtime"] + 100, row["mtime"] + 100))
    client.post(f"/api/roots/{indexed}/rescan")
    job = wait_scan(client, indexed)
    assert job["indexed"] == 2
    assert job["removed"] == 1
    updated = db.one("SELECT * FROM images WHERE id=?", (row["id"],))
    assert (updated["width"], updated["height"], updated["rating"], updated["favorite"]) == (73, 92, 4, 1)
    assert updated["rnd"] == rnd
    client.post(f"/api/roots/{indexed}/rescan")
    assert wait_scan(client, indexed)["indexed"] == 0


def test_nonrecursive(client, library):
    response = client.post("/api/roots", json={"path": str(library), "recursive": False}).json()
    root_id = response["root"]["id"]
    wait_scan(client, root_id)
    rows = client.get(f"/api/roots/{root_id}/images").json()["items"]
    assert all(row["dir"] == canonical(library) for row in rows)
    assert len(rows) < len(list(library.rglob("*.jpg"))) + 15


def test_phase_one_never_opens_images(client, library, monkeypatch):
    from photo_explorer import scanner
    monkeypatch.setattr(Scanner, "phase_two", lambda self, root_id: None)
    monkeypatch.setattr(scanner, "read_metadata", lambda *args: (_ for _ in ()).throw(AssertionError("decoded in phase one")))
    root_id = client.post("/api/roots", json={"path": str(library)}).json()["root"]["id"]
    wait_scan(client, root_id)
    assert client.app.state.db.one("SELECT COUNT(*) n FROM images WHERE meta_state=0")["n"] > 10


def test_one_running_job_per_root(client, library, monkeypatch):
    import threading
    gate = threading.Event()
    entered = threading.Event()
    original = Scanner.phase_one

    def paused(self, root):
        entered.set()
        assert gate.wait(3)
        original(self, root)

    monkeypatch.setattr(Scanner, "phase_one", paused)
    root_id = client.post("/api/roots", json={"path": str(library)}).json()["root"]["id"]
    assert entered.wait(3)
    try:
        first = client.get(f"/api/roots/{root_id}/scan").json()
        second = client.post(f"/api/roots/{root_id}/rescan").json()["job"]
        assert first["started"] == second["started"]
        assert second["running"]
    finally:
        gate.set()
    wait_scan(client, root_id)


def test_unreadable_subfolder_is_skipped_not_fatal(client, indexed, library, monkeypatch):
    """A folder that cannot be listed is skipped, its rows survive, and other deletions still prune."""
    from photo_explorer import scanner

    blocked = canonical(library / "Trips" / "Coast")
    db = client.app.state.db
    before = db.one("SELECT COUNT(*) n FROM images WHERE dir=?", (blocked,))["n"]
    assert before > 0
    (library / "img1.jpg").unlink()
    real_scandir = os.scandir

    def guarded(path=".", *args, **kwargs):
        if canonical(path) == blocked:
            raise PermissionError(13, "Access is denied", str(path))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(scanner.os, "scandir", guarded)
    client.post(f"/api/roots/{indexed}/rescan")
    job = wait_scan(client, indexed)
    assert job["phase"] == "done"
    assert job["skipped"] == 1
    assert job["removed"] == 1
    assert db.one("SELECT COUNT(*) n FROM images WHERE dir=?", (blocked,))["n"] == before
    assert db.one("SELECT COUNT(*) n FROM images WHERE name='img1.jpg'")["n"] == 0
