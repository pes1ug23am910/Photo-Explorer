"""Journal and undo safety on disposable libraries."""

import json
from pathlib import Path
import time

import pytest
from PIL import Image

from photo_explorer import fileops
from photo_explorer.config import canonical
from conftest import wait_scan


def open_library(client, tmp_path, names=("a.jpg", "nested/b.jpg")):
    root = tmp_path / "pictures"
    for index, name in enumerate(names):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with Image.new("RGB", (30 + index, 20), (index * 30, 60, 70)) as image:
            image.save(path)
    result = client.post("/api/roots", json={"path": str(root)})
    assert result.status_code == 200, result.text
    root_id = result.json()["root"]["id"]
    wait_scan(client, root_id)
    return root, root_id


def wait_operation(client, result):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        op = client.get(f"/api/operations/{result['operation_id']}").json()
        if op["status"] != "running":
            return op
        time.sleep(.01)
    raise AssertionError("Operation did not finish")


@pytest.mark.parametrize("kind", ["move", "copy", "rename", "trash"])
def test_single_actions_journal_and_undo(client, tmp_path, monkeypatch, kind):
    root, root_id = open_library(client, tmp_path)
    db = client.app.state.db
    row = db.one("SELECT * FROM images WHERE name='a.jpg'")
    client.patch(f"/api/images/{row['id']}", json={"rating": 5, "favorite": True})
    monkeypatch.setattr(fileops, "send2trash", lambda path: Path(path).unlink())
    body = {"name": "renamed.jpg"} if kind == "rename" else {"dest": str(root / "nested")} if kind in {"move", "copy"} else {}
    response = client.post(f"/api/images/{row['id']}/{kind}", json=body)
    assert response.status_code == 200, response.text
    op = wait_operation(client, response.json())
    assert (op["kind"], op["status"], op["done"], op["failed"]) == (kind, "done", 1, 0)
    assert op["entries"][0]["action"] == {"move": "moved", "copy": "copied", "rename": "renamed", "trash": "trashed"}[kind]
    manifest = client.app.state.home / "operations" / f"{op['id']}.json"
    assert json.loads(manifest.read_text(encoding="utf-8"))["entries"] == op["entries"]
    result = client.post(f"/api/operations/{op['id']}/undo")
    if kind == "trash":
        assert result.status_code == 409 and not op["undoable"]
        return
    undone = wait_operation(client, result.json())
    assert undone["kind"] == "undo" and undone["undoes"] == op["id"] and undone["failed"] == 0
    assert (root / "a.jpg").exists()
    restored = db.one("SELECT * FROM images WHERE path=?", (canonical(root / "a.jpg"),))
    assert (restored["id"], restored["rating"], restored["favorite"], restored["rnd"]) == (row["id"], 5, 1, row["rnd"])
    assert client.post(f"/api/operations/{op['id']}/undo").json()["operation_id"] == undone["id"]
    assert len(client.get("/api/operations").json()) == 2
    wait_scan(client, root_id)


def test_external_move_undo_reserves_ids_and_annotations(client, tmp_path):
    root, root_id = open_library(client, tmp_path, ("last.jpg",))
    dest = tmp_path / "outside"
    dest.mkdir()
    db = client.app.state.db
    row = db.one("SELECT * FROM images")
    client.patch(f"/api/images/{row['id']}", json={"rating": 4})
    op = client.post(f"/api/images/{row['id']}/move", json={"dest": str(dest)}).json()
    assert not db.one("SELECT * FROM images")
    with Image.new("RGB", (10, 10)) as image:
        image.save(root / "new.jpg")
    client.post(f"/api/roots/{root_id}/rescan")
    wait_scan(client, root_id)
    assert db.one("SELECT id FROM images")["id"] != row["id"]
    undo = client.post(f"/api/operations/{op['operation_id']}/undo").json()
    assert undo["failed"] == 0
    assert db.one("SELECT rating FROM images WHERE id=?", (row["id"],))["rating"] == 4


