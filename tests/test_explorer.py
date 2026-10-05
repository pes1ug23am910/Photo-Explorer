"""Exact scopes, filesystem counts, and byte-stable structure exports."""

import os
from photo_explorer.explorer import structure
from photo_explorer.config import canonical
from test_operations import open_library
from conftest import wait_scan


def test_exact_subtree_listing_position_stats_groups_and_counts(client, tmp_path):
    root, root_id = open_library(client, tmp_path, ("a.jpg", "Sub/b.jpg", "Sub/Deep/c.jpg"))
    (root / "Sub/readme.txt").write_text("text")
    (root / "Empty").mkdir()
    client.post(f"/api/roots/{root_id}/rescan")
    wait_scan(client, root_id)
    endpoint = f"/api/roots/{root_id}"
    params = {"dir": str(root / "Sub"), "dir_mode": "exact"}
    listing = client.get(endpoint + "/images", params=params).json()
    assert listing["total"] == 1
    assert client.get(endpoint + "/images", params={"dir": str(root / "Sub")}).json()["total"] == 2
    assert client.get(endpoint + "/images/position", params={**params, "image_id": listing["items"][0]["id"]}).json() == {"index": 0}
    deep_id = client.app.state.db.one("SELECT id FROM images WHERE name='c.jpg'")["id"]
    assert client.get(endpoint + "/images/position", params={**params, "image_id": deep_id}).status_code == 404
    assert client.get(endpoint + "/stats", params=params).json()["total"] == 1
    assert sum(row["count"] for row in client.get(endpoint + "/groups", params=params).json()) == 1
    tree = client.get(endpoint + "/dirs").json()[0]
    sub = next(child for child in tree["children"] if child["name"].lower() == "sub")
    assert (tree["count"], tree["subfolders"], sub["images_direct"], sub["files_direct"], sub["subfolders"]) == (3, 2, 1, 1, 1)


def test_structure_fixture_and_save(client, tmp_path, monkeypatch):
    root = tmp_path / "Tree"
    for directory in ("Sub/Deeper", "Denied", "SkipMe", "PathSkip", "Directory Structure"):
        (root / directory).mkdir(parents=True)
    for path in ("file2.jpg", "file1.jpg", "Sub/a.jpg", "Sub/Deeper/z.png", "SkipMe/hidden.txt"):
        (root / path).touch()
    real_scandir = os.scandir
    def denied(path):
        if canonical(path) == canonical(root / "Denied"):
            raise PermissionError(13, "permission denied")
        return real_scandir(path)
    monkeypatch.setattr(os, "scandir", denied)
    skip = f"skipme,{root / 'PathSkip'}"
    header = f"Structure for: {root}\n{'=' * (len(str(root)) + 16)}\n\nTree/\n"
    expected = header + "|-- file1.jpg\n|-- file2.jpg\n|-- Denied/\n|   |-- [Error: Permission Denied]\n`-- Sub/\n    |-- a.jpg\n    `-- Deeper/\n        `-- z.png\n"
    assert structure(str(root), True, skip) == expected
    response = client.get("/api/fs/structure", params={"path": str(root), "skip": skip, "files": 1})
    assert response.content == expected.encode()
    folders = header + "|-- Denied/\n|   |-- [Error: Permission Denied]\n`-- Sub/\n    `-- Deeper/\n"
    assert structure(str(root), False, skip) == folders
    saved = client.post("/api/fs/structure/save", json={"path": str(root), "skip": skip}).json()
    assert saved["path"] == str(root / "Directory Structure/Tree Structure.txt")
    assert (root / "Directory Structure/Tree Structure.txt").read_bytes() == expected.encode()
