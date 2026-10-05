# Photo Explorer

An offline Windows image browser with a virtual thumbnail grid and a full-screen viewer. Choose a sort criterion and direction; every arrow step uses that exact server-ordered result, including all active filters. Nothing is uploaded, and the interface needs no network assets or build step.

## Run

Double-click `run.bat`, or run from this folder:

```powershell
.\.venv\Scripts\python.exe -m photo_explorer "D:\Pictures"
```

The launcher binds **127.0.0.1 only**, prints its URL, and opens an Edge app window (Chrome or the default browser is the fallback). Leave the terminal running; Ctrl+C stops the server. Closing the window alone leaves the server running.

```text
python -m photo_explorer [FOLDER] [--port N] [--no-browser]
  [--no-recursive] [--home DIR] [--browser auto|edge|chrome|default]
```

Use **Open folder** for the native picker, **Browse…** for the in-app folder browser (which also has an include-subfolders checkbox), or reopen a recent library. Click selects; double-click or Enter opens the viewer. Sort and direction remain available inside the viewer. The first scan lists files immediately, then fills metadata in the background; metadata-based order can settle as the scan progresses.

## Shortcuts

| Key | Action |
| --- | --- |
| Enter / double-click | Open the focused grid image |
| Arrows / Home / End / Page Up / Page Down | Move grid selection |
| Ctrl+click / Shift+click / Ctrl+A | Toggle, range, or select all |
| → / Space / Page Down | Next viewer image in the chosen order |
| ← / Backspace / Page Up | Previous image |
| Home / End / Ctrl+G | First, last, or go to image number |
| Esc | Close dialog/menu/viewer; return to the current grid image |
| F / 0 | Fit (does not upscale unless enabled in Settings) |
| 1 | Actual size, 1:1 |
| + / − / Ctrl+wheel | Zoom; wheel zooms at the cursor |
| Double-click / drag / Shift+arrows | Fit ↔ 1:1, pan, or keyboard pan |
| O | Load original or full-size render |
| I / H | Toggle information / pin overlays |
| Shift+F / L | Fill viewport / lock zoom across images |
| R / Shift+R | Rotate the view clockwise / anticlockwise |
| V / Shift+V | Flip the view horizontally / vertically |
| T / S | Toggle filmstrip / start or pause slideshow |
| Space / Esc (slideshow) | Pause/resume / stop slideshow |
| Ctrl+C / Ctrl+Shift+C | Copy the image as PNG / copy its path |
| B | Toggle sidebar |
| Delete | Move selected/current images to Recycle Bin |
| M / C / F2 | Move / copy / rename |
| Ctrl+E / Ctrl+O | Reveal in Explorer / open in default app |
| 2–5 / Alt+1 | Rate 2–5 stars / 1 star |
| ` / Ctrl+0 | Clear rating |
| Ctrl+D | Toggle favourite |
| X | Compare 2–4 selected grid images |
| Ctrl+R / W | Confirm rotation on disk / set desktop wallpaper |
| ? | Open the shortcut reference |

The help overlay is generated from the definitions in `keys.js`. `1` selects actual size and `Alt+1` gives one star. Clickable stars always provide ratings 1–5.

## Ordering and filters

All list and position requests use one query builder. Date sorts default to descending; other sorts default to ascending. Every order ends with natural filename ascending and image id ascending. Missing dimensions/camera sort last in both directions. Date taken falls back to modified time per image. Random stores a sequence until **Shuffle** is pressed; its API accepts a reproducible integer seed.

Sort keys: `mtime`, `ctime`, `taken`, `name`, `path`, `size`, `pixels`, `width`, `height`, `aspect`, `ext`, `camera`, `rating`, `random`.

The API accepts these ANDed filters: `ext` (comma list), `q` (name substring, or full path when it contains a separator), `date_field` (`mtime`, `ctime`, `taken`), `date_from`, `date_to` (local ISO dates or epochs), `size_min`, `size_max` (bytes), `orient`, `min_rating`, `favorites=1`, `camera`, `dir` (component-safe subtree), `animated=1`, `has_gps=1`, `min_pixels`, and `meta_failed=1`. ISO end dates include the whole day.

Example: `/api/roots/1/images?sort=mtime&order=desc&ext=jpg,png&offset=5&limit=1`. The viewer uses the same parameters and an indexed neighbourhood of up to five items. Position lookups use a SQLite window function; the browser never independently re-sorts the results.

The sidebar provides all filters, a folder tree with recursive counts, and filtered size/type/orientation/month aggregates. Its month strip selects an inclusive date range. **Group dates** adds real header rows to the virtual layout for date sorts. **Light** switches theme. Viewer Settings control wheel behavior, small-image upscaling, wrap-around, rotation lock and slideshow timing (1–60 seconds, default 4). Slideshow follows the current order, can loop or stop at the end, optionally fades, and requests a screen wake lock when available. The filmstrip shows up to 15 neighbours on each side. Touch supports swipe navigation, pinch zoom and pan. Browser clipboard/wake-lock permission failures are recoverable; they do not affect viewing.

**Fit cells** letterboxes grid thumbnails. Select 2–4 images and press **X** for comparison, with a shared zoom factor relative to each pane's fit size and normalized pan. Wheel or `+`/`−` zooms all panes, drag pans them, and double-click, `F` or `0` resets. Esc exits. The info panel groups readable EXIF into collapsible sections. Broken thumbnails include the decoder's error text.

`R` only rotates the view. **Ctrl+R** confirms a permanent clockwise 90° rotation; the API also accepts 180° and 270°. It writes a temporary image beside the original, preserves EXIF, resets orientation to 1, checks for concurrent source changes, then atomically replaces the file. JPEGs are re-encoded at quality 95. There is no rotation undo. Animated/multi-frame images and SVG are refused. **W** confirms desktop wallpaper replacement; unsupported Windows raster formats use a full PNG cache render. SVG wallpaper is unavailable because no SVG rasterizer is installed.

## Storage and file safety

Default storage is `%LOCALAPPDATA%\PhotoExplorer`: `index.db` and a `thumbs/` cache. Override with `PHOTO_EXPLORER_HOME` or `--home`. Preferences, ratings and favourites live in SQLite only; they are never embedded in source files. Stop the server before removing cache files. Removing `thumbs/` is safe: images regenerate on request. Removing `index.db` also clears libraries, ratings, favourites, destinations and settings. The UI mirrors preferences to localStorage; clear site storage as well for a full preference reset.

Cache identities hash the normalized absolute path, byte size, and nanosecond modification time. JPEG thumbs preserve display aspect and apply EXIF orientation. Previews are bounded by 2048 pixels. HEIC/HEIF, TIFF and PSD get full-size PNG renders; animated GIF/WebP use their original bytes. SVG stays browser-rendered with a restrictive response policy. Corrupt files remain listed with placeholders. Recycle Bin, move, copy and rename act only on indexed ids; overwrite is refused. Recycle Bin is the recovery route for deleted images.

The global unique path index permits one owning root per file. Overlapping roots are rejected with an explanation; use the existing folder tree or forget the old root through `DELETE /api/roots/{id}` before reopening its parent. Symlinks and hidden/system folders are skipped. Access failures stop a scan without pruning unvisited files. Re-scan after external edits; there is no filesystem watcher. Very large images are subject to Pillow's decompression-bomb protection. Rotation of EXIF-only dimensions is supported without a full decode.

## Samples and verification

Install the runtime and test dependencies before running these commands. `requirements.txt` and `pyproject.toml` describe runtime requirements; pytest and httpx are additional test dependencies. The frontend needs no build step.

```powershell
$env:PHOTO_EXPLORER_HOME = "$PWD\.work\home"
.\.venv\Scripts\python.exe tools\make_samples.py .work\samples --count 80 --seed 7
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m photo_explorer .work\samples --no-browser --port 8765 --home .work\home
```

Tests create private libraries and home directories under `.work/pytest`, replace Recycle Bin and native system calls, and exercise every sort direction and filter plus the ordered-navigation acceptance test. The optional Node runtime runs a navigation-module contract test without a browser or npm. A deprecation warning from the installed Starlette/httpx test integration may appear; the installed client remains usable.

No videos, RAW decoding, editing controls, maps, cloud services, archives or duplicate detection are included.
