"""Validated rule conditions, scoped candidates, and collision-aware plans."""

from __future__ import annotations

import fnmatch
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import EXTENSIONS, access_path, canonical, inside, visible_name
from .db import Database, build_query, date_boundary
from .transfers import Names

OPERATORS = {
    "name": {"contains", "starts", "ends", "glob", "regex", "suffix", "prefix"},
    "ext": {"in"}, "kind": {"in"}, "size": {"lt", "gt", "between"},
    "width": {"lt", "gt", "between"}, "height": {"lt", "gt", "between"},
    "pixels": {"lt", "gt", "between"}, "orient": {"is"},
    "mtime": {"before", "after", "between"}, "ctime": {"before", "after", "between"},
    "taken": {"before", "after", "between"}, "rating": {"gte", "eq"},
    "favorite": {"is"}, "camera": {"contains"}, "meta_failed": {"is"}, "dir": {"is", "under"},
}
FILE_FIELDS = {"name", "ext", "size", "mtime", "ctime", "dir"}
NUMBER_FIELDS = {"size", "width", "height", "pixels", "rating"}
DATE_FIELDS = {"mtime", "ctime", "taken"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Condition(StrictModel):
    field: str
    op: str
    value: Any

    @model_validator(mode="after")
    def validate_condition(self) -> Condition:
        if self.op not in OPERATORS.get(self.field, set()):
            raise ValueError("Unsupported rule field/operator")
        if self.field in NUMBER_FIELDS | DATE_FIELDS:
            values = self.value if self.op == "between" else [self.value]
            if not isinstance(values, list) or len(values) != (2 if self.op == "between" else 1):
                raise ValueError("between requires a two-value range")
            numbers = []
            for value in values:
                number = date_boundary(str(value)) if self.field in DATE_FIELDS else float(value)
                if isinstance(value, bool) or not math.isfinite(number) or (self.field in NUMBER_FIELDS and number < 0):
                    raise ValueError("Rule numbers must be finite and nonnegative")
                numbers.append(number)
            if len(numbers) == 2 and numbers[0] > numbers[1]:
                raise ValueError("Range start must precede its end")
            self.value = numbers if self.op == "between" else numbers[0]
        elif self.field in {"favorite", "meta_failed"}:
            if not isinstance(self.value, bool):
                raise ValueError("This condition requires true or false")
        elif self.op == "in":
            if not isinstance(self.value, list) or not self.value or not all(isinstance(v, str) for v in self.value):
                raise ValueError("in requires a nonempty list of strings")
            self.value = [value.casefold().lstrip(".") for value in self.value]
            if self.field == "kind" and not set(self.value) <= {"raster", "animated", "vector", "rendered"}:
                raise ValueError("Unknown image kind")
        else:
            if not isinstance(self.value, str) or len(self.value) > 2000:
                raise ValueError("This condition requires text of at most 2000 characters")
            if self.field == "orient" and self.value not in {"landscape", "portrait", "square"}:
                raise ValueError("Unknown orientation")
            if self.op == "regex":
                try:
                    re.compile(self.value, re.IGNORECASE)
                except re.error as exc:
                    raise ValueError(f"Invalid regular expression: {exc}") from exc
        return self


class Scope(StrictModel):
    root_id: int | None = None
    dir: str | None = None
    dir_mode: Literal["subtree", "exact"] = "subtree"
    include_other_files: bool = False
    ids: list[int] | None = None


class Action(StrictModel):
    type: Literal["move", "copy"] = "move"
    dest: str
    collision: Literal["rename", "skip", "skip_identical"] = "rename"
    create_dest: bool = True


class Rule(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    scope: Scope
    match: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(min_length=1, max_length=50)
    action: Action


def matches(row: dict, condition: Condition, root_path: str) -> bool:
    """Evaluate a condition without constructing SQL from rule input."""
    field, op, wanted = condition.field, condition.op, condition.value
    if row.get("other_file") and field not in FILE_FIELDS:
        return False
    actual = row.get(field)
    if field == "kind":
        actual = "vector" if row["ext"] == "svg" else "animated" if row.get("animated") else "rendered" if row["ext"] in {"heic", "heif", "tif", "tiff", "psd"} else "raster"
    elif field == "meta_failed":
        actual = row.get("meta_state") == 2
    elif field == "dir":
        wanted_path = Path(root_path) / wanted.replace("\\", os.sep).replace("/", os.sep)
        return canonical(actual) == canonical(wanted_path) if op == "is" else inside(actual, wanted_path)
    if actual is None:
        return False
    if op == "in":
        return str(actual).casefold() in wanted
    if field in NUMBER_FIELDS | DATE_FIELDS:
        if op in {"lt", "before"}:
            return actual < wanted
        if op in {"gt", "after"}:
            return actual > wanted
        if op == "between":
            return wanted[0] <= actual <= wanted[1]
        return actual >= wanted if op == "gte" else actual == wanted
    if op == "is":
        return actual == wanted
    actual = str(actual)
    if op == "regex":
        return re.search(wanted, actual, re.IGNORECASE) is not None
    actual, wanted = actual.casefold(), str(wanted).casefold()
    if op == "contains":
        return wanted in actual
    if op == "glob":
        return fnmatch.fnmatchcase(actual, wanted)
    if op in {"suffix", "prefix"}:
        actual = os.path.splitext(actual)[0]
    return actual.startswith(wanted) if op in {"starts", "prefix"} else actual.endswith(wanted)


def candidates(db: Database, rule: Rule) -> tuple[dict, list[dict]]:
    """Read indexed candidates through the shared scope query, with optional files."""
    root = db.one("SELECT * FROM roots WHERE id=?", (rule.scope.root_id,))
    if root is None:
        raise ValueError("Choose an indexed library for this rule")
    directory = Path(root["path"] if rule.scope.ids is not None else rule.scope.dir or root["path"])
    if not directory.is_absolute():
        directory = Path(root["path"]) / directory
    if not inside(directory.resolve(), Path(root["path"]).resolve()):
        raise ValueError("Rule scope must be inside its library")
    where, values, ordering = build_query(root["id"], {"dir": str(directory), "dir_mode": "subtree" if rule.scope.ids is not None else rule.scope.dir_mode})
    rows = db.all(f"SELECT * FROM images WHERE {where} ORDER BY {ordering}", values)
    if rule.scope.ids is not None:
        ids = set(rule.scope.ids)
        return root, [row for row in rows if row["id"] in ids]
    if rule.scope.include_other_files:
        def fail(error: OSError) -> None:
            raise error
        for current, dirs, files in os.walk(access_path(directory), onerror=fail):
            folder = directory / Path(current).relative_to(access_path(directory))
            dirs[:] = [name for name in dirs if visible_name(name) and not access_path(folder / name).is_symlink()
                       and not access_path(folder / name).is_junction()] if rule.scope.dir_mode == "subtree" and root["recursive"] else []
            for name in sorted(files):
                path = folder / name
                ext = path.suffix.lower().lstrip(".")
                if not visible_name(name) or ext in EXTENSIONS or access_path(path).is_symlink():
                    continue
                stat = access_path(path).stat()
                rows.append({"path": canonical(path), "display_path": str(path), "name": name, "ext": ext,
                             "dir": canonical(folder), "size": stat.st_size, "mtime": stat.st_mtime,
                             "ctime": getattr(stat, "st_birthtime", stat.st_ctime), "other_file": True})
    return root, list({row["path"]: row for row in rows}.values())


def plan(db: Database, rule: Rule) -> dict:
    """Evaluate and reserve destinations without modifying the filesystem."""
    root, rows = candidates(db, rule)
    dest = Path(rule.action.dest)
    if not dest.is_absolute() or ".." in dest.parts:
        raise ValueError("Destination must be an absolute directory without '..'")
    if access_path(dest).exists() and not access_path(dest).is_dir():
        raise ValueError("Destination is not a directory")
    if not access_path(dest).exists() and not rule.action.create_dest:
        raise ValueError("Destination does not exist; enable Create destination")
    matched = [row for row in rows if (all if rule.match == "all" else any)(matches(row, condition, root["path"]) for condition in rule.conditions)]
    if any(canonical(row["dir"]) == canonical(dest) for row in matched):
        raise ValueError("A matching file already lives in the destination; choose another folder")
    names = Names(dest)
    entries = []
    for row in matched:
        src = Path(row["display_path"])
        target, decision, note = names.choose(src, row["name"], rule.action.collision)
        entries.append({"src": str(src), "path": str(src), "dest": str(target), "dest_name": target.name,
                        "image_id": row.get("id"), "name": row["name"], "size": row["size"], "mtime": row["mtime"],
                        "action": "skipped" if decision == "skipped" else "moved" if rule.action.type == "move" else "copied", "note": note})
    return {"total": len(entries), "bytes": sum(row["size"] for row in entries), "entries": entries}


def seed_presets(db: Database) -> None:
    """Insert deletable starter templates exactly once for each home."""
    if db.one("SELECT value FROM settings WHERE key='starter_rules'"):
        return
    presets = [
        ("Suffix -t", [{"field": "name", "op": "suffix", "value": "-t"}]),
        ("Larger than 5 MB", [{"field": "size", "op": "gt", "value": 5 * 1024**2}]),
        ("Smaller than 100 KB", [{"field": "size", "op": "lt", "value": 100 * 1024}]),
        ("Animated images", [{"field": "kind", "op": "in", "value": ["animated"]}]),
        ("PNG screenshots", [{"field": "ext", "op": "in", "value": ["png"]}, {"field": "name", "op": "contains", "value": "screenshot"}]),
    ]
    with db.connect() as connection:
        for name, conditions in presets:
            body = {"name": name, "scope": {}, "match": "all", "conditions": conditions,
                    "action": {"type": "move", "dest": "", "collision": "rename", "create_dest": True}}
            connection.execute("INSERT INTO rules(name,body,created,updated) VALUES (?,?,?,?)",
                               (name, json.dumps(body), time.time(), time.time()))
        connection.execute("INSERT INTO settings(key,value) VALUES ('starter_rules','1')")
