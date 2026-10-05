"""Lazy dimensions, readable EXIF, and display-oriented metadata."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image
from pillow_heif import register_heif_opener
from .config import access_path

register_heif_opener()


def natural_key(name: str) -> str:
    """Make names with numbered components sort in natural order."""
    return re.sub(r"\d+", lambda match: match.group().zfill(12), name.lower())


def parse_taken(tags: dict) -> float | None:
    """Read the first valid local EXIF timestamp, rejecting dates before 1990."""
    for tag in (36867, 36868, 306):
        value = tags.get(tag)
        if isinstance(value, bytes):
            value = value.decode("ascii", errors="ignore")
        if not value:
            continue
        try:
            parsed = datetime.strptime(str(value).strip("\x00 "), "%Y:%m:%d %H:%M:%S")
            if parsed.year >= 1990:
                return parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            continue
    return None


def _float(value: Any) -> float | None:
    try:
        result = float(value)
        return result if float("-inf") < result < float("inf") else None
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00 ")[:2000]
    return str(value).strip("\x00 ")[:2000] or None


def _coordinate(parts: Any, reference: Any) -> float | None:
    try:
        degrees, minutes, seconds = [float(part) for part in parts]
        number = degrees + minutes / 60 + seconds / 3600
        return -number if _text(reference) in {"S", "W"} else number
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def exif_data(image: Image.Image) -> tuple[dict, dict]:
    """Flatten EXIF IFDs and decode the GPS coordinate pair."""
    source = image.getexif()
    tags = dict(source)
    try:
        tags.update(source.get_ifd(34665))
    except (KeyError, TypeError, ValueError, OSError):
        pass
    try:
        gps = source.get_ifd(34853)
    except (KeyError, TypeError, ValueError, OSError):
        gps = {}
    coordinates = {"gps_lat": _coordinate(gps.get(2), gps.get(1)),
                   "gps_lon": _coordinate(gps.get(4), gps.get(3))}
    readable = {ExifTags.TAGS.get(key, str(key)): _text(value)
                for key, value in tags.items() if key not in {34665, 34853, 37500, 37510}}
    if coordinates["gps_lat"] is not None:
        readable["GPSLatitude"] = coordinates["gps_lat"]
    if coordinates["gps_lon"] is not None:
        readable["GPSLongitude"] = coordinates["gps_lon"]
    return tags, {"exif": readable, **coordinates}


def read_metadata(path: str | Path) -> dict:
    """Read dimensions without decoding pixels; SVG stays browser-rendered."""
    if Path(path).suffix.lower() == ".svg":
        return {"width": None, "height": None, "pixels": None, "aspect": None, "orient": None,
                "taken": None, "format": "SVG", "animated": 0, "frames": 1,
                "exif_orientation": 1, "exif": {}, "meta_state": 1, "meta_error": None}
    with Image.open(access_path(path)) as image:
        tags, extra = exif_data(image)
        orientation = int(tags.get(274, 1))
        width, height = image.size
        if orientation in {5, 6, 7, 8}:
            width, height = height, width
        make = _text(tags.get(271)) or ""
        model = _text(tags.get(272)) or ""
        camera = model if model.lower().startswith(make.lower()) else f"{make} {model}".strip()
        iso = _float(tags.get(34855))
        return {
            "width": width, "height": height, "pixels": width * height,
            "aspect": width / height if height else None,
            "orient": "landscape" if width > height else "portrait" if height > width else "square",
            "taken": parse_taken(tags), "camera": camera or None, "lens": _text(tags.get(42036)),
            "iso": int(iso) if iso is not None else None, "fnumber": _float(tags.get(33437)),
            "exposure": _text(tags.get(33434)), "focal": _float(tags.get(37386)),
            "exif_orientation": orientation, "format": image.format,
            "animated": int(getattr(image, "is_animated", False)), "frames": getattr(image, "n_frames", 1),
            "meta_state": 1, "meta_error": None, **extra,
        }
