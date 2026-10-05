"""Independent order expectations and parameterized filter coverage."""

from functools import cmp_to_key
from datetime import datetime

import pytest

from photo_explorer.db import SORTS


def expected_compare(sort, direction):
    columns = ["dir", "name_key"] if sort == "path" else [{"taken": "sort_date", "name": "name_key", "random": "rnd"}.get(sort, sort)]

    def compare(left, right):
        for column in columns:
            a, b = left[column], right[column]
            if a is None or b is None:
                if a is b:
                    continue
                return 1 if a is None else -1
            if isinstance(a, str):
                a, b = a.lower(), b.lower()
            if a != b:
                return (1 if a > b else -1) * (-1 if direction == "desc" else 1)
        return (left["name_key"] > right["name_key"]) - (left["name_key"] < right["name_key"]) or (left["id"] > right["id"]) - (left["id"] < right["id"])
    return compare


@pytest.mark.parametrize("sort", SORTS)
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_all_sorts_both_directions(client, indexed, sort, order):
    db = client.app.state.db
    # Exercise ties and nulls even for fields that are normally unique.
    db.execute("UPDATE images SET mtime=12345,ctime=12345,size=500,rnd=0.5 WHERE name IN ('img1.jpg','img2.jpg','img10.jpg')")
    rows = db.all("SELECT * FROM images WHERE root_id=?", (indexed,))
    expected = sorted(rows, key=cmp_to_key(expected_compare(sort, order)))
    response = client.get(f"/api/roots/{indexed}/images", params={"sort": sort, "order": order, "limit": 500}).json()
    assert [row["id"] for row in response["items"]] == [row["id"] for row in expected]
    for index in [0, 5, len(expected) - 1]:
        position = client.get(f"/api/roots/{indexed}/images/position", params={"image_id": expected[index]["id"], "sort": sort, "order": order})
        assert position.json() == {"index": index}


@pytest.mark.parametrize("params,predicate", [
    ({"ext": "jpg,png"}, lambda r: r["ext"] in {"jpg", "png"}),
    ({"q": "IMG"}, lambda r: "img" in r["name"].lower()),
    ({"size_min": "15000"}, lambda r: r["size"] >= 15000),
    ({"size_max": "15000"}, lambda r: r["size"] <= 15000),
    ({"orient": "portrait"}, lambda r: r["orient"] == "portrait"),
    ({"min_rating": "3"}, lambda r: r["rating"] >= 3),
    ({"favorites": "1"}, lambda r: r["favorite"] == 1),
    ({"camera": "Canon"}, lambda r: "canon" in (r["camera"] or "").lower()),
    ({"animated": "1"}, lambda r: r["animated"] == 1),
    ({"has_gps": "1"}, lambda r: r["gps_lat"] is not None),
    ({"min_pixels": "500000"}, lambda r: (r["pixels"] or 0) >= 500000),
    ({"meta_failed": "1"}, lambda r: r["meta_state"] == 2),
    ({"date_field": "mtime", "date_from": "2026-09-02", "date_to": "2026-09-02"},
     lambda r: datetime.fromtimestamp(r["mtime"]).date().isoformat() == "2026-09-02"),
    ({"date_field": "taken", "date_to": "2022-12-31"}, lambda r: r["sort_date"] <= datetime(2023, 1, 1).timestamp() - .000001),
    ({"date_field": "ctime", "date_from": "0"}, lambda r: r["ctime"] >= 0),
])
def test_every_filter(client, indexed, params, predicate):
    db = client.app.state.db
    db.execute("UPDATE images SET rating=4,favorite=1,gps_lat=12.9,gps_lon=77.6 WHERE name='img1.jpg'")
    all_rows = db.all("SELECT * FROM images WHERE root_id=?", (indexed,))
    response = client.get(f"/api/roots/{indexed}/images", params={**params, "limit": 500}).json()
    assert {row["id"] for row in response["items"]} == {row["id"] for row in all_rows if predicate(row)}


def test_path_subfolder_and_and_filters(client, indexed, library):
    endpoint = f"/api/roots/{indexed}/images"
    response = client.get(endpoint, params={"dir": str(library / "Trips"), "ext": "jpg", "q": "Trips/Coast"}).json()
    assert response["items"]
    assert all("coast" in row["dir"].lower() and row["ext"] == "jpg" for row in response["items"])
    assert client.get(endpoint, params={"q": "%"}).json()["total"] == 0
    assert client.get(endpoint, params={"q": "CAFÉ"}).json()["total"] == 1
    excluded = client.app.state.db.one("SELECT id FROM images WHERE name='img1.jpg'")["id"]
    assert client.get(endpoint + "/position", params={"image_id": excluded, "ext": "png"}).status_code == 404


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"limit": "bad"},
    {"sort": "DROP TABLE"}, {"order": "sideways"}, {"date_field": "path"}, {"min_rating": 6}, {"date_to": "oops"}])
def test_invalid_query_bounds(client, indexed, params):
    response = client.get(f"/api/roots/{indexed}/images", params=params)
    assert response.status_code == 400
    assert "detail" in response.json()


def test_defaults_pagination_and_shuffle(client, indexed):
    endpoint = f"/api/roots/{indexed}"
    def ids(params):
        return [row["id"] for row in client.get(endpoint + "/images", params=params).json()["items"]]
    assert ids({"sort": "mtime"}) == ids({"sort": "mtime", "order": "desc"})
    assert ids({"sort": "name"}) == ids({"sort": "name", "order": "asc"})
    full = ids({"sort": "name"})
    assert ids({"sort": "name", "offset": 4, "limit": 3}) == full[4:7]
    client.post(endpoint + "/shuffle", json={"seed": 7})
    first = ids({"sort": "random"})
    assert first == ids({"sort": "random"})
    client.post(endpoint + "/shuffle", json={"seed": 8})
    assert first != ids({"sort": "random"})
    client.post(endpoint + "/shuffle", json={"seed": 7})
    assert first == ids({"sort": "random"})
