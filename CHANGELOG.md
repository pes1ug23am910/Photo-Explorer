# Changelog

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
