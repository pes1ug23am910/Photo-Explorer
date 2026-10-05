"""Source and frozen entry point; internal modes never import the server."""

from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from pathlib import Path

from . import __version__
from .picker import write_stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Browse a local photo library in an ordered desktop viewer")
    parser.add_argument("folder", nargs="?")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--home")
    parser.add_argument("--browser", choices=["auto", "edge", "chrome", "default"], default="auto")
    parser.add_argument("--keep-running", action="store_true")
    parser.add_argument("--no-tray", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--pick-folder", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--title", default="Open a photo folder", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.version:
        write_stdout(__version__)
        return 0
    if args.pick_folder:
        from .picker import pick_folder
        return pick_folder(args.title)
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.home:
        os.environ["PHOTO_EXPLORER_HOME"] = str(Path(args.home).resolve())

    from .config import home_directory
    from .logging_setup import setup_logging, close_logging
    from .launcher import free_port, WindowManager
    from .lifecycle import Lifecycle
    from .single_instance import Instance, forward_launch, request_json

    home = home_directory()
    log_path = setup_logging(home, args.verbose)
    log = logging.getLogger(__name__)
    instance = Instance(home)
    port = args.port or free_port()
    try:
        existing = instance.acquire(port)
        if existing:
            result = forward_launch(existing, args.folder, args.no_browser, not args.no_recursive)
            close_logging(log_path)
            return result
    except Exception:
        log.exception("Instance acquisition or forwarding failed")
        close_logging(log_path)
        return 1
    lifecycle = Lifecycle(not args.no_browser, args.keep_running)
    thread = server = windows = tray = None
    exit_code = 0
    try:
        import uvicorn
        from .app import create_app
        app = create_app(home, lifecycle=lifecycle, port=port, verbose=args.verbose)
        base = f"http://127.0.0.1:{port}"
        windows = WindowManager(base + "/", home, args.browser, lifecycle)
        app.state.windows = windows
        lifecycle.on_focus = windows.open
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None,
                                access_log=args.verbose, proxy_headers=False, timeout_graceful_shutdown=15))

        def request_shutdown(reason: str) -> None:
            app.state.scanner.stopping.set()
            app.state.duplicates.stopping.set()
            app.state.cache_policy.stopping.set()
            app.state.backups.stop.set()
            for row in app.state.db.all("SELECT id FROM operations WHERE status='running'"):
                app.state.operations.cancelled.add(row["id"])
            server.should_exit = True

        lifecycle.on_shutdown = request_shutdown
        thread = threading.Thread(target=server.run, name="http-server", daemon=True)
        thread.start()
        deadline = time.monotonic() + 30
        while not server.started:
            if not thread.is_alive():
                raise RuntimeError("Server failed to start; check whether the port is already in use")
            if time.monotonic() >= deadline:
                raise RuntimeError("Server did not become ready within 30 seconds")
            time.sleep(.1)
        instance.publish()
        write_stdout(base + "/")
        if args.folder:
            root = request_json(port, "/api/roots", {"path": str(Path(args.folder).resolve()),
                                                     "recursive": not args.no_recursive}, timeout=15)["root"]
            windows.url = base + f"/#root={root['id']}"
        if not args.no_browser:
            windows.open()
            if not args.no_tray:
                from .tray import Tray
                tray = Tray(port, lifecycle)
                tray.start()
        while thread.is_alive() and not lifecycle.stopping.wait(.25):
            pass
    except KeyboardInterrupt:
        lifecycle.shutdown("Ctrl+C")
    except Exception:
        log.exception("Application failed")
        lifecycle.shutdown("application error")
        exit_code = 1
    finally:
        if not lifecycle.reason:
            lifecycle.shutdown("server stopped")
        for desktop in (tray, windows):
            if desktop:
                try:
                    desktop.close()
                except Exception:
                    log.exception("Desktop shutdown cleanup failed")
        if server:
            server.should_exit = True
        if thread:
            thread.join(timeout=20)
            if thread.is_alive():
                log.error("Shutdown exceeded the 20-second join limit; workers are cancelling")
                exit_code = 1
        lifecycle.close()
        try:
            instance.release()
        finally:
            close_logging(log_path)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
