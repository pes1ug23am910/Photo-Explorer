"""Application assembly and local-only request protections."""

from __future__ import annotations

import subprocess
import logging
import platform
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import home_directory, load_settings, save_settings, resource_path
from .logging_setup import setup_logging, close_logging
from .lifecycle import Lifecycle
from .db import Database
from .fileops import ConflictError
from .scanner import Scanner
from .thumbs import ThumbnailCache
from .operations import Operations
from .cache_policy import CachePolicy
from .backups import Backups
from .duplicates import Duplicates
from .watcher import Watcher
from .rules import seed_presets
from . import routes_fs, routes_images, routes_roots, routes_ops, routes_cache, routes_duplicates, routes_desktop

log = logging.getLogger(__name__)


def create_app(home: str | Path | None = None, *, lifecycle: Lifecycle | None = None,
               port: int | None = None, verbose: bool = False) -> FastAPI:
    """Construct an isolated local server and its background indexing services."""
    directory = home_directory(home)
    log_path = setup_logging(directory, verbose)
    lifecycle = lifecycle or Lifecycle()
    db = Database(directory)
    scanner = Scanner(db)
    operations = Operations(db, scanner)
    seed_presets(db)
    cache = ThumbnailCache(directory, db)
    policy = CachePolicy(db, cache, scanner)
    backups = Backups(db)
    watcher = Watcher(db, scanner, policy)
    duplicates = Duplicates(db, scanner, operations)
    operations.self_change = watcher.mark_self
    scanner.on_complete = policy.schedule

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("Startup version=%s frozen=%s python=%s home=%s port=%s", __version__,
                 bool(getattr(sys, "frozen", False)), platform.python_version(), directory, port)
        try:
            backups.start()
            policy.start()
            watcher.start()
            lifecycle.start()
            yield
        finally:
            if not lifecycle.reason:
                lifecycle.shutdown("server stopped")
            # Signal every worker before joining any worker that might own a root lock.
            scanner.stopping.set()
            duplicates.stopping.set()
            policy.stopping.set()
            backups.stop.set()
            for row in db.all("SELECT id FROM operations WHERE status='running'"):
                operations.cancelled.add(row["id"])
            for service in (watcher, duplicates, operations, scanner, policy, backups, lifecycle):
                try:
                    service.close()
                except Exception:
                    log.exception("Shutdown cleanup failed: %s", type(service).__name__)
            log.info("Shutdown complete: %s", lifecycle.reason)
            close_logging(log_path)

    app = FastAPI(title="Photo Explorer", version=__version__, lifespan=lifespan)
    app.state.home = directory
    app.state.db = db
    app.state.scanner = scanner
    app.state.operations = operations
    app.state.cache = cache
    app.state.cache_policy = policy
    app.state.watcher = watcher
    app.state.backups = backups
    app.state.duplicates = duplicates
    app.state.lifecycle = lifecycle
    app.state.port = port
    app.state.started = time.time()
    app.state.log_path = log_path

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        policy.last_request = time.time()
        host = request.url.hostname
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "Only local hosts are allowed"}, status_code=403)
        if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse({"detail": "Only loopback clients are allowed"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            return JSONResponse({"detail": "Cross-origin requests are not allowed"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site requests are not allowed"}, status_code=403)
        response = await call_next(request)
        policy.last_request = time.time()
        if request.url.path.startswith("/static/"):
            # Modules must be revalidated on every load so an updated build never runs stale scripts.
            response.headers["Cache-Control"] = "no-cache"
        if request.url.path == "/":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
            )
        return response

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=409 if isinstance(exc, ConflictError) else 400)

    @app.exception_handler(OSError)
    async def filesystem_error(request: Request, exc: OSError):
        status = 404 if isinstance(exc, FileNotFoundError) else 403 if isinstance(exc, PermissionError) else 400
        return JSONResponse({"detail": str(exc)}, status_code=status)

    @app.exception_handler(subprocess.SubprocessError)
    async def child_error(request: Request, exc: subprocess.SubprocessError):
        return JSONResponse({"detail": f"System action failed: {exc}"}, status_code=400)

    @app.get("/api/health")
    def health() -> dict:
        """Report the active storage location and server version."""
        return {"ok": True, "version": __version__, "home": str(directory), "last_backup": backups.last_backup}

    @app.get("/api/settings")
    def settings() -> dict:
        """Read persistent interface preferences."""
        return load_settings(db)

    @app.put("/api/settings")
    def update_settings(body: dict) -> dict:
        """Store interface preferences in the local index."""
        result = save_settings(db, body)
        watcher.configure()
        return result

    app.include_router(routes_roots.router)
    app.include_router(routes_images.router)
    app.include_router(routes_fs.router)
    app.include_router(routes_ops.router)
    app.include_router(routes_cache.router)
    app.include_router(routes_duplicates.router)
    app.include_router(routes_desktop.router)
    static = resource_path("static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """Serve the offline interface."""
        return FileResponse(static / "index.html")

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
