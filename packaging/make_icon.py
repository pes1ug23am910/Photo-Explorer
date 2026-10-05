"""Generate the distribution's framed-photo icon using Pillow."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from photo_explorer.tray import icon_image


def main() -> None:
    with icon_image(256) as image:
        image.save(ROOT / "packaging/icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])


if __name__ == "__main__":
    main()
