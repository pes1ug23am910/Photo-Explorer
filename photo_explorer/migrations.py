"""Additive, repeatable database upgrades."""

import sqlite3

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS operations (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL, root_id INTEGER,
 params TEXT NOT NULL, started REAL NOT NULL, finished REAL, status TEXT NOT NULL,
 total INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0,
 failed INTEGER NOT NULL DEFAULT 0, undoable INTEGER NOT NULL DEFAULT 1,
 undone_by INTEGER, undoes INTEGER);
CREATE TABLE IF NOT EXISTS operation_entries (
 id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
 seq INTEGER NOT NULL, action TEXT NOT NULL, src TEXT NOT NULL, dest TEXT,
 image_id INTEGER, size INTEGER, mtime REAL, error TEXT);
CREATE INDEX IF NOT EXISTS operation_entry_order ON operation_entries(op_id,seq);
CREATE TABLE IF NOT EXISTS operation_images (
 op_id INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
 seq INTEGER NOT NULL, image_id INTEGER NOT NULL, body TEXT NOT NULL,
 PRIMARY KEY(op_id,seq));
CREATE INDEX IF NOT EXISTS operation_image_ids ON operation_images(image_id);
CREATE TABLE IF NOT EXISTS rules (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, body TEXT NOT NULL,
 created REAL NOT NULL, updated REAL NOT NULL, last_run REAL);
CREATE TABLE IF NOT EXISTS folder_counts (
 root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
 path TEXT NOT NULL, images_direct INTEGER NOT NULL, files_direct INTEGER NOT NULL,
 subfolders INTEGER NOT NULL, PRIMARY KEY(root_id,path));
CREATE TABLE IF NOT EXISTS hashes (
 image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
 size INTEGER, mtime REAL, sha256 TEXT, dhash TEXT, computed REAL);
CREATE TABLE IF NOT EXISTS dup_runs (
 id INTEGER PRIMARY KEY, root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
 mode TEXT NOT NULL, threshold INTEGER NOT NULL, started REAL NOT NULL, finished REAL,
 groups INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'running', error TEXT);
CREATE TABLE IF NOT EXISTS dup_groups (
 id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES dup_runs(id) ON DELETE CASCADE,
 kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dup_members (
 group_id INTEGER NOT NULL REFERENCES dup_groups(id) ON DELETE CASCADE,
 image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
 distance INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(group_id,image_id));
"""


def migrate(connection: sqlite3.Connection) -> None:
    """Open older libraries without changing their image records."""
    connection.executescript(SCHEMA_V2)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(roots)")}
    if "revision" not in columns:
        connection.execute("ALTER TABLE roots ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
    cache_columns = {row[1] for row in connection.execute("PRAGMA table_info(cache_keys)")}
    if "kind" not in cache_columns:
        connection.executescript("""
        CREATE TABLE cache_keys_v2 (
         root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
         key TEXT NOT NULL, kind TEXT NOT NULL, bytes INTEGER, last_used REAL,
         PRIMARY KEY(root_id,key,kind));
        INSERT INTO cache_keys_v2(root_id,key,kind,bytes,last_used)
         SELECT root_id,key,'legacy',0,0 FROM cache_keys;
        ALTER TABLE cache_keys RENAME TO cache_keys_legacy;
        ALTER TABLE cache_keys_v2 RENAME TO cache_keys;
        """)
    connection.execute("CREATE INDEX IF NOT EXISTS cache_lru ON cache_keys(last_used)")
    connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('schema_version','2')")
