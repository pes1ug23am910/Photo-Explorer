"""Loopback diagnostics and desktop control endpoints."""

import platform
import subprocess
import sys

from fastapi import APIRouter, Request

from . import __version__
from .logging_setup import log_tail

router = APIRouter(prefix="/api")


@router.get("/diagnostics")
def diagnostics(request: Request) -> dict:
    state = request.app.state
    watcher = state.watcher
    with watcher.config_lock, watcher.guard:
        watch = {"enabled": watcher.enabled(), "mode": type(watcher.observer).__name__ if watcher.observer else None,
                 "roots": len(watcher.watches), "pending": len(watcher.pending), "error": watcher.error}
    return {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
            "python": platform.python_version(), "executable": sys.executable,
            "home": str(state.home), "port": state.port or request.url.port, "started": state.started,
            "roots": state.db.one("SELECT COUNT(*) n FROM roots")["n"],
            "images": state.db.one("SELECT COUNT(*) n FROM images")["n"],
            "cache": state.cache_policy.stats(), "watcher": watch,
            "last_backup": state.backups.last_backup, "log_path": str(state.log_path),
            "log_tail": log_tail(state.log_path)}


@router.post("/diagnostics/open-log-folder")
def open_log_folder(request: Request) -> dict:
    subprocess.run(["explorer.exe", str(request.app.state.log_path.parent)], timeout=15, check=False)
    return {"ok": True}


@router.post("/heartbeat")
def heartbeat(request: Request) -> dict:
    request.app.state.lifecycle.heartbeat()
    windows = getattr(request.app.state, "windows", None)
    return {"ok": True, "command": windows.page_command if windows else None}


@router.post("/window-closed")
def window_closed(request: Request) -> dict:
    request.app.state.lifecycle.window_closed()
    return {"ok": True}


@router.post("/shutdown")
def shutdown(request: Request) -> dict:
    request.app.state.lifecycle.shutdown("Quit requested through API")
    return {"ok": True}


@router.post("/focus")
def focus(request: Request, diagnostics: bool = False, root_id: int | None = None) -> dict:
    fragment = "#diagnostics" if diagnostics else f"#root={root_id}" if root_id is not None else ""
    request.app.state.lifecycle.on_focus(fragment)
    return {"ok": True}
