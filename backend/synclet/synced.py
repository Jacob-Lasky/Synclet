"""Synced-tab data assembly, two-phase so the list paints instantly.

The /api/synced response has two parts with very different costs:

  - The LOCAL part (what's synced on disk + per-title byte totals) is one
    scandir-backed SYNC_ROOT walk, a few hundred ms. This is the answer to
    "what do I have offline" and must show immediately.
  - The ENRICHMENT part (per-show new-unwatched-episode hints) costs a
    scan_title_detail FS walk plus a WatchState/Plex episode lookup PER show,
    ~24s on Jake's library. It only drives a badge.

So they are cached separately under the background-refresh engine:

  - synced_local: built on the read path on first boot (cheap), then served
    from cache. Always present.
  - synced_enrichment: NEVER built on the read path (peek()), even on first
    boot. The background loop builds it; until then the list serves with empty
    badges and `enriched=False`, and the frontend re-polls to fill them in.

Both invalidate alongside the existing sync/unsync/remove seams via
maint_cache.invalidate(); no new invalidation hooks needed.
"""

from __future__ import annotations

from synclet import maint_cache
from synclet.config import LIBRARIES
from synclet.fs_helpers import iter_synced_titles, synced_title_sizes
from synclet.scan import clean_name, scan_title_detail
from synclet.sync_ops import find_source_lib
from synclet.watchstate import show_watch_map

# Cache keys inside maint_cache. Distinct from the maintenance keys
# (watched/hanging/pending) and from state/disk_usage so invalidations stay
# targeted, but the underlying background-refresh engine is shared.
_LOCAL_KEY = "synced_local"
_ENRICH_KEY = "synced_enrichment"


def _build_local() -> list[dict]:
    """Fast phase: the synced list + sizes, with empty new_unwatched.

    One SYNC_ROOT byte-walk; no per-show scan or watch-state lookup. This is
    what the Synced tab renders on first paint.
    """
    sizes = synced_title_sizes()

    items: list[dict] = []
    for _sub_path, item in iter_synced_titles():
        source_lib = find_source_lib(item.name)
        items.append(
            {
                "title": clean_name(item.name),
                "folder": item.name,
                "lib": source_lib,
                "kind": LIBRARIES[source_lib]["kind"] if source_lib else "unknown",
                "size_bytes": sizes.get(item.name, 0),
                "new_unwatched": [],
            }
        )
    return items


def _build_enrichment() -> dict[str, list[dict]]:
    """Slow phase: {folder: new_unwatched_episodes} for show/youtube titles.

    Per show: scan_title_detail (FS walk) + show_watch_map (WatchState SQLite
    with a Plex per-episode fallback for sections WatchState does not index).
    Runs only in the background loop, never under a user click.
    """
    enrichment: dict[str, list[dict]] = {}
    for _sub_path, item in iter_synced_titles():
        source_lib = find_source_lib(item.name)
        if not source_lib or LIBRARIES[source_lib]["kind"] not in ("show", "youtube"):
            continue
        display = clean_name(item.name)
        detail = scan_title_detail(source_lib, item.name)
        if not detail:
            continue
        ws_map = show_watch_map(display, lib=source_lib, folder=item.name)
        new_eps = [
            {
                "season": e.season,
                "episode": e.episode,
                "title": e.title,
                "size_bytes": e.size_bytes,
            }
            for s in detail.seasons
            for e in s.episodes
            if not ws_map.get((e.season, e.episode), False) and not e.is_synced
        ]
        if new_eps:
            enrichment[item.name] = new_eps
    return enrichment


def get_synced(*, force: bool = False) -> dict:
    """Return the /api/synced payload: {"items": [...], "enriched": bool}.

    The list is served instantly from the local cache (built on first boot).
    The new-unwatched badges are layered on from the enrichment cache IF it has
    been built; `enriched` reports whether it has. The enrichment build never
    runs on this call (see peek) so the Synced tab never blocks on it.

    `force=True` flags both phases dirty so the background loop rebuilds them on
    its next pass; it does not rebuild synchronously, keeping /api/refresh and
    sync/unsync mutations non-blocking.
    """
    if force:
        maint_cache.invalidate(_LOCAL_KEY)
        maint_cache.invalidate(_ENRICH_KEY)

    items = [dict(entry) for entry in maint_cache.get_cached(_LOCAL_KEY, _build_local)]
    enriched, enrichment = maint_cache.peek(_ENRICH_KEY, _build_enrichment)
    if enriched and enrichment:
        for entry in items:
            entry["new_unwatched"] = enrichment.get(entry["folder"], [])
    return {"items": items, "enriched": enriched}


def register_builders() -> None:
    """Register both synced phases with the background-refresh engine.

    Called once at startup so the loop's full-refresh pass builds the slow
    enrichment phase in the background; the local phase is cheap and also builds
    on the first read.
    """
    maint_cache.register(_LOCAL_KEY, _build_local)
    maint_cache.register(_ENRICH_KEY, _build_enrichment)
