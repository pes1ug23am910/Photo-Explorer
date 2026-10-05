"""The viewer's server-order acceptance contract."""

import pytest


@pytest.mark.parametrize("sort,order", [("mtime", "desc"), ("taken", "asc"), ("name", "asc"), ("size", "desc"), ("random", "asc")])
def test_walk_exactly_matches_full_list(client, indexed, sort, order):
    endpoint = f"/api/roots/{indexed}/images"
    params = {"sort": sort, "order": order}
    full = client.get(endpoint, params={**params, "limit": 500}).json()
    previous = float("inf")
    for index in range(5, full["total"]):
        page = client.get(endpoint, params={**params, "offset": index, "limit": 1}).json()
        assert page["total"] == full["total"]
        assert page["items"][0]["id"] == full["items"][index]["id"]
        if sort == "mtime":
            assert page["items"][0]["mtime"] <= previous
            previous = page["items"][0]["mtime"]
        neighbours = client.get(endpoint, params={**params, "offset": max(0, index - 2), "limit": 5}).json()
        assert neighbours["items"][index - max(0, index - 2)]["id"] == page["items"][0]["id"]


def test_delete_midwalk_and_clamp(client, indexed, monkeypatch):
    from photo_explorer import fileops
    monkeypatch.setattr(fileops, "send2trash", lambda path: None)
    endpoint = f"/api/roots/{indexed}/images?sort=mtime&order=desc"
    before = client.get(endpoint).json()
    index = 5
    removed = before["items"][index]["id"]
    assert client.post(f"/api/images/{removed}/trash").json() == {"ok": True, "next_hint": None}
    after = client.get(endpoint).json()
    assert after["total"] == before["total"] - 1
    expected = [item["id"] for item in before["items"] if item["id"] != removed]
    for position in range(index, len(expected)):
        result = client.get(endpoint + f"&offset={position}&limit=1").json()
        assert result["items"][0]["id"] == expected[position]
    last_id = expected[-1]
    client.post(f"/api/images/{last_id}/trash")
    empty = client.get(endpoint + f"&offset={len(expected) - 1}&limit=1").json()
    assert empty["items"] == []
    clamped = client.get(endpoint + f"&offset={empty['total'] - 1}&limit=1").json()
    assert clamped["items"][0]["id"] == expected[-2]


def test_external_deletion_is_pruned_when_preview_is_requested(client, indexed):
    from pathlib import Path
    endpoint = f"/api/roots/{indexed}/images?sort=mtime&order=desc"
    original = client.get(endpoint).json()
    item = original["items"][5]
    record = client.get(f"/api/images/{item['id']}").json()
    Path(record["display_path"]).unlink()
    assert client.get(f"/api/images/{item['id']}/preview").status_code == 404
    following = client.get(endpoint + "&offset=5&limit=1").json()
    assert following["total"] == original["total"] - 1
    assert following["items"][0]["id"] == original["items"][6]["id"]
