# Architecture

Photo Explorer runs a local FastAPI service and a browser interface on Windows. SQLite stores library metadata, preferences and operation records; original images remain in the folders the user opens. HTML, CSS and JavaScript ship with the application. The browser requests local API data and image derivatives without a frontend build step.

## Shared ordering

[db.py](../photo_explorer/db.py) defines the filters and ordering in one parameterized query builder. Listings, counts, positions and aggregates reuse it. Each ordering ends with natural filename and image ID tie-breakers; nullable dimensions and camera values sort last in either direction. The viewer requests positions and nearby items using the same sort and filter parameters as the grid.

A listing's count and page come from one SQLite read transaction. This is a snapshot for that request: background metadata work or filesystem changes can change later results. The interface refreshes when the library revision changes. See [state.js](../photo_explorer/static/js/state.js), [grid.js](../photo_explorer/static/js/grid.js) and [viewer.js](../photo_explorer/static/js/viewer.js).

## Indexing and visible work

[scanner.py](../photo_explorer/scanner.py) first indexes file attributes in batches, then reads pending metadata in batches of 64 with at most eight metadata workers. Images can appear before all metadata is available, so metadata-dependent ordering can settle during a scan. The pending-row list is materialized in memory; bounded worker batches do not imply constant memory for an entire library.

The grid renders a window of rows and caches up to 18 pages of 200 records. Query generations discard responses belonging to an older view. [layout.js](../photo_explorer/static/js/layout.js) maps visible rows and optional date headers; [thumbs.py](../photo_explorer/thumbs.py) produces derivatives. [cache_policy.py](../photo_explorer/cache_policy.py) evicts unused derivatives while protecting recent use, so its configured capacity may be temporarily exceeded.

## File operations and recovery

[operations.py](../photo_explorer/operations.py) serializes file mutations and coordinates with scan locks. It records an operation before execution, saves indexed-image snapshots, records per-entry outcomes and writes a JSON manifest beside the database. [transfers.py](../photo_explorer/transfers.py) and [fileops.py](../photo_explorer/fileops.py) perform the underlying filesystem work. Larger operations run in a background worker; cancellation takes effect between entries.

Undo checks for changed files and conflicting destinations. Copies are undone through the Recycle Bin; trashed files require restoration from the Recycle Bin, and on-disk rotation has no undo. The filesystem action, index update and outcome record are separate steps, not one atomic transaction. A crash between them can require manual inspection and recovery. Previously running operations are marked failed on startup; interrupted and cancelled runs are not automatically replayed or rolled back. The journal supports inspection and guarded undo, not a guarantee of crash-atomic file operations.

## Exact duplicates

[duplicates.py](../photo_explorer/duplicates.py) first groups indexed files by size, then hashes candidates with SHA-256 in one-MiB chunks. Cached hashes are reused while size and modification time match. Resolution checks file content again, selects a keeper and sends moves or Recycle Bin actions through the operation journal. Only exact-content grouping is implemented; visual similarity and near-duplicate search are outside this version.

## Desktop lifecycle

[__main__.py](../photo_explorer/__main__.py) starts the loopback service. [app.py](../photo_explorer/app.py) checks the host, client address and browser origin. This is a local desktop application, not a remotely hosted multi-user service.

[launcher.py](../photo_explorer/launcher.py) opens an Edge or Chrome app window with a dedicated browser profile, with a default-browser fallback. [single_instance.py](../photo_explorer/single_instance.py) coordinates launches sharing the same home directory. [lifecycle.py](../photo_explorer/lifecycle.py) combines browser-process state, page heartbeats and a close grace period; explicit headless or keep-running modes change that behavior. [tray.py](../photo_explorer/tray.py) provides optional desktop controls.

Shutdown requests cancellation, stops watchers and closes owned workers. Cancellation is cooperative: a native filesystem or decoder call can outlast the shutdown wait. Automated checks suppress desktop UI, so browser focus, tray behavior and real-file workflows also need manual verification.
