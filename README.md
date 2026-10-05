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

The global unique path index permits one owning root per file. Overlapping roots are rejected with an explanation; use the existing folder tree or forget the old root through `DELETE /api/roots/{id}` before reopening its parent. Symlinks and hidden/system folders are skipped. Unreadable subfolders are skipped without pruning their indexed files. Live watching updates external changes; Rescan remains available. Very large images are subject to Pillow's decompression-bomb protection. Rotation of EXIF-only dimensions is supported without a full decode.

## Samples and verification

Install the runtime and test dependencies before running these commands. `requirements.txt` and `pyproject.toml` describe runtime requirements; pytest and httpx are additional test dependencies. The frontend needs no build step.

```powershell
$env:PHOTO_EXPLORER_HOME = "$PWD\.work\home"
.\.venv\Scripts\python.exe tools\make_samples.py .work\samples --count 80 --seed 7
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m photo_explorer .work\samples --no-browser --port 8765 --home .work\home
```

Tests create private libraries and home directories under `.work/pytest`, replace Recycle Bin and native system calls, and exercise every sort direction and filter plus the ordered-navigation acceptance test. The optional Node runtime runs a navigation-module contract test without a browser or npm. A deprecation warning from the installed Starlette/httpx test integration may appear; the installed client remains usable.

No videos, RAW decoding, editing controls, maps, cloud services or archives are included.

## Folder tools and recoverable operations

**Tools** opens Operations, Flatten, Rules, and Export structure. The folder context menu also opens these tools for that folder. The header mode switches between **This folder only** and **Include subfolders**; the API uses `dir_mode=exact` or `subtree` (the unchanged default). Counts distinguish direct images, other files and child folders; recursive image counts remain available in the folder tooltip.

Every move, copy, rename, Recycle Bin action and batch has an operation id and a manifest under `home/operations/`. **Ctrl+Z**, a toast's ten-second **Undo** button, or the Operations panel reverses a completed operation after naming it in a confirmation. Undo never overwrites another file. Conflicts are recorded per entry and other entries continue. Repeated undo returns the same result. Copies are undone through the Recycle Bin. Trashed files must be restored from the Recycle Bin; on-disk rotation remains irreversible and is recorded as such.

Flatten previews file moves or copies from nested folders. It supports folder-chain prefixes, nested bracket-tag stripping, prefix caps, extension/exclude filters, case-insensitive numbered collisions, and byte-identical skipping. Run always replans; a changed file count requires another preview. Move undo recreates removed empty folders. A destination inside the target subtree is refused unless it is the target itself.

Rules support all/any conditions over names (including stem suffix `-t`), formats, sizes, dimensions, dates, orientation, ratings, favourites, camera, metadata failures and relative directories. Five editable, deletable starter presets are installed once. Preview shows matching files and resolved destination names. Rules can include other files or be limited to selected images. Destinations containing an already matched file are refused to prevent repeated processing.

Structure export produces a plain text tree, files before directories, with skip-by-name/path options. Preview shows 400 lines; Copy and Save include everything. Save uses `Directory Structure/<folder> Structure.txt` beneath the selected source or destination folder.

New APIs: `/api/operations`, `/api/operations/{id}/undo`, `/api/operations/{id}/cancel`, `/api/operations/batch`, `/api/flatten/plan`, `/api/flatten/run`, `/api/rules/preview`, `/api/rules/run`, `/api/rules`, `/api/fs/structure`, and `/api/fs/structure/save`. Operations over 200 entries return an id immediately; poll the journal for progress. Completed runs with per-entry failures can undo their successful entries. Interrupted or cancelled runs remain inspectable and require manual recovery using their entries/manifests.

## Cache, backups, and folder watching

Settings adds **Cache** and **Library** sections. The default cap is 2048 MB, adjustable from 256 MB to 16 GB. Old unused derivatives are evicted after scans, after warming batches, and hourly. Files used in the last ten minutes and the viewer's current neighbourhood are protected, so interactive use can temporarily exceed the cap. Cache hits collect touches in memory and flush them every minute and at shutdown. **Clear cache** is safe while the app is running; files regenerate as needed.

Idle pre-warming generates 256-pixel thumbnails in batches of 32, pauses during interactive traffic, and stops before the cap. A conservative reservation can leave some unused space at the cap. `/api/cache` returns usage, cap and last eviction. `/api/cache/clear` optionally accepts `root_id`.

SQLite's online backup API saves one consistent `home/backups/index-YYYYMMDD.db` per day, retaining seven. `/api/health` includes `last_backup`. Stop the app before restoring a backup over `index.db`.

The watcher coalesces changes for 1.5 seconds, updates up to 200 affected files directly, and uses an incremental rescan for larger changes or directory events. Native watching falls back to polling if startup fails. Problems appear beside scan progress; manual Rescan stays available. Root revisions refresh the grid while preserving scroll and locating the current viewer image again. The browse dialog lazily expands folders with direct image counts.

## Exact duplicates

**Tools → Duplicates** hashes only files sharing a size, reuses SHA-256 results while size and modified time match, and displays exact-copy groups. Choose a keeper, preview the affected count, then move the others to an existing folder or explicitly confirm sending them to the Recycle Bin. Bulk resolution applies to every group in the current scan, including unloaded pages. Resolution rechecks file bytes and refuses stale groups. Moves use the operations journal and can be undone; trash recovery uses the Recycle Bin. Scan progress is in `dup_done` / `dup_total` / `dup_running` on the library scan endpoint.

Near-duplicate detection is not included; this feature finds files with identical content, not visually similar images.

## Upgrade compatibility

Opening a 0.1.0 index adds journal, rule, folder-count, hash and duplicate tables plus a guarded `roots.revision` column and `settings.schema_version=2`. Cache rows migrate to a derivative-specific key while the original rows remain in `cache_keys_legacy`. Images and annotations are not rebuilt by migration. Saved operation snapshots reserve deleted image ids until undo can restore them. Libraries can still use every original endpoint and filter; single-file action responses add `operation_id`.
