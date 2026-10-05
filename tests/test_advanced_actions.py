"""Destructive actions use disposable originals and mocked desktop integration."""

import ctypes
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_rotate_preserves_exif_annotations_and_resets_orientation(client, indexed, degrees):
    row = client.app.state.db.one("SELECT * FROM images WHERE name='IMG_0001.JPG'")
    key = client.app.state.cache.key(row)
    client.patch(f"/api/images/{row['id']}", json={"rating": 3, "favorite": True})
    response = client.post(f"/api/images/{row['id']}/rotate", json={"degrees": degrees})
    assert response.status_code == 200, response.text
    result = response.json()
    expected = (row["height"], row["width"]) if degrees != 180 else (row["width"], row["height"])
    assert (result["width"], result["height"]) == expected
    assert result["exif_orientation"] == 1
    assert result["taken"] == row["taken"]
    assert result["rating"] == 3 and result["favorite"] == 1
    assert client.app.state.cache.key(result) != key
    with Image.open(result["display_path"]) as image:
        assert image.getexif()[274] == 1
        assert image.size == expected
    assert not list(Path(result["display_path"]).parent.glob(".photo-explorer-*"))


@pytest.mark.parametrize("name", ["motion.gif", "motion.webp", "vector.svg"])
def test_rotate_refuses_animation_and_svg(client, indexed, name):
    row = client.app.state.db.one("SELECT * FROM images WHERE name=?", (name,))
    original = Path(row["display_path"]).read_bytes()
    response = client.post(f"/api/images/{row['id']}/rotate", json={"degrees": 90})
    assert response.status_code == 400
    assert Path(row["display_path"]).read_bytes() == original


def test_rotation_failure_leaves_original_untouched(client, indexed, monkeypatch):
    row = client.app.state.db.one("SELECT * FROM images WHERE name='img1.jpg'")
    source = Path(row["display_path"])
    original = source.read_bytes()
    def broken_save(*args, **kwargs):
        raise OSError("Simulated encoder failure")
    monkeypatch.setattr(Image.Image, "save", broken_save)
    assert client.post(f"/api/images/{row['id']}/rotate", json={"degrees": 90}).status_code == 400
    assert source.read_bytes() == original
    assert not list(source.parent.glob(".photo-explorer-*"))


def test_wallpaper_uses_original_or_full_png(client, indexed, monkeypatch):
    calls = []
    def wallpaper(*args):
        calls.append(args)
        return 1
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(SystemParametersInfoW=wallpaper)), raising=False)
    for name in ("img1.jpg", "scan.tiff"):
        row = client.app.state.db.one("SELECT * FROM images WHERE name=?", (name,))
        assert client.post(f"/api/images/{row['id']}/wallpaper").json() == {"ok": True}
        call = calls[-1]
        assert call[0:2] == (20, 0) and call[3] == 3
        assert Path(call[2]).is_file()
        assert call[2] == row["display_path"] if name.endswith(".jpg") else call[2].endswith("_full.png")
