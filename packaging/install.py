"""Install the built folder distribution for the current user and add a Start menu entry.

Usage (from the project folder, after ``packaging/build_exe.py``):

    .venv\\Scripts\\python.exe packaging\\install.py              # install or update
    .venv\\Scripts\\python.exe packaging\\install.py --uninstall  # remove the program and its shortcut

The program is copied to ``%LOCALAPPDATA%\\Programs\\PhotoExplorer`` so a later rebuild of ``dist``
never breaks the shortcut, and ``Photo Explorer.lnk`` is created in the user's Start menu Programs
folder. Library data under ``%LOCALAPPDATA%\\PhotoExplorer`` is never touched. No administrator
rights are needed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dist" / "PhotoExplorer"
TARGET = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "PhotoExplorer"
SHORTCUT = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Photo Explorer.lnk"


def running() -> bool:
    """True when a PhotoExplorer.exe process exists; replacing files under it would fail."""
    result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq PhotoExplorer.exe", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True, timeout=30)
    return "PhotoExplorer.exe" in result.stdout


def powershell(script: str, env: dict[str, str]) -> str:
    """Run a short PowerShell snippet; paths travel through environment variables, never quoting."""
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=60, env={**os.environ, **env})
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "PowerShell failed")
    return result.stdout.strip()


def write_shortcut(target: Path) -> None:
    SHORTCUT.parent.mkdir(parents=True, exist_ok=True)
    powershell(
        "$shell = New-Object -ComObject WScript.Shell; "
        "$link = $shell.CreateShortcut($env:PE_SHORTCUT); "
        "$link.TargetPath = $env:PE_TARGET; "
        "$link.WorkingDirectory = $env:PE_WORKDIR; "
        "$link.IconLocation = $env:PE_TARGET + ',0'; "
        "$link.Description = 'Photo Explorer'; "
        "$link.Save()",
        {"PE_SHORTCUT": str(SHORTCUT), "PE_TARGET": str(target), "PE_WORKDIR": str(target.parent)},
    )


def shortcut_target() -> str | None:
    if not SHORTCUT.exists():
        return None
    return powershell("(New-Object -ComObject WScript.Shell).CreateShortcut($env:PE_SHORTCUT).TargetPath",
                      {"PE_SHORTCUT": str(SHORTCUT)})


def install() -> int:
    if not (SOURCE / "PhotoExplorer.exe").is_file():
        print(f"No build found at {SOURCE}; run packaging\\build_exe.py first.", file=sys.stderr)
        return 1
    if running():
        print("Photo Explorer is running; quit it (tray icon -> Quit) and run this again.", file=sys.stderr)
        return 1
    staging = TARGET.with_name(TARGET.name + ".new")
    previous = TARGET.with_name(TARGET.name + ".old")
    for leftover in (staging, previous):
        if leftover.exists():
            shutil.rmtree(leftover)
    shutil.copytree(SOURCE, staging)
    if TARGET.exists():
        TARGET.rename(previous)
    staging.rename(TARGET)
    if previous.exists():
        shutil.rmtree(previous)
    exe = TARGET / "PhotoExplorer.exe"
    write_shortcut(exe)
    print(f"Installed:  {exe}")
    print(f"Start menu: {SHORTCUT}")
    print("Search for 'Photo Explorer' in Start; right-click the entry to pin it to Start or the taskbar.")
    return 0


def uninstall() -> int:
    if running():
        print("Photo Explorer is running; quit it first.", file=sys.stderr)
        return 1
    if SHORTCUT.exists():
        SHORTCUT.unlink()
        print(f"Removed shortcut {SHORTCUT}")
    if TARGET.exists():
        shutil.rmtree(TARGET)
        print(f"Removed program folder {TARGET}")
    print("Library data in %LOCALAPPDATA%\\PhotoExplorer was left in place.")
    return 0


def main() -> int:
    if os.name != "nt":
        print("This installer is for Windows.", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description="Install Photo Explorer for the current user")
    parser.add_argument("--uninstall", action="store_true", help="remove the installed program and its Start menu entry")
    parser.add_argument("--status", action="store_true", help="show where the shortcut points")
    args = parser.parse_args()
    if args.status:
        target = shortcut_target()
        print(f"Shortcut: {SHORTCUT if target else 'not installed'}" + (f" -> {target}" if target else ""))
        print(f"Program:  {TARGET if (TARGET / 'PhotoExplorer.exe').is_file() else 'not installed'}")
        return 0
    return uninstall() if args.uninstall else install()


if __name__ == "__main__":
    raise SystemExit(main())
