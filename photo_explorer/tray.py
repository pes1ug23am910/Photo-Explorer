"""Optional Windows notification icon; failure never prevents using the library."""

import logging
import threading

from .single_instance import request_json

log = logging.getLogger(__name__)


def icon_image(size: int = 32):
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((1, 1, size - 2, size - 2), radius=size // 5, fill="#253746")
    draw.rounded_rectangle((size * .16, size * .2, size * .84, size * .8), radius=max(1, size // 16),
                           outline="#bce4ed", width=max(1, size // 16))
    draw.ellipse((size * .27, size * .3, size * .4, size * .43), fill="#ffcc73")
    draw.polygon([(size * .2, size * .73), (size * .44, size * .47),
                  (size * .6, size * .61), (size * .7, size * .49), (size * .81, size * .73)], fill="#69c6bb")
    return image


class Tray:
    def __init__(self, port: int, lifecycle):
        self.port, self.lifecycle = port, lifecycle
        self.icon = None
        self.stopping = threading.Event()

    def _request(self, path: str) -> None:
        try:
            request_json(self.port, path, {}, timeout=5)
        except Exception:
            log.exception("Tray action failed: %s", path)

    def start(self) -> None:
        # Detached native/setup threads inherit daemon status, including failure paths.
        threading.Thread(target=self._start, name="tray-start", daemon=True).start()

    def _start(self) -> None:
        try:
            import pystray
            def toggle(icon, item):
                try:
                    self.lifecycle.set_keep_running(not self.lifecycle.keep_running)
                    icon.update_menu()
                except Exception:
                    log.exception("Tray background setting failed")
            self.icon = pystray.Icon("PhotoExplorer", icon_image(), f"Photo Explorer — {self.port}", pystray.Menu(
                pystray.MenuItem("Open window", lambda: self._request("/api/focus"), default=True),
                pystray.MenuItem("Keep running in background", toggle,
                                 checked=lambda item: self.lifecycle.keep_running),
                pystray.MenuItem("Diagnostics", lambda: self._request("/api/focus?diagnostics=true")),
                pystray.MenuItem("Quit", lambda: self._request("/api/shutdown")),
            ))
            def ready(icon):
                try:
                    if self.stopping.is_set():
                        icon.stop()
                    else:
                        icon.visible = True
                except Exception:
                    log.exception("Tray display failed; continuing without it")
                    icon.stop()
            self.icon.run_detached(setup=ready)
        except Exception:
            log.exception("Tray unavailable; continuing without it")
            self.close()

    def close(self) -> None:
        self.stopping.set()
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                log.exception("Tray could not stop")
            self.icon = None
