"""Local storage, supported formats, and path validation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

EXTENSIONS = frozenset("jpg jpeg jpe jfif png gif webp bmp tif tiff heic heif avif ico psd svg".split())
SKIP_DIRS = frozenset({"$recycle.bin", "system volume information", ".git", "node_modules", "__pycache__"})
BROWSER_FORMATS = frozenset("jpg jpeg jpe jfif png gif webp bmp avif ico svg".split())
MIME_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "jpe": "image/jpeg", "jfif": "image/jpeg",
    "png": "image/png", "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
    "avif": "image/avif", "ico": "image/x-icon", "svg": "image/svg+xml",
}


def home_directory(override: str | Path | None = None) -> Path:
    """Create and return the application's private storage directory."""
    base = override or os.environ.get("PHOTO_EXPLORER_HOME")
    if not base:
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PhotoExplorer"
    result = Path(base).expanduser().resolve()
    result.mkdir(parents=True, exist_ok=True)
    (result / "thumbs").mkdir(exist_ok=True)
    return result


def canonical(path: str | Path) -> str:
    """Return an absolute, normalized identity for a filesystem path."""
    return os.path.normcase(os.path.abspath(path))


def access_path(path: str | Path) -> Path:
    """Use Windows extended paths for filesystem access, retaining plain display paths."""
    value = os.path.abspath(path)
    if os.name == "nt" and len(value) > 240 and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)


def validate_directory(value: str) -> Path:
    """Accept existing absolute directories without traversal segments."""
    if not value or ".." in value.replace("\\", "/").split("/"):
        raise ValueError("Choose an absolute folder path without '..' segments")
    if not os.path.isabs(value):
        raise ValueError("Folder path must be absolute")
    path = Path(os.path.abspath(value))
    if not access_path(path).is_dir():
        raise ValueError("Folder does not exist or is not accessible")
    return path


def inside(path: str | Path, parent: str | Path) -> bool:
    """Test directory containment using path components, not string prefixes."""
    try:
        return os.path.commonpath([canonical(path), canonical(parent)]) == canonical(parent)
    except ValueError:
        return False


def visible_name(name: str) -> bool:
    """Exclude hidden and known system directory names."""
    return not name.startswith(".") and name.lower() not in SKIP_DIRS


def load_settings(db: Any) -> dict:
    """Read the JSON preference object."""
    row = db.one("SELECT value FROM settings WHERE key='ui'")
    return json.loads(row["value"]) if row else {}


def save_settings(db: Any, value: dict) -> dict:
    """Replace the JSON preference object."""
    serialized = json.dumps(value, ensure_ascii=False)
    if len(serialized) > 64_000:
        raise ValueError("Settings must be smaller than 64 KB")
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('ui',?)", (serialized,))
    return value
