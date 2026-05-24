"""Read Jake's watch history.

The primary source is the WatchState SQLite DB (Plex aggregator that also pulls
from Jellyfin). For Plex library sections that WatchState's daemon does not
index (notably section 6 / YouTube and any under-tracked 4K section), we fall
back to reading Plex's `viewCount` / `viewedLeafCount` directly. Without that
fallback, Synclet's Mark-watched gesture lands in Plex but never re-surfaces
in Synclet's grid, so checkmarks appear and then revert after the frontend's
session-only optimistic overlay clears on page reload.

WatchState wins when it has any rows for a title; Plex fills the gap. We do
not merge per-episode for shows where WatchState has partial coverage , both
sources are read at the show aggregate level for the grid, and at per-episode
level only for the detail drawer (where we have lib/folder to disambiguate).

The DB is opened with `immutable=1` because the file lives on a read-only bind
mount, sqlite's WAL mode wants to create -wal/-shm sidecars even for mode=ro
which fails with "unable to open database file". A fresh connection is opened
per call so writers in the WatchState daemon are picked up on the next read
despite the "immutable" promise.
"""

from __future__ import annotations

import concurrent.futures
import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache

from synclet.config import LIBRARIES, WATCHSTATE_DB


@dataclass(frozen=True, slots=True)
class ShowAggregate:
    """Aggregate counters for a single show, used by the grid."""

    watched: int
    total: int


def _conn() -> sqlite3.Connection | None:
    if not WATCHSTATE_DB.exists():
        return None
    return sqlite3.connect(f"file:{WATCHSTATE_DB}?immutable=1", uri=True)


def _strip_year(title: str) -> str:
    return re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()


# ── Per-title reads (WatchState + Plex fallback) ────────────────────────────


def _ws_show_map(title: str) -> dict[tuple[int, int], bool]:
    """{(season, episode): watched} from WatchState for a single show."""
    c = _conn()
    if c is None:
        return {}
    try:
        plex_title = _strip_year(title)
        rows = c.execute(
            "SELECT season, episode, watched FROM state"
            " WHERE type='episode' AND title=? COLLATE NOCASE",
            (plex_title,),
        ).fetchall()
        return {
            (int(s), int(e)): bool(w)
            for s, e, w in rows
            if s is not None and e is not None
        }
    finally:
        c.close()


def _ws_movie_state(title: str) -> bool | None:
    """Watched/unwatched/missing from WatchState for a single movie."""
    c = _conn()
    if c is None:
        return None
    try:
        plex_title = _strip_year(title)
        row = c.execute(
            "SELECT watched FROM state WHERE type='movie' AND title=? COLLATE NOCASE LIMIT 1",
            (plex_title,),
        ).fetchone()
        if row is None:
            return None
        return bool(row[0])
    finally:
        c.close()


def show_watch_map(
    title: str,
    *,
    lib: str | None = None,
    folder: str | None = None,
) -> dict[tuple[int, int], bool]:
    """{(season, episode): watched_bool} for a TV/YouTube show.

    WatchState is authoritative when it has rows. When WatchState returns
    nothing AND lib/folder are provided, fall back to Plex's per-episode
    viewCount (synclet.plex.episode_watch_map). The fallback is keyword-only
    so legacy call sites that pass only `title` keep their existing behavior.
    """
    ws = _ws_show_map(title)
    if ws:
        return ws
    if lib is None or folder is None:
        return {}
    return _plex_show_watch_map(lib, folder)


def movie_watch_state(
    title: str,
    *,
    lib: str | None = None,
    folder: str | None = None,
) -> bool | None:
    """True if watched, False if known unwatched, None if not in WatchState.

    Falls back to Plex section_index's viewCount when WatchState has no row
    AND lib/folder are provided.
    """
    ws = _ws_movie_state(title)
    if ws is not None:
        return ws
    if lib is None or folder is None:
        return None
    return _plex_movie_state(lib, folder)


