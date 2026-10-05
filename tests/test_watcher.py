"""Synthetic events verify watcher updates without timing-dependent sleeps."""

from pathlib import Path
from PIL import Image
from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileDeletedEvent, FileMovedEvent

from photo_explorer.config import canonical
from photo_explorer.watcher import Handler, Watcher
from test_operations import open_library


def test_created_modified_moved_deleted_and_revision(client, tmp_path):
    root, root_id = open_library(client, tmp_path)
    watcher = client.app.state.watcher
    watcher.forget(root_id)
    handler = Handler(watcher, root_id)
    db = client.app.state.db
    original = client.get(f'/api/roots/{root_id}/scan').json()["revision"]
    path = root / 'created.png'
    with Image.new('RGB', (20, 30)) as image:
        image.save(path)
    handler.on_any_event(FileCreatedEvent(str(path)))
    watcher.apply(root_id)
    row = db.one('SELECT * FROM images WHERE path=?', (canonical(path),))
    assert row and row["width"] == 20
    db.execute('UPDATE images SET rating=4,favorite=1 WHERE id=?', (row['id'],))
    with Image.new('RGB', (90, 60)) as image:
        image.save(path)
    handler.on_any_event(FileModifiedEvent(str(path)))
    watcher.apply(root_id)
    assert db.one('SELECT width FROM images WHERE id=?', (row['id'],))["width"] == 90
    dest = root / 'renamed.png'
    path.rename(dest)
    handler.on_any_event(FileMovedEvent(str(path), str(dest)))
    watcher.apply(root_id)
    moved = db.one('SELECT * FROM images WHERE id=?', (row['id'],))
    assert moved["path"] == canonical(dest) and moved["rating"] == 4 and moved["favorite"] == 1
    dest.unlink()
    handler.on_any_event(FileDeletedEvent(str(dest)))
    watcher.apply(root_id)
    assert not db.one('SELECT id FROM images WHERE id=?', (row['id'],))
    assert client.get(f'/api/roots/{root_id}/scan').json()["revision"] == original + 4
    note = root / 'notes.txt'
    note.write_text('other file')
    handler.on_any_event(FileCreatedEvent(str(note)))
    watcher.apply(root_id)
    assert client.get(f'/api/roots/{root_id}/dirs').json()[0]["files_direct"] == 1


def test_own_changes_hidden_temp_and_home_ignored(client, tmp_path):
    root, root_id = open_library(client, tmp_path)
    watcher = client.app.state.watcher
    watcher.forget(root_id)
    watcher.mark_self([str(root / 'a.jpg')])
    for path in [root / 'a.jpg', root / '.hidden.jpg', root / '~temp.jpg', root / 'file.tmp', client.app.state.home / 'image.jpg']:
        watcher.handle(root_id, FileModifiedEvent(str(path)))
    assert not watcher.pending


def test_over_200_paths_schedule_incremental_scan(client, tmp_path, monkeypatch):
    root, root_id = open_library(client, tmp_path)
    watcher = client.app.state.watcher
    watcher.forget(root_id)
    starts = []
    monkeypatch.setattr(client.app.state.scanner, 'start', starts.append)
    for index in range(201):
        watcher.handle(root_id, FileCreatedEvent(str(root / f'{index}.jpg')))
    watcher.apply(root_id)
    assert starts == [root_id]


def test_native_failure_uses_polling_fallback(client, monkeypatch):
    import photo_explorer.watcher as module
    attempts = []
    class FakeObserver:
        def start(self):
            attempts.append('polling')
        def stop(self):
            pass
        def join(self, timeout):
            pass
        def is_alive(self):
            return False
    class FailedObserver(FakeObserver):
        def start(self):
            attempts.append('native')
            raise OSError('unsupported native watch')
    monkeypatch.setattr(module, 'Observer', FailedObserver)
    monkeypatch.setattr(module, 'PollingObserver', FakeObserver)
    watcher = Watcher(client.app.state.db, client.app.state.scanner, client.app.state.cache_policy)
    watcher.configure()
    assert attempts == ['native', 'polling'] and watcher.error is None
    watcher.close()
