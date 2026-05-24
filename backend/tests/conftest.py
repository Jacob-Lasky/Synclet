"""Shared pytest fixtures.

Most tests use a synthetic media tree under tmp_path so they don't depend on
the real `/data/media` mount. The `media_env` fixture wires every module that
holds a path binding (config + per-module re-imports) so monkeypatching is
not silently incomplete.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

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


@pytest.fixture
def patch_paths(
    monkeypatch: pytest.MonkeyPatch, media_tree: dict[str, Path]
) -> Generator[dict[str, Path]]:
    """Repoint every module's MEDIA_ROOT / SYNC_ROOT binding at the fixture tree.

    Each module imports the path once at module-load, so config-level patching
    alone is not enough. This fixture is the single source of truth for "all
    the places the path leaks into" , if a new module is added, add it here.
    """
    media = media_tree["media"]
    sync = media_tree["sync"]

    monkeypatch.setattr("synclet.config.MEDIA_ROOT", media)
    monkeypatch.setattr("synclet.config.SYNC_ROOT", sync)
    monkeypatch.setattr("synclet.scan.MEDIA_ROOT", media)
    monkeypatch.setattr("synclet.scan.SYNC_ROOT", sync)
    monkeypatch.setattr("synclet.sync_ops.MEDIA_ROOT", media)
    monkeypatch.setattr("synclet.sync_ops.SYNC_ROOT", sync)
    monkeypatch.setattr("synclet.state.SYNC_ROOT", sync)
    monkeypatch.setattr("synclet.fs_helpers.SYNC_ROOT", sync)
    monkeypatch.setattr("synclet.pending.SYNC_ROOT", sync)
    # plex.py imports THUMB_CACHE at module-load, so we have to patch BOTH
    # the config binding and the per-module re-import. Without the plex.py
    # patch, fetch_thumb_bytes mkdirs the production-default path, which
    # raises and surfaces as a 500 on the route.
    thumb_cache = media_tree["tmp"] / ".thumb-cache"
    monkeypatch.setattr("synclet.config.THUMB_CACHE", thumb_cache)
    monkeypatch.setattr("synclet.plex.THUMB_CACHE", thumb_cache)
    # Disk cache for section_index. Default lives under /app/data inside the
    # container; point at tmp so the test suite never reads or writes the
    # production-default path. Existing tests stub _get_xml, so the write
    # branch wasn't hit before — this patch is defensive for future tests
    # that exercise the full section_index path.
    monkeypatch.setattr(
        "synclet.plex.PLEX_CACHE_FILE",
        media_tree["tmp"] / ".plex-section-cache.json",
    )
    # Snapshot file for the pending module. Default lives under /app/data
    # which only exists inside the backend container; point at tmp so sync_ops
    # tests that mutate the snapshot don't try to write into that path.
    snapshot = media_tree["tmp"] / "snapshot.json"
    monkeypatch.setattr("synclet.config.SNAPSHOT_FILE", snapshot)
    monkeypatch.setattr("synclet.pending.SNAPSHOT_FILE", snapshot)
    # Same treatment for the ignored-entries store (see synclet.ignored).
    ignored_file = media_tree["tmp"] / "ignored.json"
    monkeypatch.setattr("synclet.config.IGNORED_FILE", ignored_file)
    monkeypatch.setattr("synclet.ignored.IGNORED_FILE", ignored_file)

    # State cache holds previous-test data; invalidate every test.
    from synclet import maint_cache
    from synclet import state as state_mod

    state_mod.invalidate()
    maint_cache.invalidate()
    yield media_tree
    state_mod.invalidate()
    maint_cache.invalidate()
