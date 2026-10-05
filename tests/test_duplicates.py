"""Exact duplicate hashing, cache reuse, stale guards, and journaled resolution."""

import shutil
import time
from pathlib import Path

import pytest

from photo_explorer import duplicates, fileops
from test_operations import open_library
from conftest import wait_scan


def scan(client, root_id):
    result = client.post(f'/api/roots/{root_id}/duplicates/scan', json={"mode": "exact"})
    assert result.status_code == 200
    run_id = result.json()["run_id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = client.get(f'/api/roots/{root_id}/scan').json()
        if not status["dup_running"]:
            assert not status["dup_error"], status
            return client.get(f'/api/roots/{root_id}/duplicates', params={"run_id": run_id}).json()
        time.sleep(.01)
    raise AssertionError('Duplicate scan timed out')


def duplicate_library(client, tmp_path):
    root, root_id = open_library(client, tmp_path, ('a.png', 'different.png'))
    shutil.copy2(root / 'a.png', root / 'copy.png')
    shutil.copy2(root / 'a.png', root / 'copy2.png')
    client.post(f'/api/roots/{root_id}/rescan')
    wait_scan(client, root_id)
    return root, root_id


def test_exact_groups_hash_only_shared_sizes_and_reuse(client, tmp_path, monkeypatch):
    root, root_id = duplicate_library(client, tmp_path)
    db = client.app.state.db
    calls = []
    original = duplicates.sha256
    monkeypatch.setattr(duplicates, 'sha256', lambda path: calls.append(str(path)) or original(path))
    result = scan(client, root_id)
    assert result["total"] == 1
    members = result["groups"][0]["members"]
    assert {member["name"] for member in members} == {'a.png', 'copy.png', 'copy2.png'}
    assert all(member['distance'] == 0 for member in members)
    shared = db.one('SELECT COUNT(*) n FROM images WHERE size IN (SELECT size FROM images GROUP BY size HAVING COUNT(*)>1)')["n"]
    assert len(calls) == shared
    calls.clear()
    assert scan(client, root_id)["total"] == 1
    assert not calls
    assert client.post(f'/api/roots/{root_id}/duplicates/scan', json={"mode": "near"}).status_code == 400


@pytest.mark.parametrize('action', ['move', 'trash'])
def test_resolve_journal_undo_and_confirm(client, tmp_path, monkeypatch, action):
    root, root_id = duplicate_library(client, tmp_path)
    result = scan(client, root_id)
    group = result['groups'][0]
    keep = next(member for member in group['members'] if member['name'] == 'a.png')
    dest = tmp_path / 'duplicates'
    dest.mkdir()
    monkeypatch.setattr(fileops, 'send2trash', lambda path: Path(path).unlink())
    for member in group['members']:
        client.patch(f"/api/images/{member['id']}", json={"rating": 5})
    body = {'group_ids': [group['id']], 'keep': {str(group['id']): keep['id']}, 'action': action, 'dest': str(dest)}
    assert client.post('/api/duplicates/preview', json=body).json()['total'] == 2
    if action == 'trash':
        assert client.post('/api/duplicates/resolve', json=body).status_code == 400
        body['confirm'] = True
    response = client.post('/api/duplicates/resolve', json=body)
    assert response.status_code == 200, response.text
    op = response.json()
    assert op['kind'] == 'duplicates' and op['done'] == 2 and not op['failed']
    wait_scan(client, root_id)
    assert (root / 'a.png').exists() and not (root / 'copy.png').exists()
    undone = client.post(f"/api/operations/{op['operation_id']}/undo")
    if action == 'move':
        assert undone.status_code == 200 and not undone.json()['failed']
        assert (root / 'copy.png').exists()
        for member in group['members']:
            assert client.app.state.db.one('SELECT rating FROM images WHERE id=?', (member['id'],))['rating'] == 5
        wait_scan(client, root_id)
    else:
        assert not op['undoable'] and undone.status_code == 409


def test_changed_duplicate_and_invalid_keeper_refused(client, tmp_path):
    root, root_id = duplicate_library(client, tmp_path)
    group = scan(client, root_id)['groups'][0]
    body = {'group_ids': [group['id']], 'keep': {str(group['id']): 99999}, 'action': 'trash', 'confirm': True}
    assert client.post('/api/duplicates/resolve', json=body).status_code == 400
    (root / 'copy.png').write_bytes(b'changed')
    body['keep'] = 'newest'
    assert client.post('/api/duplicates/resolve', json=body).status_code == 409
    assert (root / 'a.png').exists()
