"""Generate a deterministic image library for local checks and automated tests."""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pillow_heif import register_heif_opener

register_heif_opener()


def picture(name: str, sequence: int, size: tuple[int, int], alpha: bool = False) -> Image.Image:
    """Draw a numbered card with an unmistakable red encoded-top marker."""
    image = Image.new("RGBA" if alpha else "RGB", size, (35 + sequence * 31 % 130, 50 + sequence * 17 % 100, 100 + sequence * 13 % 110))
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.rectangle((0, 0, width - 1, max(16, height // 8)), fill=(245, 25, 35))
    draw.rectangle((width * .08, height * .26, width * .92, height * .82), outline="white", width=max(2, width // 180))
    font = ImageFont.load_default(size=max(12, min(width, height) // 16))
    draw.text((width * .12, height * .36), f"{sequence:03d}  {name}\nRED = encoded TOP", fill="white", font=font)
    if alpha:
        draw.ellipse((width * .7, height * .68, width * .96, height * .95), fill=(255, 255, 255, 70))
    return image


def make_samples(destination: Path, count: int = 80, seed: int = 7) -> list[Path]:
    """Create named, nested, dated samples including unsupported and broken pixels."""
    destination.mkdir(parents=True, exist_ok=True)
    for folder in ("Trips", "Trips/Coast", ".hidden"):
        (destination / folder).mkdir(exist_ok=True)
    generator = random.Random(seed)
    files: list[Path] = []
    names = ["img1.jpg", "img2.jpg", "img10.jpg", "IMG_0001.JPG", "日本.jpg", "with spaces #1.jpg"]
    dimensions = [(640, 400), (300, 600), (400, 400), (200, 280), (1200, 800), (6000, 2000)]
    cameras = [("Canon", "EOS R6"), ("Nikon", "Z 6"), ("Sony", "ILCE-7M4")]
    for index in range(max(6, count - 13)):
        name = names[index] if index < len(names) else f"photo{index + 1}.jpg"
        folder = destination if index < 6 or index % 3 == 0 else destination / ("Trips" if index % 3 == 1 else "Trips/Coast")
        path = folder / name
        size = dimensions[index % len(dimensions)] if index < 6 else generator.choice(dimensions[:5])
        with picture(name, index, size) as image:
            exif = Image.Exif()
            if index % 7 != 5:
                exif[36867] = f"{2019 + index % 8}:0{1 + index % 9}:{1 + index % 27:02d} 12:34:56"
                exif[271], exif[272] = cameras[index % 3]
                exif[274] = [1, 3, 6, 8][index % 4]
                exif[34855] = [100, 400, 800][index % 3]
                exif[33437] = 2.8
                exif[37386] = 50.0
            image.save(path, quality=85, exif=exif)
        files.append(path)
    formats = [("café.png", "PNG"), ("alpha.png", "PNG"), ("still.webp", "WEBP"),
               ("bitmap.bmp", "BMP"), ("scan.tiff", "TIFF"), ("modern.avif", "AVIF"),
               ("phone.heic", "HEIF"), ("icon.ico", "ICO")]
    for name, format_name in formats:
        path = destination / name
        try:
            with picture(name, len(files), (480, 320), name.endswith(".png")) as image:
                image.save(path, format=format_name)
            files.append(path)
        except (OSError, ValueError, KeyError) as exc:
            print(f"Skipping {name}: {exc}", file=sys.stderr)
            path.unlink(missing_ok=True)
    for name, format_name in (("motion.gif", "GIF"), ("motion.webp", "WEBP")):
        frames = [picture(name, 80 + frame, (320, 240)) for frame in range(4)]
        path = destination / name
        frames[0].save(path, format=format_name, save_all=True, append_images=frames[1:], duration=180, loop=0)
        for frame in frames:
            frame.close()
        files.append(path)
    svg = destination / "vector.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400" viewBox="0 0 600 400">'
                   '<rect width="600" height="400" fill="#426877"/><rect width="600" height="50" fill="#f51923"/>'
                   '<text x="50" y="200" fill="white" font-size="36">vector.svg #SVG</text></svg>', encoding="utf-8")
    files.append(svg)
    broken = destination / "corrupt.jpg"
    broken.write_bytes(b"\xff\xd8\xff\xe0truncated")
    files.append(broken)
    zero = destination / "empty.png"
    zero.write_bytes(b"")
    files.append(zero)
    with picture("hidden.jpg", 999, (200, 200)) as image:
        image.save(destination / ".hidden/hidden.jpg")
    base = datetime(2026, 9, 1, 9).timestamp()
    for index, path in enumerate(files):
        stamp = base + index * 3607
        os.utime(path, (stamp, stamp))
    return files


def main() -> None:
    """Run the sample generator as a standalone command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dest", type=Path)
    parser.add_argument("--count", type=int, default=80)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    paths = make_samples(args.dest.resolve(), args.count, args.seed)
    print(f"Created {len(paths)} visible images in {args.dest.resolve()}")


if __name__ == "__main__":
    main()
