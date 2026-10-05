"""Derivative geometry, file delivery, caching, and sidecar annotations."""

import io
import os
from pathlib import Path

import pytest
from PIL import Image


def row_named(client, name):
    return client.app.state.db.one("SELECT * FROM images WHERE name=?", (name,))


@pytest.mark.parametrize("size", [160, 256, 400])
def test_thumb_dimensions_orientation_and_cache(client, indexed, size, monkeypatch):
    row = row_named(client, "img10.jpg")
    endpoint = f"/api/images/{row['id']}/thumb?s={size}"
    first = client.get(endpoint)
    assert first.status_code == 200, first.text[:200] if first.status_code != 200 else ""
    with Image.open(io.BytesIO(first.content)) as image:
        assert max(image.size) <= size
        assert abs(image.width / image.height - row["width"] / row["height"]) < .02
        # Orientation 6 moves the encoded top stripe onto the right edge.
        red, green, blue = image.getpixel((image.width - 3, image.height // 2))
        assert red > 200 and green < 70 and blue < 80
    monkeypatch.setattr(client.app.state.cache, "_generate", lambda *args: pytest.fail("Cache hit tried to render"))
    second = client.get(endpoint)
    assert second.content == first.content
    assert second.headers["etag"] == first.headers["etag"]
    assert "immutable" in second.headers["cache-control"]
    assert client.get(endpoint, headers={"If-None-Match": first.headers["etag"]}).status_code == 304


def test_changed_mtime_invalidates_cache_without_rescan(client, indexed):
    row = row_named(client, "img1.jpg")
    endpoint = f"/api/images/{row['id']}/thumb"
    old = client.get(endpoint)
    os.utime(row["display_path"], (row["mtime"] + 300, row["mtime"] + 300))
    new = client.get(endpoint)
    assert new.headers["etag"] != old.headers["etag"]


def test_preview_rendered_formats_animation_and_broken(client, indexed):
    large = row_named(client, "with spaces #1.jpg")
    preview = client.get(f"/api/images/{large['id']}/preview")
    with Image.open(io.BytesIO(preview.content)) as image:
        assert max(image.size) == 2048
    for name in ("scan.tiff", "phone.heic"):
        row = row_named(client, name)
        if not row:
            continue
        response = client.get(f"/api/images/{row['id']}/file")
        assert response.headers["x-rendered"] == "1"
        assert response.headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (row["width"], row["height"])
    for name in ("motion.gif", "motion.webp", "vector.svg"):
        row = row_named(client, name)
        response = client.get(f"/api/images/{row['id']}/file")
        assert response.content == Path(row["display_path"]).read_bytes()
    broken = row_named(client, "corrupt.jpg")
    response = client.get(f"/api/images/{broken['id']}/thumb")
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.content)).format == "JPEG"


def test_annotations_do_not_modify_file(client, indexed):
    row = row_named(client, "img1.jpg")
    source = Path(row["display_path"]).read_bytes()
    response = client.patch(f"/api/images/{row['id']}", json={"rating": 5, "favorite": True})
    assert response.json()["rating"] == 5
    assert response.json()["favorite"] == 1
    assert Path(row["display_path"]).read_bytes() == source
    assert client.patch(f"/api/images/{row['id']}", json={"rating": 6}).status_code == 422
    record = client.get(f"/api/images/{row['id']}").json()
    assert "DateTimeOriginal" in record["exif"]
    assert "key" in record["cache"]
    assert client.get("/api/images/999999/file").status_code == 404
