"""Per-home ownership and bounded loopback forwarding between launches."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from .picker import write_stdout


def request_json(port: int, path: str, body: dict | None = None, timeout: float = 1):
    """Bypass system proxies: desktop control never leaves loopback."""
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                    data=json.dumps(body).encode("utf-8") if body is not None else None,
                                    headers={"Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
        return json.load(response)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


class Instance:
    """Exclusive file claim, serialized stale recovery, and ownership-safe release."""

    def __init__(self, home: Path):
        self.home = home.resolve()
        self.lock = self.home / "instance.lock"
        self.path = self.home / "instance.json"
        self.record: dict | None = None

    @contextmanager
    def _guard(self):
        # The kernel mutex closes the stale-file read/unlink race between two launches.
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        identity = hashlib.sha256(os.path.normcase(str(self.home)).encode()).hexdigest()
        handle = kernel.CreateMutexW(None, False, "Local\\PhotoExplorer-" + identity)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = False
        try:
            acquired = kernel.WaitForSingleObject(handle, 5000) in {0, 0x80}
            if not acquired:
                raise RuntimeError("Another launch is still acquiring the instance lock")
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(record, dict) and isinstance(record.get("pid"), int)
                    and isinstance(record.get("port"), int) and 1 <= record["port"] <= 65535
                    and isinstance(record.get("started"), (int, float))):
                return record
        except (OSError, ValueError):
            pass
        return None

    def _healthy(self, record: dict) -> bool:
        try:
            health = request_json(record["port"], "/api/health", timeout=1)
            return health.get("ok") is True and Path(health.get("home", "")).resolve() == self.home
        except (OSError, ValueError):
            return False

    def acquire(self, port: int) -> dict | None:
        self.home.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 35
        while True:
            with self._guard():
                if self.lock.exists():
                    previous = self._read(self.lock)
                    if previous and pid_alive(previous["pid"]):
                        published = self._read(self.path) == previous
                        if self._healthy(previous):
                            return previous
                        # A live owner gets time to import and start before publication.
                        if not published and time.time() - previous["started"] < 30:
                            starting = True
                        else:
                            starting = False
                    else:
                        starting = False
                    if not starting:
                        self.lock.unlink(missing_ok=True)
                        self.path.unlink(missing_ok=True)
                else:
                    starting = False
                if not starting:
                    record = {"pid": os.getpid(), "port": port, "started": time.time()}
                    with self.lock.open("x", encoding="utf-8") as output:
                        json.dump(record, output)
                    self.record = record
                    return None
            if time.monotonic() >= deadline:
                raise RuntimeError("The previous instance is still starting; retry shortly")
            time.sleep(.1)

    def publish(self) -> None:
        with self._guard():
            if self.record is None or self._read(self.lock) != self.record:
                raise RuntimeError("Instance ownership changed during startup")
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(self.record), encoding="utf-8")
            temporary.replace(self.path)

    def release(self) -> None:
        with self._guard():
            if self.record is not None and self._read(self.lock) == self.record:
                self.path.unlink(missing_ok=True)
                self.lock.unlink(missing_ok=True)
            self.record = None


def forward_launch(record: dict, folder: str | None, no_browser: bool, recursive: bool = True) -> int:
    """Send folder registration and focus to the owner of the browser handle."""
    port = record["port"]
    root_id = None
    if folder:
        result = request_json(port, "/api/roots", {"path": str(Path(folder).resolve()), "recursive": recursive}, timeout=15)
        root_id = result["root"]["id"]
    if not no_browser:
        request_json(port, "/api/focus" + (f"?root_id={root_id}" if root_id is not None else ""), {}, timeout=5)
    write_stdout(f"http://127.0.0.1:{port}/")
    return 0
