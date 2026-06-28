"""Background-refresh cache for expensive backend reads.

Started life as a Maintenance-tab-only TTL cache, then broadened to back
/api/state, /api/synced, /api/watchlist, /api/coverage and the maintenance
walks. The shape changed in 2026-06: it no longer rebuilds on the read path.

WHY THE FLIP. The old contract built synchronously on cache miss/expire, so
every time a TTL lapsed the next user click paid the full build cost (synced
~25s, maint counts ~19s on Jake's library). That is the stall the grid felt as
"slow tabs". The fix is not a faster build, it is to take the build OFF the
read path entirely:

  - A read ALWAYS returns the last-good in-memory value, instantly. A read
    NEVER triggers a build, except the one cold first-boot miss for a key that
    nothing has primed yet.
  - A background loop (main.cache_refresh_loop) rebuilds keys off the request
    path: dirty keys every CACHE_DIRTY_REFRESH_S (prompt after a mutation), and
    every registered key every CACHE_FULL_REFRESH_S (picks up external changes
    from WatchState's own import poll and Syncthing propagation).
  - Mutations (sync/unsync/remove/ignore) call invalidate(), which FLAGS the
    key dirty WITHOUT dropping its value. The stale value keeps serving until
    the loop rebuilds it; the frontend's optimistic overlay covers the gap.

Because state changes slowly and WatchState is the source of truth for watch
state (on its own poll cadence), serving data a few minutes old is fine, and no
read ever blocks.

Cache lives in-process. Two implications, unchanged from before:
- A uvicorn restart cold-starts the cache. The background loop's first pass
  (and the startup prewarm in main) primes the heaviest entries.
- A multi-worker uvicorn would have per-worker caches that drift. Not a concern
  today (single-worker config); flagged here for future.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from common.log_utils import get_logger

logger = get_logger(__name__)

# key -> last-good value. Reads serve from here and never write here except the
# cold first-boot miss; the background loop owns every other write.
_cache: dict[str, Any] = {}
# key -> builder, registered the first time a key is requested (or via
# register()), so the background loop knows how to rebuild it.
_builders: dict[str, Callable[[], Any]] = {}
# keys awaiting a background rebuild, set by invalidate(). Serving the last-good
# value continues until the loop clears the flag.
_dirty: set[str] = set()
# Guards the three structures above against the background thread racing the
# request worker threads (both run builds off the event loop via to_thread).
_lock = threading.Lock()


def register(key: str, build: Callable[[], Any]) -> None:
    """Register a builder so the background loop can refresh `key`.

    Idempotent. get_cached() also registers on first use, so explicit
    registration is only needed to prime the loop for a key no read has hit
    yet (e.g. so the very first full-refresh pass builds it).
    """
    with _lock:
        _builders[key] = build


def get_cached(key: str, build: Callable[[], Any]) -> Any:
    """Return the last-good cached value for `key`, never rebuilding on read.

    The build callable is registered for the background loop. A read runs
    build() synchronously ONLY on the cold first-boot miss, before the loop or
    prewarm has primed the key; every subsequent read serves memory in
    microseconds, even when the value is stale or a rebuild is in flight. This
    is what makes tabs load instantly: the expensive build runs in the
    background, never under a user click.
    """
    with _lock:
        _builders[key] = build
        if key in _cache:
            return _cache[key]
    # Cold first-boot miss: build once, synchronously, so the first caller
    # still gets data. Every later caller takes the in-cache branch above.
    value = build()
    with _lock:
        _cache[key] = value
        _dirty.discard(key)
    return value


def peek(key: str, build: Callable[[], Any]) -> tuple[bool, Any]:
    """Return (present, last-good value) for `key` WITHOUT ever building.

    Registers the builder and, when the key is absent, flags it dirty so the
    background loop fills it on its next pass. Unlike get_cached, this never
    builds even on the cold first-boot miss, so a read stays instant. Use for
    expensive enrichment that should layer onto a fast base payload once it
    lands (the /api/synced new-unwatched badges). `present` is False until the
    loop has built the key at least once.
    """
    with _lock:
        _builders[key] = build
        if key in _cache:
            return True, _cache[key]
        _dirty.add(key)
        return False, None


def invalidate(key: str | None = None) -> None:
    """Flag keys for background rebuild WITHOUT dropping the cached value.

    Mutations call this. The OLD contract cleared the cache so the next read
    rebuilt synchronously, which was the 25s stall. New contract: the value
    keeps serving and the background loop rebuilds it on its next pass (within
    CACHE_DIRTY_REFRESH_S). Passing None flags every registered key.
    """
    with _lock:
        if key is None:
            _dirty.update(_builders)
        else:
            _dirty.add(key)


def _rebuild(key: str) -> None:
    """Rebuild one key, swapping in the new value. Keeps last-good on error."""
    with _lock:
        build = _builders.get(key)
    if build is None:
        return
    try:
        value = build()
    except Exception:
        logger.exception("cache rebuild failed for %r; keeping last-good", key)
        return
    with _lock:
        _cache[key] = value
        _dirty.discard(key)


def run_refresh_cycle(*, full: bool) -> list[str]:
    """Rebuild dirty keys (always) plus every registered key (when `full`).

    Called by the background loop. `full` drives the periodic all-keys refresh
    that picks up external changes (WatchState's poll, Syncthing propagation);
    the dirty set drives prompt rebuilds after a mutation. Returns the rebuilt
    keys, for logging.
    """
    with _lock:
        due = set(_dirty)
        if full:
            due.update(_builders)
        _dirty.clear()
    for key in sorted(due):
        _rebuild(key)
    return sorted(due)


def clear() -> None:
    """Drop all cached values, builders, and dirty flags.

    Full reset of the engine. Used by tests between cases; not called in normal
    operation (mutations use invalidate(), which preserves last-good).
    """
    with _lock:
        _cache.clear()
        _builders.clear()
        _dirty.clear()
