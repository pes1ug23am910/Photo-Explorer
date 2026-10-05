"""Internal native dialog mode, usable before importing the HTTP server."""

import os
import sys


def write_stdout(value: str) -> None:
    """Write UTF-8 to a parent pipe even in a windowed frozen executable."""
    if sys.stdout is not None:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(value, flush=True)
    elif os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel.WriteFile.restype = wintypes.BOOL
        handle = kernel.GetStdHandle(-11 & 0xffffffff)
        if handle and handle != ctypes.c_void_p(-1).value:
            payload = (value + "\n").encode("utf-8")
            written = wintypes.DWORD()
            if not kernel.WriteFile(handle, payload, len(payload), ctypes.byref(written), None):
                raise ctypes.WinError(ctypes.get_last_error())


def pick_folder(title: str = "Open a photo folder") -> int:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        write_stdout(filedialog.askdirectory(parent=root, title=title, mustexist=True) or "")
    finally:
        root.destroy()
    return 0
