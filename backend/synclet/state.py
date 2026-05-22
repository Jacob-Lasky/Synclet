"""Aggregate state for the grid view: scan + watchstate joined per title.

Cached in-process with a TTL so back-to-back API calls don't re-scan. The grid
needs enough data to render watched% / synced% badges without round-trips per
title, so we precompute those aggregates here.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass

from synclet.config import STATE_CACHE_TTL, SYNC_ROOT
from synclet.fs_helpers import iter_synced_titles
from synclet.scan import Title, scan_titles, watchstate_key
from synclet.watchstate import all_watched_movies, all_watched_shows, invalidate_cache


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


# Module-level cache as separate typed scalars, so static analysis can verify
# arithmetic on the timestamp. A dict[str, object] was forcing a # type: ignore
# in get_state because object - float isn't well-typed.
_cache_ts: float = 0.0
_cache_data: list[TitleWithState] | None = None


def _build() -> list[TitleWithState]:
    invalidate_cache()  # force fresh watchstate read
    shows = all_watched_shows()
    movies = all_watched_movies()

    out: list[TitleWithState] = []
    for t in scan_titles():
        ws_key = watchstate_key(t.folder)
        watched = 0
        synced_pct = 0
        if t.kind in ("show", "youtube"):
            ws_map = shows.get(ws_key, {})
            watched = sum(1 for v in ws_map.values() if v)
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
    global _cache_ts, _cache_data
    now = time.time()
    if force or _cache_data is None or now - _cache_ts > STATE_CACHE_TTL:
        _cache_data = _build()
        _cache_ts = now
    return _cache_data


def invalidate() -> None:
    global _cache_ts, _cache_data
    _cache_data = None
    _cache_ts = 0.0
    invalidate_cache()


def disk_usage() -> dict:
    """Free/used bytes on the sync mount, plus rolled-up synced count."""
    import shutil

    usage = shutil.disk_usage(str(SYNC_ROOT))

    synced_titles = 0
    synced_bytes = 0
    for _sub_path, item in iter_synced_titles():
        synced_titles += 1
        for f in item.rglob("*"):
            if f.is_file():
                with contextlib.suppress(OSError):
                    synced_bytes += f.stat().st_size

    return {
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "pct": int(usage.used / usage.total * 100) if usage.total else 0,
        "synced_titles": synced_titles,
        "synced_bytes": synced_bytes,
    }
