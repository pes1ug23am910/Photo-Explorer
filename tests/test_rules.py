"""Rule semantics, validation, presets, and reversible collision policies."""

from pathlib import Path
import pytest
from pydantic import ValidationError

from photo_explorer.rules import Condition, matches, seed_presets
from photo_explorer import fileops
from test_operations import open_library
from conftest import wait_scan


@pytest.mark.parametrize("field,op,value", [
    ("name", "contains", "PHOTO"), ("name", "starts", "photo"), ("name", "ends", ".JPG"),
    ("name", "glob", "PHOTO-*.JPG"), ("name", "regex", r"^photo-.*\.jpg$"),
    ("name", "suffix", "-t"), ("name", "prefix", "photo"), ("ext", "in", ["jpg"]),
    ("kind", "in", ["raster"]), ("size", "lt", 6 * 1024**2), ("size", "gt", 100 * 1024),
    ("size", "between", [4 * 1024**2, 6 * 1024**2]), ("width", "lt", 300), ("width", "gt", 100),
    ("width", "between", [150, 250]), ("height", "lt", 200), ("height", "gt", 50), ("height", "between", [80, 120]),
    ("pixels", "lt", 30000), ("pixels", "gt", 10000), ("pixels", "between", [15000, 25000]),
    ("orient", "is", "landscape"), ("mtime", "before", 300), ("mtime", "after", 100),
    ("mtime", "between", [100, 300]), ("ctime", "before", "2030-01-01"), ("ctime", "after", 100),
    ("ctime", "between", [100, 300]), ("taken", "before", 300), ("taken", "after", 100),
    ("taken", "between", [100, 300]), ("rating", "gte", 3), ("rating", "eq", 4),
    ("favorite", "is", True), ("camera", "contains", "CANON"), ("meta_failed", "is", False),
    ("dir", "is", "Sub"), ("dir", "under", ""),
])
def test_every_condition(tmp_path, field, op, value):
    root = tmp_path
    row = {"name": "photo-t.jpg", "ext": "jpg", "size": 5 * 1024**2, "width": 200, "height": 100,
           "pixels": 20000, "orient": "landscape", "mtime": 200, "ctime": 200, "taken": 200,
           "rating": 4, "favorite": 1, "camera": "Canon EOS", "meta_state": 1, "dir": str(root / "Sub")}
    condition = Condition(field=field, op=op, value=value)
    assert matches(row, condition, str(root))
    if field not in {"name", "ext", "size", "mtime", "ctime", "dir"}:
        assert not matches({**row, "other_file": True}, condition, str(root))


@pytest.mark.parametrize("condition", [
    {"field": "size", "op": "glob", "value": "*"}, {"field": "size", "op": "between", "value": [2, 1]},
    {"field": "width", "op": "gt", "value": "nan"}, {"field": "name", "op": "regex", "value": "["},
    {"field": "favorite", "op": "is", "value": "false"}, {"field": "kind", "op": "in", "value": ["video"]},
])
def test_invalid_conditions(condition):
    with pytest.raises(ValidationError):
        Condition(**condition)


def rule(root_id, dest, collision="rename"):
    return {"name": "Suffix -t", "scope": {"root_id": root_id}, "match": "all",
            "conditions": [{"field": "name", "op": "suffix", "value": "-t"}],
            "action": {"type": "move", "dest": str(dest), "collision": collision, "create_dest": True}}


@pytest.mark.parametrize("collision,identical,action", [("rename", False, "moved"), ("skip", False, "skipped"),
    ("skip_identical", True, "skipped"), ("skip_identical", False, "moved")])
def test_collision_preview_run_undo(client, tmp_path, collision, identical, action):
    root, root_id = open_library(client, tmp_path, ("photo-t.jpg", "photo.jpg"))
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "PHOTO-T.JPG").write_bytes((root / "photo-t.jpg").read_bytes() if identical else b"other")
    body = rule(root_id, dest, collision)
    preview = client.post("/api/rules/preview", json={"rule": body}).json()
    assert preview["total"] == 1 and preview["entries"][0]["action"] == action
    assert client.post("/api/rules/run", json={"rule": body, "expected_total": 2}).status_code == 409
    result = client.post("/api/rules/run", json={"rule": body, "expected_total": preview["total"]}).json()
    assert result["total"] == preview["total"] and result["failed"] == 0
    assert client.post(f"/api/operations/{result['operation_id']}/undo").json()["failed"] == 0
    assert (root / "photo-t.jpg").exists()


def test_other_files_all_any_selection_and_rematch_guard(client, tmp_path):
    root, root_id = open_library(client, tmp_path, ("photo-t.jpg", "photo.png"))
    (root / "notes-t.txt").write_text("notes")
    body = rule(root_id, tmp_path / "dest")
    assert client.post("/api/rules/preview", json={"rule": body}).json()["total"] == 1
    body["scope"]["include_other_files"] = True
    assert client.post("/api/rules/preview", json={"rule": body}).json()["total"] == 2
    body["conditions"].append({"field": "ext", "op": "in", "value": ["png"]})
    assert client.post("/api/rules/preview", json={"rule": body}).json()["total"] == 0
    body["match"] = "any"
    assert client.post("/api/rules/preview", json={"rule": body}).json()["total"] == 3
    row = client.app.state.db.one("SELECT id FROM images WHERE name='photo.png'")
    body["scope"]["ids"] = [row["id"]]
    assert client.post("/api/rules/preview", json={"rule": body}).json()["total"] == 1
    body["action"]["dest"] = str(root)
    assert client.post("/api/rules/preview", json={"rule": body}).status_code == 400


def test_presets_crud_and_no_reseed(client, tmp_path):
    root, root_id = open_library(client, tmp_path)
    starter = client.get("/api/rules").json()
    assert {row["name"] for row in starter} == {"Suffix -t", "Larger than 5 MB", "Smaller than 100 KB", "Animated images", "PNG screenshots"}
    body = rule(root_id, tmp_path / "dest")
    response = client.post("/api/rules", json=body)
    assert response.status_code == 200
    rule_id = response.json()["id"]
    body["name"] = "Edited"
    assert client.put(f"/api/rules/{rule_id}", json=body).json()["name"] == "Edited"
    assert client.post(f"/api/rules/{rule_id}/run").json()["kind"] == "rule"
    assert client.delete(f"/api/rules/{rule_id}").json()["ok"]
    client.delete(f"/api/rules/{starter[0]['id']}")
    seed_presets(client.app.state.db)
    assert len(client.get("/api/rules").json()) == 4