def _plex_show_watch_map(lib: str, folder: str) -> dict[tuple[int, int], bool]:
    """Plex-direct per-episode read. One /allLeaves call (cached) per show."""
    from synclet.plex import episode_watch_map, find_in_library

    meta = find_in_library(lib, folder)
    if not meta:
        return {}
    rk = meta.get("ratingKey")
    if not rk:
        return {}
    return episode_watch_map(rk)


def _plex_movie_state(lib: str, folder: str) -> bool | None:
    """Plex-direct movie read. Pulled from the cached section_index."""
    from synclet.plex import find_in_library

    meta = find_in_library(lib, folder)
    if not meta:
        return None
    return meta.get("view_count", 0) > 0


# ── Parallel section_index fetch ─────────────────────────────────────────────


def _fetch_indices_parallel(section_ids: list[int]) -> list[dict[str, dict]]:
    """Call synclet.plex.section_index for each id concurrently, preserving order.

    section_index is sync (it wraps urllib.request.urlopen). Running the
    per-library fetches serially turned /api/state cold-load into N * single-
    section-RTT. A ThreadPoolExecutor lets the network round-trips overlap
    without pulling the whole call chain into asyncio.

    The lru_cache on section_index dedupes per-id calls within a single worker
    process; this helper just parallelizes the first-cold fills.
    """
    from synclet.plex import section_index

    if not section_ids:
        return []
    # max_workers caps at the number of unique sections — 5 in production —
    # so we don't spin up a wide pool for a tiny job. ex.map preserves input
    # order so callers can zip against their source iterable.
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, len(section_ids))
    ) as ex:
        return list(ex.map(section_index, section_ids))


# ── Bulk reads for the grid (WatchState + Plex merged) ──────────────────────


@lru_cache(maxsize=1)
def _ws_all_shows() -> dict[str, dict[tuple[int, int], bool]]:
    """{title_lower: {(s,e): watched}} for every episode WatchState knows."""
    c = _conn()
    if c is None:
        return {}
    out: dict[str, dict[tuple[int, int], bool]] = {}
    try:
        for title, s, e, w in c.execute(
            "SELECT title, season, episode, watched FROM state WHERE type='episode'"
        ):
            if title is None or s is None or e is None:
                continue
            key = title.lower().strip()
            out.setdefault(key, {})[int(s), int(e)] = bool(w)
        return out
    finally:
        c.close()


@lru_cache(maxsize=1)
def _ws_all_movies() -> dict[str, bool]:
    """{title_lower: watched} from WatchState (movies only)."""
    c = _conn()
    if c is None:
        return {}
    out: dict[str, bool] = {}
    try:
        for title, w in c.execute(
            "SELECT title, watched FROM state WHERE type='movie'"
        ):
            if title is None:
                continue
            out[title.lower().strip()] = bool(w)
        return out
    finally:
        c.close()


@lru_cache(maxsize=1)
def all_show_aggregates() -> dict[str, ShowAggregate]:
    """{title_lower: ShowAggregate(watched, total)} merged across sources.

    Both sources contribute, max-merged per field. WatchState knows Jellyfin
    views Plex does not (cross-server aggregation); Plex knows views WatchState
    cannot see (sections its daemon does not index, or scrobbles too recent for
    its poll cycle). DO NOT switch back to "WatchState wins when present" , for
    a YouTube show with 2 Jellyfin rows in WatchState but 11 actual episodes on
    Plex, that strategy under-reports total to 2 and breaks the grid badge.
    MAX honors both sides without losing data from either.
    """
    out: dict[str, ShowAggregate] = {}
    for ws_key, ep_map in _ws_all_shows().items():
        watched = sum(1 for v in ep_map.values() if v)
        out[ws_key] = ShowAggregate(watched=watched, total=len(ep_map))

    show_sections = [
        info["plex_section"]
        for info in LIBRARIES.values()
        if info["kind"] in ("show", "youtube")
    ]
    for idx in _fetch_indices_parallel(show_sections):
        for plex_key, item in idx.items():
            if item.get("tag") != "Directory":
                continue
            plex_watched = item.get("viewed_leaf_count", 0)
            plex_total = item.get("leaf_count", 0)
            existing = out.get(plex_key)
            if existing is None:
                out[plex_key] = ShowAggregate(
                    watched=plex_watched,
                    total=plex_total,
                )
            else:
                out[plex_key] = ShowAggregate(
                    watched=max(existing.watched, plex_watched),
                    total=max(existing.total, plex_total),
                )
    return out


