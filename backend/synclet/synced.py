"""Synced-tab data assembly with TTL cache.

The /api/synced response is expensive to compute (one scan_title_detail
per show + a WatchState/Plex episode-watch lookup + per-title byte
totals across SYNC_ROOT). Without caching, every tab click pays the
full cost — observed at 12.9s on Jake's library, even on warm hits.

This module:
  - Builds the response with a single SYNC_ROOT walk for byte totals
    (replacing the prior per-title rglob+stat loop).
  - Caches the result via maint_cache's TTL primitive so repeat clicks
    are sub-millisecond.
  - Invalidates alongside the existing sync/unsync/remove seams via
    maint_cache.invalidate(); no new invalidation hooks needed.

Kept separate from main.py to keep route handlers thin and to make the
build function testable in isolation.
"""

from __future__ import annotations

from synclet import maint_cache
from synclet.config import LIBRARIES
from synclet.fs_helpers import iter_synced_titles, synced_title_sizes
from synclet.scan import clean_name, scan_title_detail
from synclet.sync_ops import find_source_lib
from synclet.watchstate import show_watch_map

# Cache key inside maint_cache. Distinct from the maintenance keys
# (find_watched_synced_files, find_hanging_files, compute_pending) so
# invalidations don't blow away unrelated caches, but the underlying
# TTL/expiry mechanism is shared.
_CACHE_KEY = "synced"


def _build() -> list[dict]:
    """Compute the /api/synced payload. Caller-cached via maint_cache."""
    # Per-title byte totals via a single SYNC_ROOT sweep. ~Half the wall
    # time of the prior per-title rglob loop on shfs FUSE.
    sizes = synced_title_sizes()

    items: list[dict] = []
    for _sub_path, item in iter_synced_titles():
        source_lib = find_source_lib(item.name)
        display = clean_name(item.name)
        total_bytes = sizes.get(item.name, 0)

        entry: dict = {
            "title": display,
            "folder": item.name,
            "lib": source_lib,
            "kind": LIBRARIES[source_lib]["kind"] if source_lib else "unknown",
            "size_bytes": total_bytes,
            "new_unwatched": [],
        }

        if source_lib and LIBRARIES[source_lib]["kind"] in ("show", "youtube"):
            detail = scan_title_detail(source_lib, item.name)
            if detail:
                ws_map = show_watch_map(display, lib=source_lib, folder=item.name)
                new_eps = []
                for s in detail.seasons:
                    for e in s.episodes:
                        watched = ws_map.get((e.season, e.episode), False)
                        if not watched and not e.is_synced:
                            new_eps.append(
                                {
                                    "season": e.season,
                                    "episode": e.episode,
                                    "title": e.title,
                                    "size_bytes": e.size_bytes,
                                }
                            )
                entry["new_unwatched"] = new_eps

        items.append(entry)
    return items


def get_synced(*, force: bool = False) -> list[dict]:
    """Return the cached /api/synced payload, recomputing on miss/expire.

    `force=True` bypasses the cache for one call (and refreshes it).
    Mutations (sync/unsync/remove) invalidate via maint_cache.invalidate()
    so callers don't need to plumb force through normal request paths.
    """
    if force:
        maint_cache.invalidate()
    return maint_cache.get_cached(_CACHE_KEY, _build)
