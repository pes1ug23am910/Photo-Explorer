"""Loopback server selection and desktop app-window launch."""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import webbrowser
from pathlib import Path

from .config import home_directory

log = logging.getLogger(__name__)

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


def open_window(url: str, choice: str = "auto", home: Path | None = None) -> subprocess.Popen | None:
    """Launch an isolated app profile and return its owned process handle."""
    executable = browser_path(choice)
    if executable is None and choice in {"auto", "chrome"}:
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"
        if local.is_file():
            executable = local
    if executable:
        profile = (home or home_directory()) / "browser-profile"
        return subprocess.Popen([str(executable), f"--app={url}", "--window-size=1500,950",
                                 f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                                 "--disable-features=Translate", "--disable-background-mode"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log.warning("App browser unavailable or default requested; using page lifecycle signals")
    webbrowser.open(url)
    return None


class WindowManager:
    """Keep the browser handle owned by the serving instance, including relaunches."""

    def __init__(self, url: str, home: Path, choice: str, lifecycle):
        self.url, self.home, self.choice, self.lifecycle = url, home, choice, lifecycle
        self.process: subprocess.Popen | None = None
        self.guard = threading.RLock()
        self.page_command: dict | None = None
        self.sequence = 0

    def open(self, fragment: str = "") -> None:
        with self.guard:
            if self.lifecycle.stopping.is_set():
                return
            if self.process is not None and self.process.poll() is None:
                self.sequence += 1
                self.page_command = {"sequence": self.sequence, "fragment": fragment}
                self._focus()
                return
            self.lifecycle.heartbeat()
            self.process = open_window(self.url.split('#')[0] + fragment if fragment else self.url, self.choice, self.home)
            if self.process is not None:
                threading.Thread(target=self._wait, args=(self.process,), daemon=True, name="browser-window").start()

    def _wait(self, process: subprocess.Popen) -> None:
        while not self.lifecycle.stopping.is_set():
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                continue
            with self.guard:
                if self.process is not process:
                    return
                self.process = None
            self.lifecycle.process_closed()
            return

    def _focus(self) -> None:
        """Best-effort foreground activation; the page also receives a focus request."""
        if os.name != "nt" or self.process is None:
            return
        import ctypes
        from ctypes import wintypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user.SetForegroundWindow.argtypes = [wintypes.HWND]

        @callback_type
        def visit(hwnd, _):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == self.process.pid and user.IsWindowVisible(hwnd):
                user.ShowWindow(hwnd, 9)
                user.SetForegroundWindow(hwnd)
                return False
            return True
        user.EnumWindows(visit, 0)

    def close(self) -> None:
        """Ask owned browser windows to close without killing a user's other profiles."""
        process = self.process
        if os.name != "nt" or process is None or process.poll() is not None:
            return
        import ctypes
        from ctypes import wintypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        pid_value = process.pid

        @callback_type
        def visit(hwnd, _):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == pid_value:
                user.PostMessageW(hwnd, 0x0010, 0, 0)
            return True
        user.EnumWindows(visit, 0)
