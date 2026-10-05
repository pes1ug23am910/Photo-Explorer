"""SQLite storage and one shared ordering/filter definition."""

from __future__ import annotations

import random
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping

SORTS = {
    "mtime": "mtime", "ctime": "ctime", "taken": "sort_date", "name": "name_key",
    "path": "dir COLLATE NOCASE, name_key", "size": "size", "pixels": "pixels",
    "width": "width", "height": "height", "aspect": "aspect", "ext": "ext",
    "camera": "camera COLLATE NOCASE", "rating": "rating", "random": "rnd",
}
NULLABLE_SORTS = {"pixels", "width", "height", "aspect", "camera"}
COMPACT = (
    "id,name,ext,size,mtime,ctime,taken,width,height,orient,rating,favorite,animated,dir,meta_state,"
    "frames,camera,pixels,aspect,sort_date,meta_error"
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS roots (
 id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, recursive INTEGER NOT NULL DEFAULT 1,
 last_scan REAL, created REAL NOT NULL, opened REAL NOT NULL);
CREATE TABLE IF NOT EXISTS images (
 id INTEGER PRIMARY KEY, root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
 path TEXT NOT NULL UNIQUE, display_path TEXT NOT NULL,
 dir TEXT NOT NULL, name TEXT NOT NULL, name_key TEXT NOT NULL, ext TEXT NOT NULL,
 size INTEGER NOT NULL, mtime REAL NOT NULL, ctime REAL NOT NULL,
 width INTEGER, height INTEGER, pixels INTEGER, aspect REAL, orient TEXT,
 taken REAL, sort_date REAL, camera TEXT, lens TEXT, iso INTEGER, fnumber REAL,
 exposure TEXT, focal REAL, gps_lat REAL, gps_lon REAL, exif_orientation INTEGER,
 format TEXT, animated INTEGER NOT NULL DEFAULT 0, frames INTEGER, rnd REAL NOT NULL,
 rating INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0,
 meta_state INTEGER NOT NULL DEFAULT 0, meta_error TEXT, indexed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS destinations (path TEXT PRIMARY KEY, last_used REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cache_keys (root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
 key TEXT NOT NULL, PRIMARY KEY(root_id,key));
"""


def _number(params: Mapping, key: str, default: Any = None, integer: bool = False) -> Any:
    value = params.get(key)
    if value in (None, ""):
        return default
    try:
        result = int(value) if integer else float(value)
        if not float("-inf") < result < float("inf"):
            raise ValueError
        return result
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"Invalid {key}") from exc


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def date_boundary(value: str, end: bool = False) -> float:
    """Parse epochs or local ISO dates; an end date includes the entire day."""
    try:
        number = float(value)
        if not float("-inf") < number < float("inf"):
            raise ValueError
        return number
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value)
            if end and len(value) == 10:
                parsed += timedelta(days=1, microseconds=-1)
            return parsed.timestamp()
        except (ValueError, OverflowError, OSError) as exc:
            raise ValueError("Dates must be ISO dates or epoch seconds") from exc


def build_query(root_id: int, params: Mapping[str, Any]) -> tuple[str, list, str]:
    """Build a parameterized WHERE and a deterministic NULL-last ORDER BY."""
    sort = params.get("sort", "mtime")
    if sort not in SORTS:
        raise ValueError("Unknown sort key")
    order = params.get("order") or ("desc" if sort in {"mtime", "ctime", "taken"} else "asc")
    if order not in {"asc", "desc"}:
        raise ValueError("Order must be asc or desc")
    expressions = SORTS[sort].split(", ")
    ordering = ", ".join(f"{expr} {order.upper()}" for expr in expressions)
    if sort in NULLABLE_SORTS:
        ordering = f"({sort} IS NULL) ASC, " + ordering
    ordering += ", name_key ASC, id ASC"
    clauses = ["root_id = ?"]
    values: list[Any] = [root_id]
    ext = params.get("ext")
    if ext:
        extensions = [part.lower().lstrip(".") for part in str(ext).split(",") if part]
        if extensions:
            clauses.append(f"ext IN ({','.join('?' for _ in extensions)})")
            values.extend(extensions)
    query = params.get("q")
    if query:
        column = "path" if "/" in query or "\\" in query else "name"
        if column == "path":
            import os
            query = str(query).replace("/", os.sep).replace("\\", os.sep)
        clauses.append(f"CASEFOLD({column}) LIKE ? ESCAPE '\\'")
        values.append(f"%{_like(str(query).casefold())}%")
    date_field = params.get("date_field", "mtime")
    if date_field not in {"mtime", "ctime", "taken"}:
        raise ValueError("Invalid date field")
    column = "sort_date" if date_field == "taken" else date_field
    for key, op in (("date_from", ">="), ("date_to", "<=")):
        if params.get(key):
            clauses.append(f"{column} {op} ?")
            values.append(date_boundary(str(params[key]), key == "date_to"))
    for key, col, op in (("size_min", "size", ">="), ("size_max", "size", "<="),
                         ("min_pixels", "pixels", ">="), ("min_rating", "rating", ">=")):
        number = _number(params, key, integer=True)
        if number is not None:
            if number < 0 or (key == "min_rating" and number > 5):
                raise ValueError(f"Invalid {key}")
            clauses.append(f"{col} {op} ?")
            values.append(number)
    if params.get("orient"):
        if params["orient"] not in {"landscape", "portrait", "square"}:
            raise ValueError("Invalid orientation")
        clauses.append("orient = ?")
        values.append(params["orient"])
    for key, sql in (("favorites", "favorite = 1"), ("animated", "animated = 1"),
                     ("has_gps", "gps_lat IS NOT NULL AND gps_lon IS NOT NULL"),
                     ("meta_failed", "meta_state = 2")):
        if str(params.get(key, "0")) == "1":
            clauses.append(sql)
    if params.get("camera"):
        clauses.append("CASEFOLD(camera) LIKE ? ESCAPE '\\'")
        values.append(f"%{_like(str(params['camera']).casefold())}%")
    if params.get("dir"):
        import os
        from .config import canonical
        directory = canonical(params["dir"]).rstrip("/\\")
        clauses.append("(dir = ? COLLATE NOCASE OR dir LIKE ? ESCAPE '\\' COLLATE NOCASE)")
        values.extend([directory, _like(directory + os.sep) + "%"])
    return " AND ".join(clauses), values, ordering


class Database:
    """Short-lived connections keep SQLite handles local to their calling thread."""

    def __init__(self, home: Path):
        self.path = home / "index.db"
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            for column in ("mtime", "ctime", "name_key", "size", "sort_date", "pixels", "ext", "rnd", "dir", "meta_state"):
                connection.execute(f"CREATE INDEX IF NOT EXISTS idx_{column} ON images(root_id,{column})")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection with foreign keys and a bounded busy wait."""
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("CASEFOLD", 1, lambda value: value.casefold() if value is not None else None, deterministic=True)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def all(self, sql: str, values: tuple | list = ()) -> list[dict]:
        """Fetch records as dictionaries."""
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, values)]

    def one(self, sql: str, values: tuple | list = ()) -> dict | None:
        """Fetch one record, if present."""
        with self.connect() as connection:
            row = connection.execute(sql, values).fetchone()
            return dict(row) if row else None

    def execute(self, sql: str, values: tuple | list = ()) -> int:
        """Commit one statement and return its last inserted id."""
        with self.connect() as connection:
            return connection.execute(sql, values).lastrowid or 0

    def listing(self, root_id: int, params: Mapping) -> dict:
        """Return one page and its count from the same read snapshot."""
        where, values, order = build_query(root_id, params)
        offset = _number(params, "offset", 0, True)
        limit = _number(params, "limit", 200, True)
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("offset must be >= 0 and limit must be 1–500")
        with self.connect() as connection:
            connection.execute("BEGIN")
            total = connection.execute(f"SELECT COUNT(*) FROM images WHERE {where}", values).fetchone()[0]
            items = connection.execute(
                f"SELECT {COMPACT} FROM images WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                [*values, limit, offset],
            )
            return {"total": total, "offset": offset, "items": [dict(row) for row in items]}

    def position(self, root_id: int, image_id: int, params: Mapping) -> int | None:
        """Find the zero-based position using exactly the listing query."""
        where, values, order = build_query(root_id, params)
        row = self.one(
            f"SELECT position FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY {order}) - 1 AS position "
            f"FROM images WHERE {where}) WHERE id = ?", [*values, image_id],
        )
        return row["position"] if row else None

    def shuffle(self, root_id: int, seed: int | None = None) -> int:
        """Persist a stable shuffle, reproducible by seed for an unchanged library."""
        chosen = secrets.randbelow(2**53) if seed is None else seed
        generator = random.Random(chosen)
        with self.connect() as connection:
            ids = connection.execute("SELECT id FROM images WHERE root_id=? ORDER BY path", (root_id,)).fetchall()
            connection.executemany("UPDATE images SET rnd=? WHERE id=?", [(generator.random(), row[0]) for row in ids])
        return chosen

    def stats(self, root_id: int, params: Mapping) -> dict:
        """Aggregate the filtered result without loading the image records."""
        where, values, _ = build_query(root_id, params)
        date = "sort_date" if params.get("date_field") == "taken" else params.get("date_field", "mtime")
        with self.connect() as connection:
            connection.execute("BEGIN")
            summary = dict(connection.execute(
                f"SELECT COUNT(*) total, COALESCE(SUM(size),0) bytes, "
                f"COALESCE(SUM(rating>0),0) rated, COALESCE(SUM(favorite),0) favorites FROM images WHERE {where}", values,
            ).fetchone())
            for column, label in (("ext", "by_ext"), ("orient", "by_orient")):
                summary[label] = {row[0] or "unknown": row[1] for row in connection.execute(
                    f"SELECT {column}, COUNT(*) FROM images WHERE {where} GROUP BY {column}", values)}
            summary["by_month"] = [dict(row) for row in connection.execute(
                f"SELECT strftime('%Y-%m',{date},'unixepoch','localtime') ym, COUNT(*) n "
                f"FROM images WHERE {where} GROUP BY ym ORDER BY ym", values)]
            return summary
