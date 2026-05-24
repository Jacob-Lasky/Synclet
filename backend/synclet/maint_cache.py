"""TTL cache for expensive backend reads.

Started life as a Maintenance-tab-only cache (find_watched_synced_files,
find_hanging_files, compute_pending — all heavy SYNC_ROOT walks on shfs
FUSE). Since broadened to back the /api/synced and /api/watchlist
caches too — they share the same TTL discipline and the same
"mutations invalidate" pattern via the existing sync_ops seams. Module
kept named maint_cache to avoid churn; the key namespace inside is what
disambiguates callers.

Mutating actions (sync/unsync/remove/resolve/ignore/unignore) call
`invalidate()` to clear the cache; the next read recomputes once and
serves subsequent reads from memory for STATE_CACHE_TTL seconds.

Cache lives in-process. Two implications:
- A uvicorn restart cold-starts the cache. The on_startup hook in
  main.warm_plex_caches pre-primes the heaviest entries to hide this.
- A multi-worker uvicorn would have per-worker caches that drift. Not
  a concern today (single-worker config); flagged here for future.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from synclet.config import STATE_CACHE_TTL

_cache: dict[str, tuple[float, Any]] = {}


def get_cached(key: str, build: Callable[[], Any]) -> Any:
    """Return cached value for `key`, recomputing via `build()` on miss/expire.

    The `build` callable runs synchronously and replaces the cache entry
    on every recompute. Errors propagate so callers see them on the API
    boundary instead of silently caching empty data.

    Entries are stamped with the BUILD-END time, not the build-start
    time. For fast builds the difference is noise; for the long-running
    builds that motivated this cache (synced ~25s, watchlist ~8s on
    Jake's library) the start-time stamp was burning 80%+ of the TTL
    window before the cache could even be hit — defeating the startup-
    warm prefetch in main.warm_plex_caches.
    """
    entry = _cache.get(key)
    if entry is not None and time.time() - entry[0] < STATE_CACHE_TTL:
        return entry[1]
    value = build()
    _cache[key] = (time.time(), value)
    return value


def invalidate() -> None:
    """Clear all maintenance caches.

    Called by every mutating action so the next read recomputes. Cheap;
    the next read pays the FS-walk cost once, subsequent reads are free
    until TTL.
    """
    _cache.clear()
