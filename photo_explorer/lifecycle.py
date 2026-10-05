"""Clock-driven desktop lifetime, independent of HTTP and native UI."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)


class Lifecycle:
    """Combine process exit, reload grace, and throttled-page heartbeats."""

    def __init__(self, window_mode: bool = False, keep_running: bool = False,
                 clock: Callable[[], float] = time.monotonic):
        self.window_mode = window_mode
        self.keep_running = keep_running
        self.clock = clock
        self.started = clock()
        self.last_heartbeat: float | None = None  # None until the page has reported in once
        self.closed_at: float | None = None
        self.closed_reason = "window closed"
        self.reason: str | None = None
        self.stopping = threading.Event()
        self.guard = threading.RLock()
        self.on_shutdown: Callable[[str], None] = lambda reason: None
        self.on_focus: Callable[[str], None] = lambda fragment: None
        self.thread: threading.Thread | None = None

    def heartbeat(self) -> None:
        with self.guard:
            self.last_heartbeat = self.clock()
            self.closed_at = None

    def window_closed(self, reason: str = "window closed") -> None:
        with self.guard:
            if self.closed_at is None:
                self.closed_at = self.clock()
                self.closed_reason = reason

    def process_closed(self) -> None:
        """The browser process we launched has ended.

        That is not proof the window is gone: a browser that is already running (or pre-launched
        in the background) adopts the new window and the process we started exits at once. So this
        only starts the grace period; a heartbeat from the page cancels it, silence lets it expire.
        """
        with self.guard:
            if self.window_mode and not self.keep_running:
                self.window_closed("browser process exited")

    def page_alive(self, within: float = 15) -> bool:
        """True when the page has sent a heartbeat recently, whichever process hosts it."""
        with self.guard:
            return (self.closed_at is None and self.last_heartbeat is not None
                    and self.clock() - self.last_heartbeat < within)

    def set_keep_running(self, enabled: bool) -> None:
        with self.guard:
            self.keep_running = enabled
            log.info("Keep running in background: %s", enabled)

    def tick(self) -> None:
        with self.guard:
            if not self.window_mode or self.keep_running or self.stopping.is_set():
                return
            now = self.clock()
            if self.closed_at is not None and now - self.closed_at >= 10:
                self.shutdown(f"{self.closed_reason} (grace expired)")
            elif now - (self.last_heartbeat if self.last_heartbeat is not None else self.started) >= 150:
                self.shutdown("heartbeat timeout")

    def shutdown(self, reason: str) -> None:
        with self.guard:
            if self.stopping.is_set():
                return
            self.reason = reason
            log.info("Shutdown requested: %s", reason)
            self.stopping.set()
        self.on_shutdown(reason)

    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._watch, name="window-lifecycle", daemon=True)
            self.thread.start()

    def _watch(self) -> None:
        while not self.stopping.wait(.5):
            self.tick()

    def close(self) -> None:
        self.stopping.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