def test_batch_conflict_continues_and_double_undo_is_safe(client, tmp_path):
    root, root_id = open_library(client, tmp_path, ("a.jpg", "b.jpg"))
    dest = root / "dest"
    dest.mkdir()
    ids = [row["id"] for row in client.app.state.db.all("SELECT id FROM images")]
    op = client.post("/api/operations/batch", json={"kind": "move", "ids": ids, "dest": str(dest)}).json()
    wait_scan(client, root_id)
    (root / "b.jpg").write_bytes(b"new user's file")
    result = client.post(f"/api/operations/{op['operation_id']}/undo").json()
    assert (result["done"], result["failed"]) == (1, 1)
    assert (root / "b.jpg").read_bytes() == b"new user's file"
    assert (root / "a.jpg").exists() and (dest / "b.jpg").exists()
    assert any("Both source" in (entry["error"] or "") for entry in result["entries"])
    assert client.post(f"/api/operations/{op['operation_id']}/undo").json()["id"] == result["id"]
    wait_scan(client, root_id)


def test_large_operation_returns_immediately_and_cancels(client, tmp_path):
    import threading
    entered, release = threading.Event(), threading.Event()
    service = client.app.state.operations
    def execute(entry):
        entered.set()
        assert release.wait(3)
        return {"action": "skipped"}
    result = service.run("copy", "Large test", None, {}, [{"src": str(tmp_path / str(i)), "action": "copied"} for i in range(201)], execute)
    assert set(result) == {"operation_id"}
    assert entered.wait(3)
    try:
        assert client.post(f"/api/operations/{result['operation_id']}/cancel").status_code == 200
    finally:
        release.set()
    op = wait_operation(client, result)
    assert op["status"] == "cancelled" and op["done"] < 201


def test_modified_copy_is_not_recycled_by_undo(client, tmp_path, monkeypatch):
    root, _ = open_library(client, tmp_path)
    row = client.app.state.db.one("SELECT * FROM images WHERE name='a.jpg'")
    result = client.post(f"/api/images/{row['id']}/copy", json={"dest": str(root / "nested")}).json()
    (root / "nested/a.jpg").write_bytes(b"edited copy")
    monkeypatch.setattr(fileops, "send2trash", lambda path: pytest.fail("Changed copy was recycled"))
    assert client.post(f"/api/operations/{result['operation_id']}/undo").json()["failed"] == 1


def test_index_failure_keeps_successful_move_recoverable(client, tmp_path, monkeypatch):
    root, _ = open_library(client, tmp_path)
    service = client.app.state.operations
    original = fileops.repoint
    def broken(*args):
        raise RuntimeError('simulated index update failure')
    monkeypatch.setattr(fileops, 'repoint', broken)
    result = service.run('move', 'Move with index failure', 1, {},
                         [{'src': str(root / 'a.jpg'), 'dest': str(root / 'nested/a.jpg'), 'action': 'moved'}])
    assert result['failed'] == 1 and result['entries'][0]['action'] == 'moved'
    assert (root / 'nested/a.jpg').exists() and not (root / 'a.jpg').exists()
    monkeypatch.setattr(fileops, 'repoint', original)
    undo = client.post(f"/api/operations/{result['operation_id']}/undo")
    assert undo.status_code == 200 and not undo.json()['failed'], undo.text
    assert (root / 'a.jpg').exists()


def test_move_between_roots_preserves_id(client, tmp_path):
    root, root_id = open_library(client, tmp_path, ('a.jpg',))
    other = tmp_path / 'second'
    other.mkdir()
    second_id = client.post('/api/roots', json={'path': str(other)}).json()['root']['id']
    wait_scan(client, second_id)
    row = client.app.state.db.one('SELECT * FROM images')
    client.patch(f"/api/images/{row['id']}", json={'rating': 4, 'favorite': True})
    result = client.post(f"/api/images/{row['id']}/move", json={'dest': str(other)}).json()
    assert result['image']['id'] == row['id'] and result['image']['root_id'] == second_id
    client.post(f"/api/operations/{result['operation_id']}/undo")
    restored = client.app.state.db.one('SELECT * FROM images WHERE id=?', (row['id'],))
    assert (restored['root_id'], restored['rating'], restored['favorite']) == (root_id, 4, 1)
