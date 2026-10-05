"""Command-line entry point for the local desktop viewer."""

from __future__ import annotations

import argparse
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import uvicorn

from .app import create_app
from .launcher import free_port, open_window


def main() -> None:
    """Run on loopback, optionally launch an app window, and stop on Ctrl+C."""
    parser = argparse.ArgumentParser(description="Browse a local photo library in an ordered desktop viewer")
    parser.add_argument("folder", nargs="?")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--home")
    parser.add_argument("--browser", choices=["auto", "edge", "chrome", "default"], default="auto")
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.home:
        os.environ["PHOTO_EXPLORER_HOME"] = str(Path(args.home).resolve())
    app = create_app()
    port = args.port or free_port()
    base_url = f"http://127.0.0.1:{port}"
    url = base_url + "/"
    if args.folder:
        url += "?" + urlencode({"open": str(Path(args.folder).resolve()), "recursive": int(not args.no_recursive)})
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="http-server")
    thread.start()
    try:
        for _ in range(150):
            if not thread.is_alive():
                raise RuntimeError("Server failed to start; check whether the port is already in use")
            if server.started:
                try:
                    with urllib.request.urlopen(base_url + "/api/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    pass
            time.sleep(.1)
        else:
            raise RuntimeError("Server did not become ready within 15 seconds")
        print(url, flush=True)
        if not args.no_browser:
            open_window(url, args.browser)
        while thread.is_alive():
            thread.join(.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=20)


if __name__ == "__main__":
    main()
