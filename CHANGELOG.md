# Changelog

## 0.3.0

- Added a windowed Windows folder distribution, versioned ZIP, generated icon and Windows version metadata.
- Added frozen resource lookup and an isolated native folder-picker mode with UTF-8 pipe output.
- Added rotating file logs, exception hooks, diagnostics APIs and a Diagnostics section in Settings.
- Added dedicated browser profiles, process lifetime tracking, reload grace, page heartbeats and graceful Quit.
- Added an optional tray icon with Open window, Keep running in background, Diagnostics and Quit.
- Added per-home single-instance ownership, stale-lock recovery and second-launch folder forwarding.
- Added offline build tooling, a fresh-home executable smoke covering HEIC decoding, and desktop lifecycle tests.
- Retained the source workflow, existing library features, settings, shortcuts and endpoints.

## 0.2.0

- Added a persistent operations journal, manifests, cancellable batches, conservative undo, and Ctrl+Z.
- Added exact-folder browsing, direct image/file/folder counts, breadcrumb navigation, folder context actions, and structure export.
- Added flatten planning, prefix cleanup, collision handling, duplicate skipping, reversible moves and copies, and empty-folder cleanup.
- Added validated move/copy rules, condition previews, saved presets, other-file matching, and selection scopes.
- Added cache accounting, protected eviction, idle pre-warming, and daily SQLite backups.
- Added debounced live folder watching, revision-based refresh, and a lazy browse tree.
- Added exact duplicate scans, keeper selection, previewed bulk resolution, and journaled undo for moves.
- Near-duplicate matching remains deferred pending an index that meets recall and scaling requirements.
- Preserved existing navigation, filters, file actions, image ids, ratings, favourites, and shuffle keys.

## 0.1.0

- Initial offline library, virtual grid, ordered viewer, metadata, filters, file actions, and image caches.
