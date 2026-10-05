"""Cache accounting, deferred touches, eviction protection, and idle warming."""

import sqlite3
import time

from photo_explorer.config import save_settings
from test_operations import open_library


def add_key(client, root_id, number, size, used):
    policy = client.app.state.cache_policy
    key = f"{number:040x}"
    path = policy.path(key, "256.jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    client.app.state.db.execute("INSERT INTO cache_keys VALUES (?,?,?,?,?)", (root_id, key, "256.jpg", size, used))
    return key, path


def test_eviction_lru_recent_and_viewer_protection(client, tmp_path):
    _, root_id = open_library(client, tmp_path)
    policy = client.app.state.cache_policy
    policy.last_request = time.time() + 100
    policy.clear()
    save_settings(client.app.state.db, {"cacheMaxMB": 1, "prewarm": False})
    now = time.time()
    _, oldest = add_key(client, root_id, 1, 600000, now - 2000)
    protected_key, protected = add_key(client, root_id, 2, 600000, now - 1500)
    _, newer = add_key(client, root_id, 3, 600000, now - 1000)
    recent_key, recent = add_key(client, root_id, 4, 600000, now - 100)
    policy.protected.add(protected_key)
    policy.evict(now)
    assert not oldest.exists() and not newer.exists()
    assert protected.exists() and recent.exists()
    assert policy.stats()["bytes"] == 1200000
    policy.protected.clear()
    policy.touch(root_id, protected_key, "256.jpg")
    policy.evict(now)
    assert protected.exists()
    assert client.post('/api/cache/clear', json={"root_id": root_id}).json()["files"] == 0


def test_cache_hit_read_only_touch_flush_orphan_and_stats(client, tmp_path, monkeypatch):
    _, root_id = open_library(client, tmp_path)
    cache, policy, db = client.app.state.cache, client.app.state.cache_policy, client.app.state.db
    policy.last_request = time.time() + 100
    row = db.one("SELECT * FROM images ORDER BY id LIMIT 1")
    path, key = cache.render(row, 256)
    original = db.execute
    calls = []
    monkeypatch.setattr(db, "execute", lambda *args: calls.append(args) or original(*args))
    cache.render(row, 256)
    assert not calls
    assert (root_id, key, "256.jpg") in policy.touched
    policy.flush()
    assert not policy.touched
    stats = client.get('/api/cache').json()
    assert stats["bytes"] == path.stat().st_size and stats["files"] == 1
    orphan = policy.path('f' * 40, '256.jpg')
    orphan.parent.mkdir(exist_ok=True)
    orphan.write_bytes(b'orphan')
    policy.evict()
    assert not orphan.exists()
    assert client.post('/api/cache/clear').json()["bytes"] == 0


def test_prewarm_pauses_for_load_and_cap(client, tmp_path):
    _, root_id = open_library(client, tmp_path)
    policy = client.app.state.cache_policy
    rows = client.app.state.db.all("SELECT * FROM images ORDER BY mtime DESC")
    policy.last_request = time.time() + 100
    policy.clear()
    assert policy.warm_batch(rows) == 0
    save_settings(client.app.state.db, {"cacheMaxMB": 0})
    policy.last_request = time.time() - 2
    assert policy.warm_batch(rows) == 0
    assert policy.stats()["bytes"] == 0
    save_settings(client.app.state.db, {"cacheMaxMB": 1})
    assert policy.warm_batch(rows) == 2
    assert policy.stats()["bytes"] <= 1024**2
    assert client.get(f'/api/roots/{root_id}/scan').json()["prewarm_total"] >= 0


def test_backup_uses_readable_sqlite_and_keeps_seven(client):
    backups = client.app.state.backups
    for day in range(1, 10):
        (backups.directory / f'index-200001{day:02d}.db').touch()
    backups.backup()
    assert len(list(backups.directory.glob('index-*.db'))) == 7
    with sqlite3.connect(backups.last_backup) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert client.get('/api/health').json()["last_backup"] == backups.last_backup
