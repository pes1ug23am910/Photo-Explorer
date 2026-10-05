"""All mutations use test copies; Recycle Bin integration is always replaced."""

from pathlib import Path
import subprocess

import pytest

from photo_explorer import fileops
from photo_explorer.config import canonical


def row(client):
    return client.app.state.db.one("SELECT * FROM images WHERE name='img1.jpg'")


def test_trash_only_removes_row_with_mocked_recycle_bin(client, indexed, monkeypatch):
    record = row(client)
    calls = []
    monkeypatch.setattr(fileops, "send2trash", calls.append)
    assert client.post(f"/api/images/{record['id']}/trash").status_code == 200
    assert calls == [record["display_path"]]
    assert Path(record["display_path"]).exists()
    assert client.get(f"/api/images/{record['id']}").status_code == 404


def test_move_copy_and_rename(client, indexed, library):
    record = row(client)
    image_id = record["id"]
    key = client.app.state.cache.key(record)
    response = client.post(f"/api/images/{image_id}/move", json={"dest": str(library / "Trips")})
    assert response.status_code == 200, response.text
    moved = response.json()["image"]
    assert moved["id"] == image_id
    assert moved["path"] == canonical(library / "Trips/img1.jpg")
    assert client.app.state.cache.key(moved) != key
    assert not (library / "img1.jpg").exists()
    copied = client.post(f"/api/images/{image_id}/copy", json={"dest": str(library)}).json()["image"]
    assert copied["id"] != image_id
    assert client.post(f"/api/images/{image_id}/copy", json={"dest": str(library)}).status_code == 409
    renamed = client.post(f"/api/images/{image_id}/rename", json={"name": "renamed.jpg"}).json()
    assert renamed["name"] == "renamed.jpg"
    assert (library / "Trips/renamed.jpg").exists()
    assert client.get("/api/destinations").json()


def test_move_outside_root_removes_index(client, indexed, tmp_path):
    record = row(client)
    target = tmp_path / "destination"
    target.mkdir()
    response = client.post(f"/api/images/{record['id']}/move", json={"dest": str(target)})
    assert response.status_code == 200
    assert response.json()["image"] is None
    assert client.get(f"/api/images/{record['id']}").status_code == 404
    assert (target / record["name"]).exists()


@pytest.mark.parametrize("name", ["", "..", "../a.jpg", "a/b.jpg", "a\\b.jpg", "CON.jpg", "aux.png", "COM1.jpg", "COM¹.jpg", "CON .jpg", "bad?.jpg", "a.jpg.", "a.txt", " img.jpg"])
def test_rename_validation(client, indexed, name):
    record = row(client)
    response = client.post(f"/api/images/{record['id']}/rename", json={"name": name})
    assert response.status_code == 400
    assert Path(record["display_path"]).exists()


def test_rename_overwrite_refused(client, indexed):
    assert client.post(f"/api/images/{row(client)['id']}/rename", json={"name": "img2.jpg"}).status_code == 409


def test_stale_index_collision_does_not_move_source(client, indexed, library):
    record = row(client)
    (library / "img2.jpg").unlink()
    response = client.post(f"/api/images/{record['id']}/rename", json={"name": "img2.jpg"})
    assert response.status_code == 409
    assert Path(record["display_path"]).exists()


def test_system_actions_use_argv_and_indexed_path(client, indexed, monkeypatch):
    record = row(client)
    calls = []
    opened = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(fileops.os, "startfile", opened.append, raising=False)
    assert client.post(f"/api/images/{record['id']}/reveal").status_code == 200
    assert client.post(f"/api/images/{record['id']}/open").status_code == 200
    assert calls[0][0] == ["explorer.exe", f"/select,{record['display_path']}"]
    assert calls[0][1]["timeout"] == 15
    assert not calls[0][1].get("shell")
    assert opened == [record["display_path"]]
