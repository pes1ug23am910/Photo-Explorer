"""Date headers, aggregate filters, folder counts, and forgotten cache versions."""

from datetime import datetime
import os
from pathlib import Path

import pytest


@pytest.mark.parametrize("sort,order", [("mtime", "desc"), ("ctime", "asc"), ("taken", "asc"), ("taken", "desc")])
def test_date_groups_match_ordered_sequence(client, indexed, sort, order):
    params = {"sort": sort, "order": order, "ext": "jpg,png"}
    endpoint = f"/api/roots/{indexed}"
    items = client.get(endpoint + "/images", params={**params, "limit": 500}).json()["items"]
    groups = client.get(endpoint + "/groups", params=params).json()
    assert sum(group["count"] for group in groups) == len(items)
    column = "sort_date" if sort == "taken" else sort
    for group in groups:
        chunk = items[group["start"]:group["start"] + group["count"]]
        assert chunk
        assert all(datetime.fromtimestamp(item[column]).strftime("%Y-%m-%d") == group["date"] for item in chunk)


def test_stats_and_recursive_folder_counts(client, indexed, library):
    endpoint = f"/api/roots/{indexed}"
    listing = client.get(endpoint + "/images", params={"ext": "jpg", "limit": 500}).json()
    stats = client.get(endpoint + "/stats", params={"ext": "jpg"}).json()
    assert stats["total"] == listing["total"]
    assert stats["bytes"] == sum(item["size"] for item in listing["items"])
    assert stats["by_ext"] == {"jpg": listing["total"]}
    assert sum(item["n"] for item in stats["by_month"]) == listing["total"]
    tree = client.get(endpoint + "/dirs").json()[0]
    assert tree["count"] == client.get(endpoint + "/images").json()["total"]
    trips = tree["children"][0]
    assert trips["name"].lower() == "trips"
    assert trips["count"] == client.get(endpoint + "/images", params={"dir": trips["path"]}).json()["total"]
    assert trips["children"][0]["count"] > 0


def test_forget_removes_old_and_current_cached_versions(client, indexed):
    row = client.app.state.db.one("SELECT * FROM images WHERE name='img1.jpg'")
    cache = client.app.state.cache
    old, _ = cache.render(row, 160)
    os.utime(row["display_path"], (row["mtime"] + 50, row["mtime"] + 50))
    new, _ = cache.render(row, 160)
    assert old != new and old.exists() and new.exists()
    assert client.delete(f"/api/roots/{indexed}").status_code == 200
    assert not old.exists() and not new.exists()
    assert Path(row["display_path"]).exists()
