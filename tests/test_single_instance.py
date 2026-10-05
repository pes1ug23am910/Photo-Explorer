"""Per-home claims and second launches never open desktop UI in tests."""

import json
import os
import time

import pytest

from photo_explorer import single_instance as module
from photo_explorer.single_instance import Instance, forward_launch, pid_alive


def test_create_publish_release(tmp_path):
    owner = Instance(tmp_path)
    assert owner.acquire(9876) is None
    assert json.loads(owner.lock.read_text())["pid"] == os.getpid()
    owner.publish()
    assert json.loads(owner.path.read_text()) == owner.record
    owner.release()
    assert not owner.lock.exists() and not owner.path.exists()


def test_dead_pid_stale_recovery(tmp_path, monkeypatch):
    previous = {"pid": 9999999, "port": 9998, "started": time.time() - 100}
    (tmp_path / "instance.lock").write_text(json.dumps(previous))
    (tmp_path / "instance.json").write_text(json.dumps(previous))
    monkeypatch.setattr(module, "pid_alive", lambda pid: False)
    owner = Instance(tmp_path)
    assert owner.acquire(9999) is None
    assert owner.record["port"] == 9999
    owner.release()


def test_healthy_second_claim_does_not_remove_owner(tmp_path, monkeypatch):
    owner = Instance(tmp_path)
    owner.acquire(9876)
    owner.publish()
    monkeypatch.setattr(module, "request_json", lambda *a, **kw: {"ok": True, "home": str(tmp_path)})
    second = Instance(tmp_path)
    assert second.acquire(9877) == owner.record
    second.release()
    assert owner.lock.exists()
    owner.release()


def test_unresponsive_published_instance_and_corrupt_lock(tmp_path, monkeypatch):
    owner = Instance(tmp_path)
    owner.acquire(9876)
    owner.publish()
    def offline(*args, **kwargs):
        assert kwargs["timeout"] == 1
        raise TimeoutError()
    monkeypatch.setattr(module, "request_json", offline)
    second = Instance(tmp_path)
    assert second.acquire(9877) is None
    owner.release()
    assert second.lock.exists()
    second.release()
    (tmp_path / "instance.lock").write_text("broken")
    assert owner.acquire(9876) is None
    owner.release()


@pytest.mark.parametrize("folder,no_browser", [(None, False), ("pictures", False), (None, True), ("pictures", True)])
def test_second_launch_forwarding(folder, no_browser, tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(module, "request_json", lambda *a, **kw: calls.append((a, kw)) or {"root": {"id": 3}})
    path = str(tmp_path / folder) if folder else None
    assert forward_launch({"port": 9876}, path, no_browser, recursive=False) == 0
    assert capsys.readouterr().out.strip() == "http://127.0.0.1:9876/"
    assert [args[1] for args, _ in calls] == (["/api/roots"] if folder else []) + ([] if no_browser else ["/api/focus?root_id=3" if folder else "/api/focus"])
    if folder:
        assert calls[0][0][2] == {"path": path, "recursive": False}


def test_pid_probe():
    assert pid_alive(os.getpid())
    assert not pid_alive(-1)
    assert not pid_alive(0x7ffffffe)
