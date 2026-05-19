"""Tests for deletion-driven watch-state write-back (synclet.pending).

The flow is: snapshot.json tracks what Synclet thinks is on disk; pending =
snapshot - on-disk. Resolve (confirm or reject) drops from the snapshot,
confirm additionally scrobbles. The bootstrap path means first-ever run
produces zero pending so existing libraries are not flagged as deletions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from synclet import pending as pending_mod
from synclet.pending import (
    SnapshotKey,
    add_keys,
    bootstrap_if_missing,
    compute_pending,
    keys_for_paths,
    load_snapshot,
    path_to_snapshot_key,
    remove_keys,
    resolve,
    save_snapshot,
)


@pytest.fixture
def patch_snapshot(monkeypatch, tmp_path: Path) -> Path:
    """Point SNAPSHOT_FILE at a temp dir.

    Re-binds every module that imported the constant. Without this the test
    would write to /app/data/snapshot.json, which (a) may not exist on
    pocket-dev and (b) would leak state between tests.
    """
    snap = tmp_path / "snapshot.json"
    monkeypatch.setattr("synclet.config.SNAPSHOT_FILE", snap)
    monkeypatch.setattr("synclet.pending.SNAPSHOT_FILE", snap)
    return snap


# ── path_to_snapshot_key ────────────────────────────────────────────────────


class TestPathToSnapshotKey:
    def test_show_episode(self, patch_paths):
        path = (
            patch_paths["sync"]
            / "tv"
            / "Better Call Saul (2015) {tvdb-1}"
            / "Season 01"
            / "Better Call Saul - S01E03 - Whatever.mkv"
        )
        key = path_to_snapshot_key(path)
        assert key == SnapshotKey(
            sync_sub="tv",
            folder="Better Call Saul (2015) {tvdb-1}",
            season=1,
            episode=3,
        )

    def test_movie(self, patch_paths):
        path = (
            patch_paths["sync"] / "movies" / "1917 (2019) {tmdb-3}" / "1917.mkv"
        )
        key = path_to_snapshot_key(path)
        assert key == SnapshotKey(sync_sub="movies", folder="1917 (2019) {tmdb-3}")

    def test_non_video_returns_none(self, patch_paths):
        path = (
            patch_paths["sync"]
            / "tv"
            / "Foo"
            / "Season 01"
            / "Foo - S01E01.srt"
        )
        assert path_to_snapshot_key(path) is None

    def test_path_outside_sync_root_returns_none(self, patch_paths, tmp_path: Path):
        elsewhere = tmp_path / "not-sync" / "thing.mkv"
        assert path_to_snapshot_key(elsewhere) is None

    def test_unknown_sync_sub_returns_none(self, patch_paths):
        path = patch_paths["sync"] / "totally-unknown-sub" / "X" / "X.mkv"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
        assert path_to_snapshot_key(path) is None


# ── load/save snapshot ──────────────────────────────────────────────────────


class TestSnapshotIO:
    def test_load_empty_when_missing(self, patch_snapshot):
        assert load_snapshot() == set()

    def test_roundtrip(self, patch_snapshot):
        keys = {
            SnapshotKey(sync_sub="tv", folder="Foo", season=1, episode=1),
            SnapshotKey(sync_sub="movies", folder="Bar"),
        }
        save_snapshot(keys)
        assert load_snapshot() == keys

    def test_load_returns_empty_on_corrupt_json(self, patch_snapshot):
        patch_snapshot.parent.mkdir(parents=True, exist_ok=True)
        patch_snapshot.write_text("{not valid json")
        assert load_snapshot() == set()

    def test_save_is_atomic(self, patch_snapshot):
        """The tempfile-then-rename pattern leaves no .tmp behind on success."""
        save_snapshot({SnapshotKey(sync_sub="tv", folder="X", season=1, episode=1)})
        siblings = list(patch_snapshot.parent.iterdir())
        assert not any(s.name.endswith(".tmp") for s in siblings)


# ── bootstrap ───────────────────────────────────────────────────────────────


class TestBootstrap:
    def test_first_run_creates_snapshot_matching_disk(
        self, patch_snapshot, patch_paths
    ):
        # The patch_paths fixture pre-seeded an After Life synced copy.
        assert not patch_snapshot.exists()
        fired = bootstrap_if_missing()
        assert fired is True
        snap = load_snapshot()
        assert snap == {
            SnapshotKey(
                sync_sub="tv",
                folder="After Life (2019) {tvdb-2}",
                season=1,
                episode=1,
            ),
        }

    def test_second_call_is_noop(self, patch_snapshot, patch_paths):
        bootstrap_if_missing()
        # Mutate the snapshot directly and re-bootstrap; it must NOT overwrite.
        save_snapshot({SnapshotKey(sync_sub="movies", folder="Foo")})
        assert bootstrap_if_missing() is False
        assert load_snapshot() == {SnapshotKey(sync_sub="movies", folder="Foo")}


# ── pending diff ────────────────────────────────────────────────────────────


class TestComputePending:
    def test_no_pending_immediately_after_bootstrap(
        self, patch_snapshot, patch_paths
    ):
        bootstrap_if_missing()
        assert compute_pending() == set()

    def test_deletion_surfaces_as_pending(self, patch_snapshot, patch_paths):
        # Seed snapshot with an item that doesn't exist on disk.
        save_snapshot(
            {SnapshotKey(sync_sub="tv", folder="Ghost Show", season=1, episode=1)}
        )
        pending_set = compute_pending()
        assert pending_set == {
            SnapshotKey(sync_sub="tv", folder="Ghost Show", season=1, episode=1)
        }

    def test_restored_file_disappears_from_pending(self, patch_snapshot, patch_paths):
        # Pre-existing After Life file is in snapshot AND on disk -> no pending.
        save_snapshot(
            {
                SnapshotKey(
                    sync_sub="tv",
                    folder="After Life (2019) {tvdb-2}",
                    season=1,
                    episode=1,
                ),
                SnapshotKey(
                    sync_sub="tv",
                    folder="Ghost Show",
                    season=1,
                    episode=1,
                ),
            }
        )
        # Only the ghost should be pending.
        assert compute_pending() == {
            SnapshotKey(sync_sub="tv", folder="Ghost Show", season=1, episode=1)
        }


# ── keys_for_paths (used by sync_ops.remove_files) ──────────────────────────


class TestKeysForPaths:
    def test_drops_non_video(self, patch_paths):
        sync = patch_paths["sync"]
        srt = sync / "tv" / "X" / "Season 01" / "X - S01E01.srt"
        mkv = sync / "tv" / "X" / "Season 01" / "X - S01E01.mkv"
        keys = keys_for_paths([str(srt), str(mkv)])
        assert keys == {
            SnapshotKey(sync_sub="tv", folder="X", season=1, episode=1)
        }

    def test_drops_paths_outside_sync_root(self, patch_paths, tmp_path: Path):
        outside = tmp_path / "elsewhere" / "S01E01.mkv"
        assert keys_for_paths([str(outside)]) == set()


# ── add_keys / remove_keys ──────────────────────────────────────────────────


class TestSnapshotMutation:
    def test_add_keys_bootstraps_if_needed(self, patch_snapshot, patch_paths):
        # Pre-existing After Life on disk; add_keys should bootstrap first.
        assert not patch_snapshot.exists()
        new = SnapshotKey(sync_sub="tv", folder="New Show", season=1, episode=1)
        add_keys({new})
        snap = load_snapshot()
        # After Life from bootstrap PLUS the new key.
        assert new in snap
        assert any(k.folder == "After Life (2019) {tvdb-2}" for k in snap)

    def test_remove_keys_idempotent_when_absent(self, patch_snapshot):
        save_snapshot({SnapshotKey(sync_sub="tv", folder="A", season=1, episode=1)})
        remove_keys(
            {SnapshotKey(sync_sub="tv", folder="Not There", season=1, episode=1)}
        )
        assert load_snapshot() == {
            SnapshotKey(sync_sub="tv", folder="A", season=1, episode=1)
        }

    def test_empty_inputs_are_noops(self, patch_snapshot):
        # Empty add_keys must not touch the file (no spurious bootstrap or write).
        add_keys([])
        assert not patch_snapshot.exists()


# ── resolve ─────────────────────────────────────────────────────────────────


class TestResolveReject:
    def test_reject_drops_from_snapshot_no_scrobble(
        self, patch_snapshot, patch_paths, monkeypatch
    ):
        # If scrobble were called we'd see it; the spy raises.
        def _boom(*_a, **_kw):
            msg = "scrobble must not be called on reject"
            raise AssertionError(msg)

        monkeypatch.setattr("synclet.plex.scrobble", _boom)

        k = SnapshotKey(sync_sub="tv", folder="X", season=1, episode=1)
        save_snapshot({k})
        results = resolve([k], confirm=False)
        assert [r.status for r in results] == ["rejected"]
        assert load_snapshot() == set()


class TestResolveConfirm:
    def test_confirm_with_mocked_scrobble(
        self, patch_snapshot, patch_paths, monkeypatch
    ):
        called: list[str] = []

        def _ok(rating_key: str, **_kw) -> bool:
            called.append(rating_key)
            return True

        # Stub the Plex lookups; movie resolves directly off section_index.
        monkeypatch.setattr("synclet.plex.scrobble", _ok)
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"ratingKey": "9000"} if folder == "Movie X" else None,
        )

        # Seed: a movie pending. find_source_lib resolves via MEDIA_ROOT,
        # which we'll satisfy by creating the source folder.
        (patch_paths["media"] / "movies" / "Movie X").mkdir(parents=True)
        k = SnapshotKey(sync_sub="movies", folder="Movie X")
        save_snapshot({k})

        results = resolve([k], confirm=True)
        assert [r.status for r in results] == ["ok"]
        assert called == ["9000"]
        assert load_snapshot() == set()

    def test_scrobble_failure_still_drops_from_snapshot(
        self, patch_snapshot, patch_paths, monkeypatch
    ):
        # Plex unreachable -> scrobble returns False. We still drop from
        # snapshot, since the file is already gone from disk.
        monkeypatch.setattr("synclet.plex.scrobble", lambda *a, **kw: False)
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"ratingKey": "9000"} if folder == "Movie X" else None,
        )

        (patch_paths["media"] / "movies" / "Movie X").mkdir(parents=True)
        k = SnapshotKey(sync_sub="movies", folder="Movie X")
        save_snapshot({k})

        results = resolve([k], confirm=True)
        assert [r.status for r in results] == ["scrobble_failed"]
        assert load_snapshot() == set()

    def test_confirm_for_show_episode_uses_episode_rating_key(
        self, patch_snapshot, patch_paths, monkeypatch
    ):
        """Show episode confirm: show ratingKey -> allLeaves -> episode key -> scrobble.

        This path is structurally different from movie confirm (which scrobbles
        the show-level ratingKey directly): show confirm has to drill into
        episode_rating_keys to find the EPISODE's ratingKey.
        """
        called: list[str] = []

        def _ok(rating_key: str, **_kw) -> bool:
            called.append(rating_key)
            return True

        monkeypatch.setattr("synclet.plex.scrobble", _ok)
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: (
                {"ratingKey": "SHOW100"} if folder == "Ghost Show" else None
            ),
        )
        monkeypatch.setattr(
            "synclet.plex.episode_rating_keys",
            lambda show_rk: {(1, 3): "EP-1-3"} if show_rk == "SHOW100" else {},
        )

        (patch_paths["media"] / "tv" / "Ghost Show").mkdir(parents=True)
        k = SnapshotKey(sync_sub="tv", folder="Ghost Show", season=1, episode=3)
        save_snapshot({k})

        results = resolve([k], confirm=True)
        assert [r.status for r in results] == ["ok"]
        # Must be the EPISODE ratingKey, not the show's. This is the trap the
        # initial design comment got wrong before live-testing surfaced it.
        assert called == ["EP-1-3"]
        assert load_snapshot() == set()

    def test_no_rating_key_when_plex_returns_nothing(
        self, patch_snapshot, patch_paths, monkeypatch
    ):
        monkeypatch.setattr(
            "synclet.plex.scrobble",
            lambda *a, **kw: pytest.fail("must not be called without rating key"),
        )
        monkeypatch.setattr("synclet.plex.find_in_library", lambda *a, **kw: None)

        (patch_paths["media"] / "movies" / "Movie X").mkdir(parents=True)
        k = SnapshotKey(sync_sub="movies", folder="Movie X")
        save_snapshot({k})

        results = resolve([k], confirm=True)
        assert [r.status for r in results] == ["no_rating_key"]
        # Snapshot still cleared — the file is gone, leaving it in pending
        # would re-prompt the user every page load.
        assert load_snapshot() == set()


# ── grouped_pending wire shape ──────────────────────────────────────────────


class TestGroupedPending:
    def test_movie_entry_shape(self, patch_snapshot, patch_paths, monkeypatch):
        monkeypatch.setattr("synclet.plex.find_in_library", lambda *a, **kw: None)
        monkeypatch.setattr(
            "synclet.plex.episode_rating_keys", lambda show_rk: {}
        )
        (patch_paths["media"] / "movies" / "Ghost Movie").mkdir(parents=True)
        save_snapshot({SnapshotKey(sync_sub="movies", folder="Ghost Movie")})

        groups = pending_mod.grouped_pending()
        assert len(groups) == 1
        g = groups[0]
        assert g["sync_sub"] == "movies"
        assert g["folder"] == "Ghost Movie"
        assert g["kind"] == "movie"
        assert g["title"] == "Ghost Movie"
        assert "rating_key" in g
        assert "already_watched_in_plex" in g

    def test_show_entry_groups_by_season(self, patch_snapshot, patch_paths, monkeypatch):
        monkeypatch.setattr("synclet.plex.find_in_library", lambda *a, **kw: None)
        monkeypatch.setattr("synclet.plex.episode_rating_keys", lambda show_rk: {})
        (patch_paths["media"] / "tv" / "Ghost Show").mkdir(parents=True)
        save_snapshot(
            {
                SnapshotKey(sync_sub="tv", folder="Ghost Show", season=1, episode=1),
                SnapshotKey(sync_sub="tv", folder="Ghost Show", season=1, episode=2),
                SnapshotKey(sync_sub="tv", folder="Ghost Show", season=2, episode=1),
            }
        )

        groups = pending_mod.grouped_pending()
        assert len(groups) == 1
        seasons = groups[0]["seasons"]
        s_nums = [s["season"] for s in seasons]
        assert s_nums == [1, 2]
        assert len(seasons[0]["episodes"]) == 2
        assert len(seasons[1]["episodes"]) == 1
