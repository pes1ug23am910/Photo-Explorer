"""Desktop log rotation, exception hooks, and bounded diagnostics."""

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace

from photo_explorer import __version__
from photo_explorer.logging_setup import setup_logging, close_logging, log_tail


def test_file_rotation_and_frozen_stderr(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    path = setup_logging(tmp_path, verbose=True)
    try:
        owned = [h for h in logging.getLogger().handlers if getattr(h, "photo_explorer", False)]
        assert len(owned) == 1 and isinstance(owned[0], RotatingFileHandler)
        handler = owned[0]
        assert handler.maxBytes == 2 * 1024**2 and handler.backupCount == 4
        assert handler.encoding == "utf-8" and handler.level == logging.DEBUG
        logging.getLogger("photo_explorer.test").info("Unicode café 日本")
        assert "Unicode café 日本" in path.read_text(encoding="utf-8")
        assert logging.getLogger("uvicorn.access").level == logging.DEBUG
        assert logging.getLogger("PIL").level == logging.WARNING
        handler.doRollover()
        assert path.with_suffix(".log.1").is_file()
    finally:
        close_logging(path)


def test_exception_hooks_and_access_level(tmp_path):
    path = setup_logging(tmp_path)
    try:
        assert logging.getLogger("uvicorn.access").level == logging.WARNING
        try:
            raise RuntimeError("intentional hook test")
        except RuntimeError:
            kind, value, trace = sys.exc_info()
            sys.excepthook(kind, value, trace)
            threading.excepthook(SimpleNamespace(exc_type=kind, exc_value=value, exc_traceback=trace,
                                                thread=threading.current_thread()))
        text = path.read_text(encoding="utf-8")
        assert "Unhandled exception" in text and "Unhandled thread exception" in text
    finally:
        close_logging(path)


def test_diagnostics_tail_is_bounded(client):
    for number in range(230):
        logging.getLogger("photo_explorer.test").info("line %s", number)
    result = client.get("/api/diagnostics").json()
    assert result["version"] == __version__
    assert result["frozen"] is False
    assert result["home"] == str(client.app.state.home)
    assert result["images"] == result["roots"] == 0
    assert result["started"] > 0 and result["last_backup"]
    assert len(result["log_tail"]) == 200
    assert result["log_tail"][-1].endswith("line 229")
    assert "cap_bytes" in result["cache"] and "mode" in result["watcher"]


def test_missing_tail_and_open_folder(client, tmp_path, monkeypatch):
    import subprocess
    assert log_tail(tmp_path / "missing.log") == []
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    assert client.post("/api/diagnostics/open-log-folder").json()["ok"]
    assert calls[0][0] == ["explorer.exe", str(client.app.state.log_path.parent)]
    assert calls[0][1]["timeout"] == 15
