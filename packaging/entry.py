"""Windowed distribution entry point."""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from photo_explorer.__main__ import main
    raise SystemExit(main())
