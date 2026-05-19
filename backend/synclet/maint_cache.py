"""TTL cache for expensive maintenance filesystem walks.

`find_watched_synced_files`, `find_hanging_files`, and `compute_pending`
each iterate SYNC_ROOT, which is shfs FUSE on the deploy target and ~10x
slower than native ext4 for rglob-style ops. The Maintenance tab hits
all three on every render (via the four producer endpoints AND the
counts endpoint), so a tab switch can take noticeable seconds.

This module wraps each producer with a TTL cache that mirrors the
state-grid pattern in `synclet/state.py`. Mutating actions
(sync/unsync/remove/resolve/ignore/unignore) call `invalidate()` to
clear the cache; the next read recomputes once and serves subsequent
reads from memory for STATE_CACHE_TTL seconds.

Cache lives in-process. Two implications:
- A uvicorn restart cold-starts the cache. Acceptable.
- A multi-worker uvicorn would have per-worker caches that drift. Not
  a concern today (single-worker config); flagged here for future.
"""

from __future__ import annotations

import time
from typing import Any

from synclet.config import STATE_CACHE_TTL

_cache: dict[str, tuple[float, Any]] = {}


def get_cached(key: str, build: callable) -> Any:  # noqa: ANN401 — generic cache value
    """Return cached value for `key`, recomputing via `build()` on miss/expire.

    The `build` callable runs synchronously and replaces the cache entry
    on every recompute. Errors propagate so callers see them on the API
    boundary instead of silently caching empty data.
    """
    now = time.time()
    entry = _cache.get(key)
    if entry is not None and now - entry[0] < STATE_CACHE_TTL:
        return entry[1]
    value = build()
    _cache[key] = (now, value)
    return value


def invalidate() -> None:
    """Clear all maintenance caches.

    Called by every mutating action so the next read recomputes. Cheap;
    the next read pays the FS-walk cost once, subsequent reads are free
    until TTL.
    """
    _cache.clear()
