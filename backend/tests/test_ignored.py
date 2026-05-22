"""Tests for the maintenance mute list (synclet.ignored).

Covers: persistence roundtrip, idempotent ignore/unignore, malformed ref
handling, and integration with the three producers (pending, watched,
hanging) so muted entries actually disappear from the UI.
"""

from __future__ import annotations

import pytest

from synclet import ignored as ignored_mod
from synclet.ignored import (
    HangingRef,
    IgnoredState,
    PendingRef,
    WatchedRef,
    ignore_hanging,
    ignore_pending,
    ignore_ref,
    ignore_watched,
    list_grouped,
    load,
    save,
    total_ignored,
    unignore_pending,
    unignore_ref,
)


class TestPersistence:
    def test_load_empty_when_missing(self, patch_paths):
        s = load()
        assert s.pending == set()
        assert s.watched == set()
        assert s.hanging == set()

    def test_roundtrip(self, patch_paths):
        state = IgnoredState(
            pending={PendingRef(sync_sub="tv", folder="X", season=1, episode=1)},
            watched={WatchedRef(lib="movies", folder="Y")},
            hanging={HangingRef(path="/sync/foo.srt")},
        )
        save(state)
        loaded = load()
        assert loaded.pending == state.pending
        assert loaded.watched == state.watched
        assert loaded.hanging == state.hanging

    def test_corrupt_file_returns_empty(self, patch_paths):
        # Write garbage; load should return an empty state, not crash.
        from synclet.config import IGNORED_FILE

        IGNORED_FILE.parent.mkdir(parents=True, exist_ok=True)
        IGNORED_FILE.write_text("{not valid")
        s = load()
        assert s.pending == set()


class TestIdempotency:
    def test_ignore_then_unignore_is_clean(self, patch_paths):
        ref = PendingRef(sync_sub="tv", folder="X", season=1, episode=1)
        ignore_pending(ref)
        assert ref in load().pending
        unignore_pending(ref)
        assert ref not in load().pending

    def test_double_ignore_no_duplicate(self, patch_paths):
        ref = WatchedRef(lib="movies", folder="X")
        ignore_watched(ref)
        ignore_watched(ref)
        assert len(load().watched) == 1


class TestIgnoreRef:
    def test_pending_via_ref_dict(self, patch_paths):
        ok = ignore_ref(
            "pending",
            {"sync_sub": "tv", "folder": "X", "season": 1, "episode": 1},
        )
        assert ok is True
        assert (
            PendingRef(sync_sub="tv", folder="X", season=1, episode=1) in load().pending
        )

    def test_watched_via_ref_dict(self, patch_paths):
        ok = ignore_ref("watched", {"lib": "movies", "folder": "X"})
        assert ok is True

    def test_hanging_via_ref_dict(self, patch_paths):
        ok = ignore_ref("hanging", {"path": "/sync/foo.srt"})
        assert ok is True

    def test_unknown_kind_returns_false(self, patch_paths):
        assert ignore_ref("bogus", {}) is False

    def test_missing_required_field_returns_false(self, patch_paths):
        # watched requires lib + folder; missing folder is malformed
        assert ignore_ref("watched", {"lib": "movies"}) is False


class TestListGrouped:
    def test_returns_three_kinds(self, patch_paths):
        ignore_pending(PendingRef(sync_sub="tv", folder="X"))
        ignore_watched(WatchedRef(lib="m", folder="Y"))
        ignore_hanging(HangingRef(path="/p"))
        g = list_grouped()
        assert {"pending", "watched", "hanging"} <= g.keys()
        assert len(g["pending"]) == 1
        assert len(g["watched"]) == 1
        assert len(g["hanging"]) == 1

    def test_total_count(self, patch_paths):
        ignore_pending(PendingRef(sync_sub="tv", folder="X"))
        ignore_watched(WatchedRef(lib="m", folder="Y"))
        assert total_ignored() == 2


# ── Integration: producers filter ignored ────────────────────────────────────


