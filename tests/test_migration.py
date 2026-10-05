"""Compatibility with the original on-disk schema."""

BASELINE_SCHEMA = """
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


def test_baseline_database_opens_migrates_and_lists(isolated_home, tmp_path):
    import sqlite3
    from fastapi.testclient import TestClient
    from photo_explorer.app import create_app
    from photo_explorer.config import canonical
    from photo_explorer.db import Database
    import shutil

    original = tmp_path / "baseline.db"
    root = tmp_path / "photos"
    root.mkdir()
    connection = sqlite3.connect(original)
    connection.executescript(BASELINE_SCHEMA)
    connection.execute("INSERT INTO roots(id,path,created,opened) VALUES (1,?,1,1)", (canonical(root),))
    connection.execute("INSERT INTO images(id,root_id,path,display_path,dir,name,name_key,ext,size,mtime,ctime,rnd,rating,favorite,indexed_at) "
                       "VALUES (17,1,?,?,?,'old.jpg','old.jpg','jpg',123,10,10,0.42,5,1,10)",
                       (canonical(root / "old.jpg"), str(root / "old.jpg"), canonical(root)))
    connection.execute("INSERT INTO cache_keys VALUES (1,'old-key')")
    connection.commit()
    connection.close()
    isolated_home.mkdir()
    shutil.copy2(original, isolated_home / "index.db")
    with TestClient(create_app()) as client:
        response = client.get("/api/roots/1/images")
        assert response.status_code == 200
        row = response.json()["items"][0]
        assert (row["id"], row["rating"], row["favorite"]) == (17, 5, 1)
        assert client.app.state.db.one("SELECT rnd FROM images WHERE id=17")["rnd"] == .42
        assert client.app.state.db.one("SELECT value FROM settings WHERE key='schema_version'")["value"] == '2'
        assert client.app.state.db.one("SELECT key FROM cache_keys WHERE key='old-key'")
        Database(isolated_home)
        assert client.get("/api/roots/1/images").json()["total"] == 1
