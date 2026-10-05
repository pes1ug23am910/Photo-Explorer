"""Flatten naming, collision, filtering, and reversible execution."""

from pathlib import Path
import pytest

from photo_explorer import flatten, fileops
from test_operations import open_library
from conftest import wait_scan


def test_prefix_tags_cap_and_fallback():
    assert flatten.strip_tags("A [one [two]] {x (y)}  B") == "A B"
    assert flatten.strip_tags("[all]") == "[all]"
    options = flatten.FlattenOptions(target="unused", prefix=True, strip_tags=True, sep="_")
    assert flatten.prefix_name(("A [tag]", "B"), "01.jpg", options) == "A_B_01.jpg"
    options.max_prefix = 1
    assert flatten.prefix_name(("..", "Z"), "01.jpg", options) == "Z_01.jpg"
    assert flatten.prefix_name(("..", "."), "01.jpg", options) == "x_01.jpg"
    options.max_prefix = 3
    assert flatten.prefix_name(("ABCDE",), "01.jpg", options) == "ABC_01.jpg"


def test_collisions_duplicates_filters_and_external_dest(tmp_path):
    target, dest = tmp_path / "target", tmp_path / "dest"
    dest.mkdir()
    (target / "A").mkdir(parents=True)
    (target / "B").mkdir()
    (dest / "SAME.JPG").write_bytes(b"different")
    for relative, data in [("A/same.jpg", b"one"), ("B/same.JPG", b"one"), ("A/skip.png", b"two"), ("A/a.txt", b"three")]:
        (target / relative).write_bytes(data)
    options = flatten.FlattenOptions(target=str(target), dest=str(dest), skip_duplicates=True, ext="JPG; .png", exclude="SKIP.*")
    plan = flatten.plan(options)
    assert plan["summary"] == {"take": 1, "skip": 1, "filtered": 2, "conflicts_renamed": 1, "bytes": 3}
    assert any(Path(entry["dest"]).name == "same (1).jpg" for entry in plan["entries"] if entry["dest"])
    assert any(entry["note"] == "identical, skipped" for entry in plan["entries"])
    assert all((target / relative).exists() for relative in ("A/same.jpg", "B/same.JPG"))
    with pytest.raises(ValueError):
        flatten.plan(options.model_copy(update={"dest": str(target / "A")}))


def test_top_level_files_stay_even_with_external_dest(tmp_path):
    """Both original flatten scripts leave the target's own top-level files untouched."""
    target, dest = tmp_path / "target", tmp_path / "dest"
    (target / "Sub").mkdir(parents=True)
    dest.mkdir()
    (target / "top.jpg").write_bytes(b"top")
    (target / "Sub" / "inner.jpg").write_bytes(b"inner")
    for options in (flatten.FlattenOptions(target=str(target)),
                    flatten.FlattenOptions(target=str(target), dest=str(dest), prefix=True)):
        plan = flatten.plan(options)
        sources = {Path(entry["src"]).name for entry in plan["entries"]}
        assert sources == {"inner.jpg"}, sources
    assert Path(plan["entries"][0]["dest"]).name == "Sub_inner.jpg"


def test_extended_paths_keep_plain_display_and_undo(client, tmp_path):
    from PIL import Image
    from photo_explorer.config import access_path, canonical
    root = tmp_path / 'long-root'
    folder = root / ('A' * 70) / ('B' * 70) / ('C' * 70)
    access_path(folder).mkdir(parents=True)
    source = folder / 'long.jpg'
    assert len(str(source)) > 240
    with Image.new('RGB', (24, 20)) as image:
        image.save(access_path(source))
    root_id = client.post('/api/roots', json={'path': str(root)}).json()['root']['id']
    wait_scan(client, root_id)
    row = client.app.state.db.one('SELECT * FROM images')
    assert canonical(row['display_path']) == canonical(source)
    assert not row['display_path'].startswith('\\\\?\\')
    run = client.post('/api/flatten/run', json={'target': str(root), 'prefix': True, 'max_prefix': 20}).json()
    assert run['failed'] == 0
    undo = client.post(f"/api/operations/{run['operation_id']}/undo")
    assert undo.status_code == 200 and not undo.json()['failed'], undo.text
    assert access_path(source).exists()
    wait_scan(client, root_id)


@pytest.mark.parametrize("mode", ["move", "copy"])
def test_flatten_run_undo_preserves_identity_and_folders(client, tmp_path, monkeypatch, mode):
    root, root_id = open_library(client, tmp_path, ("a.jpg", "A/B/01.jpg"))
    (root / "A/empty").mkdir()
    db = client.app.state.db
    row = db.one("SELECT * FROM images WHERE name='01.jpg'")
    db.execute("UPDATE images SET rating=5,favorite=1 WHERE id=?", (row["id"],))
    monkeypatch.setattr(fileops, "send2trash", lambda path: Path(path).unlink())
    body = {"target": str(root), "mode": mode, "prefix": True}
    preview = client.post("/api/flatten/plan", json=body).json()
    assert preview["summary"]["take"] == 1
    assert client.post("/api/flatten/run", json={**body, "expected_total": 100}).status_code == 409
    result = client.post("/api/flatten/run", json={**body, "expected_total": preview["total"]}).json()
    assert result["kind"] == "flatten" and result["failed"] == 0
    assert (root / "A_B_01.jpg").exists()
    if mode == "move":
        assert not (root / "A").exists()
        assert db.one("SELECT name FROM images WHERE id=?", (row["id"],))["name"] == "A_B_01.jpg"
    response = client.post(f"/api/operations/{result['operation_id']}/undo")
    assert response.status_code == 200, response.text
    undo = response.json()
    assert undo["failed"] == 0
    assert (root / "A/B/01.jpg").exists() and (root / "A/empty").is_dir()
    assert not (root / "A_B_01.jpg").exists()
    restored = db.one("SELECT * FROM images WHERE id=?", (row["id"],))
    assert restored["rating"] == 5 and restored["favorite"] == 1 and restored["rnd"] == row["rnd"]
    wait_scan(client, root_id)
