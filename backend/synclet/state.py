"""Aggregate state for the grid view: scan + watchstate joined per title.

Served from the background-refresh cache (synclet.maint_cache) so the grid
loads instantly: the scan + watchstate join runs off the request path, never
under a user click. The grid needs enough data to render watched% / synced%
badges without round-trips per title, so we precompute those aggregates here.
"""

from __future__ import annotations

from dataclasses import dataclass

from synclet import maint_cache
from synclet.config import SYNC_ROOT
from synclet.fs_helpers import synced_title_sizes
from synclet.scan import Title, scan_titles, watchstate_key
from synclet.watchstate import (
    all_movie_watched,
    all_show_aggregates,
    invalidate_cache,
)

_STATE_KEY = "state"
_DISK_KEY = "disk_usage"


@dataclass
class TitleWithState:
    base: Title
    watched_count: int
    watched_pct: int
    synced_pct: int

    def to_dict(self) -> dict:
        d = self.base.to_dict()
        d["watched_count"] = self.watched_count
        d["watched_pct"] = self.watched_pct
        d["synced_pct"] = self.synced_pct
        return d


def _build() -> list[TitleWithState]:
    invalidate_cache()  # force fresh watchstate read
    shows = all_show_aggregates()
    movies = all_movie_watched()

    out: list[TitleWithState] = []
    for t in scan_titles():
        ws_key = watchstate_key(t.folder)
        watched = 0
        synced_pct = 0
        if t.kind in ("show", "youtube"):
            agg = shows.get(ws_key)
            watched = agg.watched if agg else 0
            denom = max(t.ep_count, 1)
            watched_pct = min(100, int(watched / denom * 100)) if t.ep_count else 0
            synced_pct = (
                min(100, int(t.synced_files / denom * 100)) if t.ep_count else 0
            )
        else:
            watched = 1 if movies.get(ws_key) else 0
            watched_pct = 100 if watched else 0
            synced_pct = 100 if t.has_synced else 0

        out.append(
            TitleWithState(
                base=t,
                watched_count=watched,
                watched_pct=watched_pct,
                synced_pct=synced_pct,
            )
        )
    return out


def get_state(force: bool = False) -> list[TitleWithState]:
    """Return the grid state, served from the background-refresh cache.

    `force=True` flags the entry dirty so the background loop rebuilds it on
    its next pass; it does NOT rebuild synchronously, so /api/state?refresh and
    /api/refresh stay non-blocking. The caller gets the current last-good value
    immediately and the fresh build lands within CACHE_DIRTY_REFRESH seconds.
    """
    if force:
        maint_cache.invalidate(_STATE_KEY)
    return maint_cache.get_cached(_STATE_KEY, _build)


def invalidate() -> None:
    """Flag the grid state (and its watchstate inputs) for background rebuild."""
    maint_cache.invalidate(_STATE_KEY)
    invalidate_cache()


def _disk_usage_build() -> dict:
    import shutil

    usage = shutil.disk_usage(str(SYNC_ROOT))

    # Reuse the single scandir-backed walk that also feeds /api/synced instead
    # of a second deep rglob+stat traversal of SYNC_ROOT. synced_title_sizes
    # keys every synced title dir (0 for empties), so its length is the title
    # count and its values sum to the synced byte total.
    sizes = synced_title_sizes()

    return {
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "pct": int(usage.used / usage.total * 100) if usage.total else 0,
        "synced_titles": len(sizes),
        "synced_bytes": sum(sizes.values()),
    }


def disk_usage() -> dict:
    """Free/used bytes on the sync mount, plus rolled-up synced count.

    Served from the background-refresh cache; the disk stat + SYNC_ROOT walk
    runs off the request path.
    """
    return maint_cache.get_cached(_DISK_KEY, _disk_usage_build)


def register_builders() -> None:
    """Register the grid-state and disk-usage builders with the cache engine.

    Called once at startup so the loop's full-refresh pass rebuilds them even
    before any request has touched them.
    """
    maint_cache.register(_STATE_KEY, _build)
    maint_cache.register(_DISK_KEY, _disk_usage_build)
