"""Shared pytest fixtures.

Most tests use a synthetic media tree under tmp_path so they don't depend on
the real `/data/media` mount. The `media_env` fixture wires every module that
holds a path binding (config + per-module re-imports) so monkeypatching is
not silently incomplete.
"""

from __future__ import annotations

import os

# Disable the background refresh loop + startup prewarm for the whole suite, so
# no live timer mutates the in-process cache mid-test. Set before synclet.config
# is imported so the module-load default is already off; the attribute override
# below covers the case where config was imported first.
os.environ.setdefault("SYNCLET_CACHE_BACKGROUND_REFRESH", "0")

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest
from litestar.testing import TestClient

from synclet import config as _config

_config.CACHE_BACKGROUND_REFRESH = False


@pytest.fixture(autouse=True)
def _refuse_unmocked_plex(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any Plex call a test forgot to mock fail at once, not after a timeout.

    CI cannot reach the committed default PLEX_URL, so an unmocked fetch used to
    sit out its full 30 s timeout (several tests did, adding minutes to the
    suite). Port 9 on localhost refuses immediately, which exercises the same
    fetch-failure path. Tests that need a Plex answer mock _get_xml or urlopen
    as before; this only changes how fast a forgotten call fails.
    """
    monkeypatch.setattr("synclet.plex.PLEX_URL", "http://127.0.0.1:9")


# ── WatchState schema (matches v02 , keep in sync with the real DB) ────────

WATCHSTATE_SCHEMA = """
CREATE TABLE state (
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    type TEXT NOT NULL,
    updated INTEGER NOT NULL,
    watched INTEGER NOT NULL DEFAULT 0,
    via TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER NULL,
    season INTEGER NULL,
    episode INTEGER NULL,
    parent TEXT NULL,
    guids TEXT NULL,
    metadata TEXT NULL,
    extra TEXT NULL,
    created_at INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL DEFAULT 0
);
"""


@pytest.fixture
def watchstate_db(tmp_path: Path) -> Path:
    """Build a temp WatchState DB with a couple watched + unwatched rows."""
    db = tmp_path / "watchstate_v02.db"
    conn = sqlite3.connect(db)
    conn.executescript(WATCHSTATE_SCHEMA)
    rows = [
        # (type, updated, watched, via, title, year, season, episode)
        ("episode", 0, 1, "plex", "Better Call Saul", 2015, 1, 1),
        ("episode", 0, 1, "plex", "Better Call Saul", 2015, 1, 2),
        ("episode", 0, 0, "plex", "Better Call Saul", 2015, 1, 3),
        ("episode", 0, 1, "plex", "After Life", 2019, 1, 1),
        ("movie", 0, 1, "plex", "The Boys", 2019, None, None),
        ("movie", 0, 0, "plex", "1917", 2019, None, None),
        # Year embedded IN the title string (not just the separate `year`
        # column) — this is how WatchState actually stores shows/movies Plex
        # disambiguates by year ("Bluey (2018)", "Doctor Who (2005)"). The
        # section_index side strips the year via watchstate_key, so the WS side
        # must too or these rows never join. Regression fixtures for the
        # year-suffix join fix in _ws_all_shows / _ws_all_movies /
        # coverage_counts and the per-title reads.
        ("episode", 0, 1, "plex", "Bluey (2018)", 2018, 1, 1),
        ("episode", 0, 0, "plex", "Bluey (2018)", 2018, 1, 2),
        ("movie", 0, 1, "plex", "Dune (2021)", 2021, None, None),
    ]
    conn.executemany(
        "INSERT INTO state (type, updated, watched, via, title, year, season, episode)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def patch_watchstate(
    monkeypatch: pytest.MonkeyPatch, watchstate_db: Path
) -> Generator[Path]:
    """Point synclet.watchstate at the fixture DB and clear its caches."""
    from synclet import watchstate as ws

    monkeypatch.setattr("synclet.config.WATCHSTATE_DB", watchstate_db)
    monkeypatch.setattr(ws, "WATCHSTATE_DB", watchstate_db)
    ws.invalidate_cache()
    yield watchstate_db
    ws.invalidate_cache()


# ── Filesystem fixtures ───────────────────────────────────────────────────


def _make_video(p: Path, size: int = 1024) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\0" * size)


@pytest.fixture
def media_tree(tmp_path: Path) -> dict[str, Path]:
    """Synthesize a tiny media/sync tree.

    tv/Better Call Saul (2015) {tvdb-1}/Season 01/S01E01.mkv (1KB)
    tv/Better Call Saul (2015) {tvdb-1}/Season 01/S01E02.mkv (1KB)
    tv/After Life (2019) {tvdb-2}/Season 01/S01E01.mkv (1KB)
    movies/1917 (2019) {tmdb-3}/1917.mkv (1KB)
    movies/1917 (2019) {tmdb-3}/1917.en.srt (1KB)
    movies/1917 (2019) {tmdb-3}/1917.fr.srt (1KB)  # non-English → wanted-file filter drops it

    Plus an existing synced copy of After Life S01E01 to verify is_synced detection.
    """
    media = tmp_path / "media"
    sync = tmp_path / "synced-media"

    bcs = media / "tv" / "Better Call Saul (2015) {tvdb-1}" / "Season 01"
    _make_video(bcs / "Better Call Saul - S01E01 - Uno [WEBDL-1080p].mkv")
    _make_video(bcs / "Better Call Saul - S01E02 - Mijo [WEBDL-1080p].mkv")
    (bcs / "Better Call Saul - S01E01 - Uno.en.srt").write_bytes(b"en")
    (bcs / "Better Call Saul - S01E01 - Uno.fr.srt").write_bytes(b"fr")

    al = media / "tv" / "After Life (2019) {tvdb-2}" / "Season 01"
    _make_video(al / "After Life - S01E01 - Episode 1.mkv")

    m1 = media / "movies" / "1917 (2019) {tmdb-3}"
    _make_video(m1 / "1917.mkv")
    (m1 / "1917.en.srt").write_bytes(b"en")
    (m1 / "1917.fr.srt").write_bytes(b"fr")

    # Pre-synced copy of After Life S01E01 → should show is_synced=True
    al_synced = sync / "tv" / "After Life (2019) {tvdb-2}" / "Season 01"
    _make_video(al_synced / "After Life - S01E01 - Episode 1.mkv")

    return {
        "tmp": tmp_path,
        "media": media,
        "sync": sync,
    }


def _point_paths(monkeypatch: pytest.MonkeyPatch, media: Path, sync: Path) -> None:
    """Point every module's MEDIA_ROOT / SYNC_ROOT binding at `media` / `sync`.

    Each module imports the path once at module-load, so config-level patching
    alone is not enough. This is the single source of truth for "all the places
    the path leaks into" , if a new module binds one, add it here.
    """
    monkeypatch.setattr("synclet.config.MEDIA_ROOT", media)
    monkeypatch.setattr("synclet.config.SYNC_ROOT", sync)
    monkeypatch.setattr("synclet.scan.MEDIA_ROOT", media)
    monkeypatch.setattr("synclet.scan.SYNC_ROOT", sync)
    monkeypatch.setattr("synclet.sync_ops.MEDIA_ROOT", media)
    monkeypatch.setattr("synclet.sync_ops.SYNC_ROOT", sync)
    monkeypatch.setattr("synclet.state.SYNC_ROOT", sync)
    monkeypatch.setattr("synclet.fs_helpers.SYNC_ROOT", sync)
    monkeypatch.setattr("synclet.pending.SYNC_ROOT", sync)


@pytest.fixture(autouse=True)
def _no_real_share(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point every test at an empty share and private app state.

    patch_paths repoints the share onto its fixture tree; the state files stay
    here, in the test's own tmp_path. The production defaults (/data/media, /data/others/synced-media) exist on
    the dev box, which is the Unraid server itself. A test that ran the real
    startup or refresh loop without patch_paths listed the live library roots
    (merged shfs directories that stall Plex while listed, Synclet#66) from a
    daemon thread, and that listing landed in a later test. patch_paths
    repoints these again onto its fixture tree.
    """
    _point_paths(monkeypatch, tmp_path / "no-media", tmp_path / "no-sync")
    # App state under /app/data and the WatchState DB under /appdata: every
    # module binding of each, so no test reads or writes the live copies.
    # patch_watchstate points the WatchState DB at its fixture DB instead.
    for target, path in (
        ("synclet.config.THUMB_CACHE", ".thumb-cache"),
        ("synclet.plex.THUMB_CACHE", ".thumb-cache"),
        ("synclet.plex.PLEX_CACHE_FILE", ".plex-section-cache.json"),
        ("synclet.config.SNAPSHOT_FILE", "snapshot.json"),
        ("synclet.pending.SNAPSHOT_FILE", "snapshot.json"),
        ("synclet.config.IGNORED_FILE", "ignored.json"),
        ("synclet.ignored.IGNORED_FILE", "ignored.json"),
        ("synclet.config.FOLLOWED_FILE", "followed.json"),
        ("synclet.config.LIBRARY_LISTING_FILE", ".library-listing.json"),
        ("synclet.config.WATCHSTATE_DB", "no-watchstate.db"),
        ("synclet.watchstate.WATCHSTATE_DB", "no-watchstate.db"),
    ):
        monkeypatch.setattr(target, tmp_path / path)


@pytest.fixture
def patch_paths(
    monkeypatch: pytest.MonkeyPatch, media_tree: dict[str, Path]
) -> Generator[dict[str, Path]]:
    """Repoint every module's MEDIA_ROOT / SYNC_ROOT binding at the fixture tree.

    See _point_paths for the bindings; this adds the fixture tree and the
    per-module state files on top.
    """
    media = media_tree["media"]
    sync = media_tree["sync"]
    _point_paths(monkeypatch, media, sync)
    from synclet import library, state, synced

    library.reset()
    # reset() drops listeners too; restore the production wiring.
    state.register_builders()
    synced.register_builders()

    # The in-process cache holds previous-test data keyed off the prior
    # tmp_path. invalidate() now only flags dirty (it preserves last-good for
    # background rebuild), so it cannot isolate tests; clear() fully resets the
    # engine. Also bust the watchstate lru caches behind the grid.
    from synclet import maint_cache, refresh, sync_ops, watchstate

    maint_cache.clear()
    watchstate.invalidate_cache()
    # Job registry and refresh backoff are process-wide; start each test empty.
    sync_ops._JOBS.clear()
    sync_ops._FOLDER_LOCKS.clear()
    sync_ops._FOLDER_USERS.clear()
    refresh._failed_at.clear()
    yield media_tree
    maint_cache.clear()
    watchstate.invalidate_cache()
    library.reset()


@pytest.fixture
def client(patch_paths, patch_watchstate, tmp_path, monkeypatch):
    """A TestClient pointed at the real Litestar app, with paths repointed
    at the fixture tree and Plex calls mocked away."""
    # Stub Plex API so tests don't depend on network
    monkeypatch.setattr("synclet.plex._get_xml", lambda *a, **kw: None)
    # Reset Plex section_index cache (lru_cache)
    from synclet.plex import episode_rating_keys, section_index

    section_index.cache_clear()
    episode_rating_keys.cache_clear()

    from main import app

    with TestClient(app=app) as c:
        yield c
