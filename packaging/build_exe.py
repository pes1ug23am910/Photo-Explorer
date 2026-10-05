"""Build one unsigned Windows folder distribution, then create its ZIP."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from collections import Counter, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from photo_explorer import __version__
from make_icon import main as make_icon


def clean(path: Path) -> None:
    """Delete only the two explicit generated directories beneath this checkout."""
    resolved = path.resolve()
    allowed = {ROOT / "build", ROOT / "dist/PhotoExplorer"}
    if resolved not in allowed or path.is_symlink() or path.is_junction():
        raise ValueError(f"Refusing unexpected clean target: {resolved}")
    if path.exists():
        shutil.rmtree(path)


def main() -> int:
    if os.name != "nt" or sys.maxsize <= 2**32:
        raise RuntimeError("Build requires 64-bit Windows Python")
    required = ROOT / ".venv/Scripts/python.exe"
    if Path(sys.executable).resolve() != required.resolve():
        raise RuntimeError(f"Use the project interpreter: {required}")
    os.environ["PHOTO_EXPLORER_HOME"] = str(ROOT / ".work/home")
    os.environ["PYINSTALLER_CONFIG_DIR"] = str(ROOT / ".work/pyinstaller-cache")
    os.environ["PYTHONIOENCODING"] = "utf-8"
    # Child temporary files and build caches remain inside the checkout.
    temporary = ROOT / ".work/build-temp"
    temporary.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = str(temporary)
    clean(ROOT / "build")
    clean(ROOT / "dist/PhotoExplorer")
    (ROOT / "build").mkdir()
    (ROOT / "dist").mkdir(exist_ok=True)
    make_icon()
    log = ROOT / "build/pyinstaller.log"
    command = [str(required), "-m", "PyInstaller", "--noconfirm", "--clean",
               "--workpath", str(ROOT / "build"), "--distpath", str(ROOT / "dist"),
               str(ROOT / "packaging/PhotoExplorer.spec")]
    print(f"Building Photo Explorer {__version__}; log: {log}", flush=True)
    with log.open("w", encoding="utf-8") as output:
        result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                timeout=900, check=False)
    with log.open(encoding="utf-8", errors="replace") as source:
        print("".join(deque(source, maxlen=30)), end="", flush=True)
    if result.returncode:
        return result.returncode
    folder = ROOT / "dist/PhotoExplorer"
    if not (folder / "PhotoExplorer.exe").is_file():
        raise RuntimeError("The build did not produce PhotoExplorer.exe")
    archive = ROOT / f"dist/PhotoExplorer-{__version__}-win64.zip"
    counts = Counter()
    size = count = 0
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                size += path.stat().st_size
                count += 1
                relative = path.relative_to(folder)
                counts[relative.parts[0]] += 1
                zipped.write(path, path.relative_to(ROOT / "dist"))
    print(f"Folder: {size:,} bytes ({size / 1024**2:.2f} MiB), {count:,} files")
    for name, number in sorted(counts.items()):
        print(f"  {name}: {number:,} files")
    print(f"ZIP: {archive.name}, {archive.stat().st_size:,} bytes", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