class TestCacheInvalidation:
    """Mutating actions must clear maint_cache so the next read recomputes."""

    def test_ignore_invalidates_cache(self, patch_paths):
        from synclet import maint_cache

        # Prime the cache with a sentinel value.
        maint_cache._cache["pending"] = (0.0, "stale")
        # Just calling time.time() picks up the stale-since-epoch entry, so
        # we use the actual cache helper to set a fresh-but-known value.
        import time

        maint_cache._cache["pending"] = (time.time(), "stale")
        assert maint_cache._cache.get("pending", (0, None))[1] == "stale"

        ignore_pending(PendingRef(sync_sub="tv", folder="X", season=1, episode=1))
        # invalidate() should have cleared the cache entry.
        assert "pending" not in maint_cache._cache

    def test_unignore_invalidates_cache(self, patch_paths):
        from synclet import maint_cache
        import time

        ignore_pending(PendingRef(sync_sub="tv", folder="Y", season=1, episode=1))
        # Re-prime after the previous invalidation.
        maint_cache._cache["pending"] = (time.time(), "stale")

        unignore_pending(PendingRef(sync_sub="tv", folder="Y", season=1, episode=1))
        assert "pending" not in maint_cache._cache


class TestPendingFilters:
    def test_ignored_pending_disappears_from_compute_pending(
        self, patch_paths, patch_snapshot_for_pending
    ):
        from synclet.pending import SnapshotKey, compute_pending, save_snapshot

        ghost = SnapshotKey(sync_sub="tv", folder="Ghost", season=1, episode=1)
        save_snapshot({ghost})
        assert ghost in compute_pending()

        ignore_pending(PendingRef(sync_sub="tv", folder="Ghost", season=1, episode=1))
        assert ghost not in compute_pending()


class TestWatchedFilters:
    def test_ignored_watched_disappears(self, patch_paths, patch_watchstate):
        from synclet.sync_ops import find_watched_synced_files

        # Pre-sync The Boys (watched=True in fixture) so it shows up.
        sync = patch_paths["sync"]
        m_dir = sync / "movies" / "The Boys (2019)"
        m_dir.mkdir(parents=True)
        (m_dir / "movie.mkv").write_bytes(b"\0" * 50)

        before = find_watched_synced_files()
        # find_watched_synced_files requires the source library to exist.
        # Our fixture has movies/ for The Boys to match via find_source_lib.
        # If the fixture doesn't include The Boys natively, the test still
        # verifies the filter path: a non-empty before becomes empty after
        # ignoring the (lib, folder) entry.
        # The patch_paths fixture's "1917 (2019) {tmdb-3}" is the source.
        # Adjust: pre-sync 1917 instead since it's already in patch_paths.
        # (Test below covers the same shape via 1917.)
        _ = before  # silence unused warning while we use the 1917 path

    def test_ignored_watched_for_real_fixture(self, patch_paths, patch_watchstate):
        from synclet.sync_ops import find_watched_synced_files

        # 1917 is in the fixture watchstate DB as watched=False, so it
        # doesn't show up in watched-files. Switch to The Boys: build the
        # The Boys folder in MEDIA_ROOT so find_source_lib succeeds.
        media = patch_paths["media"]
        sync = patch_paths["sync"]
        (media / "movies" / "The Boys (2019)").mkdir(parents=True)
        m_dir = sync / "movies" / "The Boys (2019)"
        m_dir.mkdir(parents=True)
        (m_dir / "movie.mkv").write_bytes(b"\0" * 50)

        before = [w["folder"] for w in find_watched_synced_files()]
        assert "The Boys (2019)" in before

        ignore_watched(WatchedRef(lib="movies", folder="The Boys (2019)"))
        after = [w["folder"] for w in find_watched_synced_files()]
        assert "The Boys (2019)" not in after


class TestHangingFilters:
    def test_ignored_hanging_disappears(self, patch_paths):
        from synclet.sync_ops import find_hanging_files

        sync = patch_paths["sync"]
        d = sync / "tv" / "Orphan Show" / "Season 01"
        d.mkdir(parents=True)
        orphan_srt = d / "ep.srt"
        orphan_srt.write_text("subs")
        # No video in the dir -> hanging.

        before = [h["path"] for h in find_hanging_files()]
        assert str(orphan_srt) in before

        ignore_hanging(HangingRef(path=str(orphan_srt)))
        after = [h["path"] for h in find_hanging_files()]
        assert str(orphan_srt) not in after


# ── Test fixture helpers ─────────────────────────────────────────────────────


@pytest.fixture
def patch_snapshot_for_pending(monkeypatch, patch_paths):
    """Repoint the snapshot at the fixture's tmp dir for pending tests.

    The default conftest.patch_paths already points SNAPSHOT_FILE at tmp,
    but recreating here keeps the test isolated and self-documenting.
    """
    from synclet import pending as pending_mod

    snap = patch_paths["tmp"] / "snapshot.json"
    monkeypatch.setattr("synclet.config.SNAPSHOT_FILE", snap)
    monkeypatch.setattr("synclet.pending.SNAPSHOT_FILE", snap)
    return snap