@lru_cache(maxsize=1)
def all_movie_watched() -> dict[str, bool]:
    """{title_lower: watched_bool} merged across sources.

    Both sources contribute, OR-merged: watched = WatchState.True OR Plex's
    view_count > 0. Symmetric reasoning to all_show_aggregates , a WatchState
    `False` for a movie Plex has now seen played (lag, or a Jellyfin-only
    aggregation that never picked up the Plex view) must not mask the Plex
    signal. Used by the grid to render movie watched% badges; per-title reads
    go through `movie_watch_state(..., lib=, folder=)`.
    """
    out: dict[str, bool] = dict(_ws_all_movies())
    movie_sections = [
        info["plex_section"] for info in LIBRARIES.values() if info["kind"] == "movie"
    ]
    for idx in _fetch_indices_parallel(movie_sections):
        for plex_key, item in idx.items():
            if item.get("tag") != "Video":
                continue
            plex_watched = item.get("view_count", 0) > 0
            out[plex_key] = out.get(plex_key, False) or plex_watched
    return out


# ── Diagnostic: per-section WatchState coverage ────────────────────────────


@dataclass(frozen=True, slots=True)
class CoverageStat:
    """Per-library WatchState coverage signal for the /api/coverage diagnostic.

    `watchstate_rows` counts state rows in WatchState that join to titles in
    this Plex section (episode rows for show kinds, movie rows for movie
    kinds). `expected_rows` is what WatchState WOULD have if it indexed the
    section fully , for shows that is `sum(leafCount)` across the section
    (total episodes), for movies it is the section_index size (one row per
    movie). Frontend compares the two: significant gap → Plex-direct read is
    doing real work for this library, surface a banner.
    """

    watchstate_rows: int
    expected_rows: int


def coverage_counts() -> dict[str, CoverageStat]:
    """{lib_id: CoverageStat} per library, joining WatchState to section_index.

    Single DB scan plus one cached Plex call per library; cheap enough to call
    on every /api/coverage hit. CoverageStat carries both the observed
    WatchState row count and what we'd expect if coverage were complete, so
    the frontend can render an honest banner instead of a binary "missing/not"
    that misses the partial-coverage case (e.g. Jellyfin rows leaking into a
    section the Plex daemon never indexed).
    """
    c = _conn()
    ws_title_counts: dict[str, int] = {}
    if c is not None:
        try:
            for title, n in c.execute(
                "SELECT lower(title), COUNT(*) FROM state GROUP BY lower(title)"
            ):
                if title is None:
                    continue
                ws_title_counts[title.strip()] = n
        finally:
            c.close()

    lib_items = list(LIBRARIES.items())
    section_ids = [info["plex_section"] for _, info in lib_items]
    indices = _fetch_indices_parallel(section_ids)

    out: dict[str, CoverageStat] = {}
    for (lib_id, info), idx in zip(lib_items, indices, strict=True):
        observed = sum(ws_title_counts.get(key, 0) for key in idx)
        if info["kind"] in ("show", "youtube"):
            expected = sum(item.get("leaf_count", 0) for item in idx.values())
        else:
            # One row per movie if WatchState saw it at all.
            expected = sum(1 for item in idx.values() if item.get("tag") == "Video")
        out[lib_id] = CoverageStat(
            watchstate_rows=observed,
            expected_rows=expected,
        )
    return out


def invalidate_cache() -> None:
    """Bust every cached read , both WatchState bulk reads and the Plex-merged
    aggregates. Called when state.py forces a fresh state build."""
    _ws_all_shows.cache_clear()
    _ws_all_movies.cache_clear()
    all_show_aggregates.cache_clear()
    all_movie_watched.cache_clear()
