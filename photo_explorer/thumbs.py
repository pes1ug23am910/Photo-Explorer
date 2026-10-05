"""Versioned, locked thumbnail and browser-render caches."""

from __future__ import annotations

import hashlib
import os
import threading
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import canonical
from .db import Database


class ThumbnailCache:
    """Use a bounded lock set to serialize generation of each cache key."""

    def __init__(self, home: Path, db: Database | None = None):
        self.directory = home / "thumbs"
        self.locks = [threading.RLock() for _ in range(128)]
        self.db = db

    def key(self, row: dict) -> str:
        """Hash current file identity; even an unscanned mtime change invalidates it."""
        stat = Path(row["display_path"]).stat()
        identity = f"{canonical(row['path'])}|{stat.st_size}|{stat.st_mtime_ns}"
        return hashlib.sha1(identity.encode("utf-8")).hexdigest()

    def info(self, row: dict) -> dict:
        """Report version and available derivatives without rendering them."""
        key = self.key(row)
        folder = self.directory / key[:2]
        return {"key": key, "files": [path.name for path in folder.glob(f"{key}_*")]}

    def render(self, row: dict, mode: int | str) -> tuple[Path, str]:
        """Return a cached oriented JPEG or full-size PNG, generating if needed."""
        key = self.key(row)
        suffix = "full.png" if mode == "full" else f"{mode}.jpg"
        destination = self.directory / key[:2] / f"{key}_{suffix}"
        with self.locks[int(key[:4], 16) % len(self.locks)]:
            if destination.exists():
                return destination, key
            # Record the key only when a derivative is actually written, so cache hits stay read-only.
            if self.db:
                self.db.execute("INSERT OR IGNORE INTO cache_keys(root_id,key) SELECT id,? FROM roots WHERE id=?",
                                (key, row["root_id"]))
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            try:
                if row.get("meta_state") == 2:
                    self._placeholder(temporary, mode, row.get("meta_error"))
                else:
                    self._generate(Path(row["display_path"]), temporary, mode)
                os.replace(temporary, destination)
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
                self._placeholder(temporary, mode, str(exc))
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return destination, key

    def _generate(self, source: Path, destination: Path, mode: int | str) -> None:
        with Image.open(source) as opened:
            size = 2048 if mode == "p" else mode
            if mode != "full":
                opened.draft("RGB", (int(size) * 2, int(size) * 2))
            image = ImageOps.exif_transpose(opened)
            try:
                if mode != "full":
                    image.thumbnail((int(size), int(size)), Image.Resampling.LANCZOS)
                    background = Image.new("RGB", image.size, (29, 32, 40))
                    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                        rgba = image.convert("RGBA")
                        background.paste(rgba, mask=rgba.getchannel("A"))
                        rgba.close()
                    else:
                        background.paste(image.convert("RGB"))
                    background.save(destination, "JPEG", quality=88 if mode == "p" else 82)
                    background.close()
                else:
                    image.convert("RGBA" if "A" in image.getbands() else "RGB").save(destination, "PNG")
            finally:
                image.close()

    def _placeholder(self, destination: Path, mode: int | str, error: str | None = None) -> None:
        size = mode if isinstance(mode, int) else 400
        with Image.new("RGB", (size, size), (42, 37, 43)) as image:
            draw = ImageDraw.Draw(image)
            draw.line((size * .38, size * .16, size * .62, size * .4), fill=(206, 115, 117), width=4)
            draw.line((size * .62, size * .16, size * .38, size * .4), fill=(206, 115, 117), width=4)
            font = ImageFont.load_default(size=max(9, min(14, size // 20)))
            draw.text((10, size * .48), "Cannot decode image", fill="white", font=font)
            detail = " ".join((error or "Unknown decoding error").split())
            lines = textwrap.wrap(detail, width=max(18, size // 7))[:4]
            draw.multiline_text((10, size * .60), "\n".join(lines), fill=(203, 179, 180), font=font, spacing=3)
            image.save(destination, "PNG" if mode == "full" else "JPEG", quality=82)

    def forget(self, rows: list[dict], root_id: int | None = None) -> None:
        """Remove current derivatives belonging to forgotten image records."""
        keys = set()
        if self.db and root_id is not None:
            keys.update(row["key"] for row in self.db.all("SELECT key FROM cache_keys WHERE root_id=?", (root_id,)))
        for row in rows:
            try:
                keys.add(self.key(row))
            except OSError:
                continue
        for key in keys:
            with self.locks[int(key[:4], 16) % len(self.locks)]:
                for path in (self.directory / key[:2]).glob(f"{key}_*"):
                    path.unlink(missing_ok=True)
