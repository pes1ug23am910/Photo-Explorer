"""Bundled resources and picker routing without showing a native dialog."""

import subprocess
import sys

import pytest

from photo_explorer.config import resource_path
from photo_explorer.routes_fs import pick


def test_resource_path_source():
    assert resource_path("static/index.html").is_file()


def test_resource_path_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resource_path("static/index.html") == tmp_path / "static/index.html"


@pytest.mark.parametrize("frozen", [False, True])
def test_picker_argv(frozen, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, str(tmp_path) + "\n", "")
    monkeypatch.setattr(subprocess, "run", run)
    assert pick() == {"path": str(tmp_path)}
    argv, kwargs = calls[0]
    assert argv == [sys.executable, *([] if frozen else ["-m", "photo_explorer"]), "--pick-folder"]
    assert kwargs["timeout"] == 300 and kwargs["encoding"] == "utf-8"
    assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert not kwargs.get("shell")


def test_picker_cancel(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "\n", ""))
    assert pick() == {"path": None}


def test_internal_modes_before_server_import(monkeypatch, capsys):
    import builtins
    from photo_explorer import picker
    from photo_explorer.__main__ import main
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert name not in {"uvicorn", "app", "single_instance", "tray"}
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    calls = []
    monkeypatch.setattr(picker, "pick_folder", lambda title: calls.append(title) or 0)
    assert main(["--pick-folder", "--title", "Choose photos"]) == 0
    assert calls == ["Choose photos"]
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "0.3.0"
