"""Bounded UTF-8 desktop logs and exception reporting without a console."""

from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(home: Path, verbose: bool = False) -> Path:
    """Route application and server logs to five files of at most two MiB."""
    path = home / "logs" / "app.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "photo_explorer", False):
            root.removeHandler(handler)
            handler.close()
    level = logging.DEBUG if verbose else logging.INFO
    root.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers = [RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=4, encoding="utf-8")]
    if sys.stderr is not None and not getattr(sys, "frozen", False):
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.photo_explorer = True
        handler.setLevel(level)
        handler.setFormatter(formatter)
        root.addHandler(handler)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(logging.WARNING if name == "uvicorn.access" and not verbose else level)
    # Decoders may emit metadata at DEBUG; diagnostic logs must not include EXIF values.
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("pillow_heif").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.excepthook = exception_hook
    threading.excepthook = thread_exception_hook
    return path


def exception_hook(kind, value, traceback) -> None:
    logging.getLogger("photo_explorer").critical("Unhandled exception", exc_info=(kind, value, traceback))


def thread_exception_hook(args) -> None:
    logging.getLogger("photo_explorer").critical(
        "Unhandled thread exception: %s", args.thread.name if args.thread else "unknown",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def log_tail(path: Path, lines: int = 200) -> list[str]:
    """Read a bounded tail without retaining the whole log in memory."""
    try:
        with path.open(encoding="utf-8", errors="replace") as source:
            return [line.rstrip("\r\n") for line in deque(source, maxlen=lines)]
    except OSError:
        return []


def close_logging(path: Path) -> None:
    """Release this home's handlers so tests and shutdown leave no file handles."""
    root = logging.getLogger()
    matching = any(isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == path for h in root.handlers)
    if matching:
        for handler in list(root.handlers):
            if getattr(handler, "photo_explorer", False):
                root.removeHandler(handler)
                handler.close()
