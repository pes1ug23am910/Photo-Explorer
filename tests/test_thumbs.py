"""Concurrent first requests generate each derivative once."""

from concurrent.futures import ThreadPoolExecutor


def test_concurrent_cache_generation(client, indexed, monkeypatch):
    cache = client.app.state.cache
    row = client.app.state.db.one("SELECT * FROM images WHERE name='img1.jpg'")
    generate = cache._generate
    calls = []

    def counted(*args):
        calls.append(1)
        return generate(*args)

    monkeypatch.setattr(cache, "_generate", counted)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outputs = list(pool.map(lambda _: cache.render(row, 256), range(12)))
    assert len(set(outputs)) == 1
    assert len(calls) == 1


def test_decode_error_is_drawn_into_placeholder(client, indexed, monkeypatch):
    from PIL import ImageDraw
    calls = []
    original = ImageDraw.ImageDraw.multiline_text

    def capture(self, xy, text, *args, **kwargs):
        calls.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", capture)
    row = client.app.state.db.one("SELECT * FROM images WHERE name='empty.png'")
    client.app.state.cache.render(row, 400)
    assert calls
    assert "cannot identify image" in calls[0].lower()
