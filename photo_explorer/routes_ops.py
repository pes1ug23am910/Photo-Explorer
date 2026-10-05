"""Operations journal, flatten, batch actions, and saved rule endpoints."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from . import fileops, flatten, rules
from .config import access_path, canonical, validate_directory

router = APIRouter(prefix="/api")


def check_total(expected: int | None, actual: int) -> None:
    """Reject changed plans before any mutation."""
    if expected is not None and actual != expected:
        raise HTTPException(409, "Plan size changed; preview again")


def single_action(request: Request, row: dict, kind: str, dest: str | None = None,
                  name: str | None = None) -> dict:
    """Preserve legacy response shapes while adding a journal id."""
    db = request.app.state.db
    access_path(row["display_path"]).stat()
    target = None
    if kind == "rename":
        fileops.validate_name(name)
        target = fileops.require_target(str(Path(row["display_path"]).parent), name)
    elif kind in {"move", "copy"}:
        target = fileops.require_target(dest, row["name"])
    if target:
        fileops.require_unindexed_target(db, target)
    entry = {"src": row["display_path"], "dest": str(target) if target else None,
             "action": {"move": "moved", "copy": "copied", "rename": "renamed", "trash": "trashed"}[kind]}
    result = request.app.state.operations.run(kind, f"{kind.title()} {row['name']}", row["root_id"], {}, [entry], undoable=kind != "trash")
    if result["failed"]:
        raise ValueError(result["entries"][0]["error"])
    operation_id = result["operation_id"]
    if kind == "trash":
        return {"ok": True, "next_hint": None, "operation_id": operation_id}
    image = db.one("SELECT * FROM images WHERE path=?", (canonical(target),))
    if kind == "rename":
        return {**image, "operation_id": operation_id}
    return {"ok": True, "path": str(target), "image": image, "operation_id": operation_id}


@router.get("/operations")
def operations(request: Request, limit: int = Query(50, ge=1, le=500), undoable: bool = False) -> list[dict]:
    service = request.app.state.operations
    where = " WHERE status='done' AND undoable=1 AND undone_by IS NULL" if undoable else ""
    return [service.get(row["id"], limit=0) for row in service.db.all("SELECT id FROM operations" + where + " ORDER BY id DESC LIMIT ?", (limit,))]


@router.get("/operations/{op_id}")
def operation(op_id: int, request: Request, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=2000)) -> dict:
    return request.app.state.operations.get(op_id, offset, limit)


@router.post("/operations/{op_id}/undo")
def undo(op_id: int, request: Request) -> dict:
    return request.app.state.operations.undo(op_id)


@router.post("/operations/{op_id}/cancel")
def cancel(op_id: int, request: Request) -> dict:
    return request.app.state.operations.cancel(op_id)


class BatchBody(rules.StrictModel):
    ids: list[int] = Field(min_length=1)
    kind: Literal["move", "copy", "trash"]
    dest: str | None = None


@router.post("/operations/batch")
def batch(body: BatchBody, request: Request) -> dict:
    db = request.app.state.db
    directory = validate_directory(body.dest or "") if body.kind != "trash" else None
    entries = []
    for image_id in dict.fromkeys(body.ids):
        row = db.one("SELECT * FROM images WHERE id=?", (image_id,))
        if row is None:
            raise HTTPException(404, f"Image {image_id} not found")
        entries.append({"src": row["display_path"], "dest": str(directory / row["name"]) if directory else None,
                        "size": row["size"], "mtime": row["mtime"],
                        "action": {"move": "moved", "copy": "copied", "trash": "trashed"}[body.kind]})
    return request.app.state.operations.run(body.kind, f"{body.kind.title()} {len(entries)} images", row["root_id"],
                                             body.model_dump(), entries, undoable=body.kind != "trash")


@router.post("/flatten/plan")
def flatten_plan(body: flatten.FlattenOptions) -> dict:
    result = flatten.plan(body)
    return {**result, "entries": result["entries"][:2000]}


@router.post("/flatten/run")
def flatten_run(body: flatten.FlattenOptions, request: Request) -> dict:
    plan = flatten.plan(body)
    check_total(body.expected_total, plan["total"])
    root = fileops.containing_root(request.app.state.db, Path(body.target) / "example.jpg")
    return request.app.state.operations.run("flatten", f"Flatten {Path(body.target).name}" + (" (prefix)" if body.prefix else ""),
                                             root["id"] if root else None, body.model_dump(), plan["entries"], finalize=flatten.remove_empty)


class RuleRequest(rules.StrictModel):
    rule: rules.Rule
    expected_total: int | None = Field(None, ge=0)


@router.post("/rules/preview")
def rule_preview(body: RuleRequest, request: Request) -> dict:
    result = rules.plan(request.app.state.db, body.rule)
    return {**result, "entries": result["entries"][:2000]}


@router.post("/rules/run")
def rule_run(body: RuleRequest, request: Request) -> dict:
    plan = rules.plan(request.app.state.db, body.rule)
    check_total(body.expected_total, plan["total"])
    if body.rule.action.create_dest:
        access_path(body.rule.action.dest).mkdir(parents=True, exist_ok=True)
    return request.app.state.operations.run("rule", body.rule.name, body.rule.scope.root_id,
                                             body.rule.model_dump(), plan["entries"])


@router.get("/rules")
def presets(request: Request) -> list[dict]:
    return [{**row, "body": json.loads(row["body"])} for row in request.app.state.db.all("SELECT * FROM rules ORDER BY name,id")]


@router.post("/rules")
def save_rule(body: rules.Rule, request: Request) -> dict:
    now = time.time()
    rule_id = request.app.state.db.execute("INSERT INTO rules(name,body,created,updated) VALUES (?,?,?,?)",
                                         (body.name, body.model_dump_json(), now, now))
    return {"id": rule_id, "name": body.name, "body": body.model_dump()}


def require_rule(request: Request, rule_id: int) -> dict:
    row = request.app.state.db.one("SELECT * FROM rules WHERE id=?", (rule_id,))
    if row is None:
        raise HTTPException(404, "Rule not found")
    return row


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, body: rules.Rule, request: Request) -> dict:
    require_rule(request, rule_id)
    request.app.state.db.execute("UPDATE rules SET name=?,body=?,updated=? WHERE id=?",
                                 (body.name, body.model_dump_json(), time.time(), rule_id))
    return {"id": rule_id, "name": body.name, "body": body.model_dump()}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, request: Request) -> dict:
    require_rule(request, rule_id)
    request.app.state.db.execute("DELETE FROM rules WHERE id=?", (rule_id,))
    return {"ok": True}


@router.post("/rules/{rule_id}/run")
def run_preset(rule_id: int, request: Request, body: RuleRequest | None = None) -> dict:
    saved = require_rule(request, rule_id)
    body = body or RuleRequest(rule=rules.Rule.model_validate_json(saved["body"]))
    result = rule_run(body, request)
    request.app.state.db.execute("UPDATE rules SET last_run=? WHERE id=?", (time.time(), rule_id))
    return result
