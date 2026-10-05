"""Natural names, EXIF timestamps, display orientation, and lazy formats."""

from datetime import datetime

import pytest
from PIL import Image

from photo_explorer.metadata import natural_key, parse_taken, read_metadata


def test_natural_order():
    names = ["img10.jpg", "IMG2.jpg", "img1.jpg", "img100.jpg"]
    assert sorted(names, key=natural_key) == ["img1.jpg", "IMG2.jpg", "img10.jpg", "img100.jpg"]
    assert natural_key("IMG_0001.JPG") == "img_000000000001.jpg"


@pytest.mark.parametrize("tags,year", [
    ({36867: "2024:02:29 12:00:00"}, 2024),
    ({36867: "bad", 36868: "2020:01:02 03:04:05"}, 2020),
    ({36867: "1980:01:01 00:00:00", 306: "2021:12:01 00:00:00"}, 2021),
    ({36867: b"2022:03:04 01:02:03\x00"}, 2022),
    ({36867: "1989:12:31 23:59:59"}, None), ({36867: "2024:99:99 00:00:00"}, None), ({}, None),
])
def test_date_fallbacks_and_rejects(tags, year):
    taken = parse_taken(tags)
    assert (datetime.fromtimestamp(taken).year if taken else None) == year


@pytest.mark.parametrize("orientation", range(1, 9))
def test_display_orientation(tmp_path, orientation):
    path = tmp_path / "oriented.jpg"
    exif = Image.Exif()
    exif[274] = orientation
    with Image.new("RGB", (120, 80)) as image:
        image.save(path, exif=exif)
    metadata = read_metadata(path)
    assert (metadata["width"], metadata["height"]) == ((80, 120) if orientation >= 5 else (120, 80))
    assert metadata["orient"] == ("portrait" if orientation >= 5 else "landscape")


@pytest.mark.parametrize("filename", ["motion.gif", "motion.webp"])
def test_animation(sample_library, filename):
    metadata = read_metadata(sample_library / filename)
    assert metadata["animated"] == 1
    assert metadata["frames"] == 4


def test_svg_never_decoded(sample_library, monkeypatch):
    monkeypatch.setattr(Image, "open", lambda *args, **kwargs: pytest.fail("SVG must not use Pillow"))
    metadata = read_metadata(sample_library / "vector.svg")
    assert metadata["width"] is None
    assert metadata["format"] == "SVG"
