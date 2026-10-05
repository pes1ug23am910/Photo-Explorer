"""Loopback server selection and desktop app-window launch."""

from __future__ import annotations

import socket
import subprocess
import threading
import webbrowser
from pathlib import Path

BROWSERS = {
    "edge": [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
             Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")],
    "chrome": [Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
               Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")],
}


def free_port() -> int:
    """Find an available TCP port bound only to loopback."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def browser_path(choice: str) -> Path | None:
    """Prefer Edge then Chrome, honoring an explicit browser preference."""
    names = ["edge", "chrome"] if choice == "auto" else [choice]
    for name in names:
        for path in BROWSERS.get(name, []):
            if path.is_file():
                return path
    return None


def open_window(url: str, choice: str) -> None:
    """Open a chromeless app window without blocking the server's main thread."""
    executable = browser_path(choice)
    if executable:
        def launch():
            try:
                subprocess.run([str(executable), f"--app={url}", "--window-size=1500,950"], timeout=86400, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        threading.Thread(target=launch, daemon=True, name="browser-window").start()
    else:
        webbrowser.open(url)
