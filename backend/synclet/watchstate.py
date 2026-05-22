"""Read Jake's watch history from the WatchState SQLite DB.

WatchState aggregates Plex view counts per user. We open the DB read-only with
URI mode so the WatchState daemon's writers are never blocked.

The schema (v02) stores each watch event as a row in `state` with the SHOW name
in `title`, plus season/episode columns. Movies use `type='movie'` with
season/episode NULL.
"""

from __future__ import annotations

import re
import sqlite3
from functools import lru_cache

from synclet.config import WATCHSTATE_DB


def _conn() -> sqlite3.Connection | None:
    # `immutable=1` is required because the file lives on a read-only bind mount
    # — sqlite's WAL mode wants to create/update sidecar -wal/-shm files even
    # for `mode=ro`, which fails with "unable to open database file". Since we
    # re-open a fresh connection per call, sqlite picks up new WatchState writes
    # on the next read regardless of the "immutable" promise.
    if not WATCHSTATE_DB.exists():
        return None
    return sqlite3.connect(f"file:{WATCHSTATE_DB}?immutable=1", uri=True)


def _strip_year(title: str) -> str:
    return re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()


def show_watch_map(title: str) -> dict[tuple[int, int], bool]:
    """{(season, episode): watched_bool} for a TV/YouTube show."""
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


def movie_watch_state(title: str) -> bool | None:
    """True if watched, False if known unwatched, None if not in WatchState."""
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


@lru_cache(maxsize=1)
def all_watched_shows() -> dict[str, dict[tuple[int, int], bool]]:
    """Bulk preload: {show_title_lower: {(s,e): watched}} for every episode.

    Called once at state-build time so the grid can compute watched% per show
    without 4000 separate sqlite queries. lru_cache holds the result; invalidate
    by clearing the cache (we do that when state refreshes).
    """
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
def all_watched_movies() -> dict[str, bool]:
    """{movie_title_lower: watched_bool} bulk preload."""
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


def invalidate_cache() -> None:
    all_watched_shows.cache_clear()
    all_watched_movies.cache_clear()
