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
    cleanup_after_resolve,
    compute_pending,
    keys_for_paths,
    load_snapshot,
    mark_watched_scope,
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
        path = patch_paths["sync"] / "movies" / "1917 (2019) {tmdb-3}" / "1917.mkv"
        key = path_to_snapshot_key(path)
        assert key == SnapshotKey(sync_sub="movies", folder="1917 (2019) {tmdb-3}")

    def test_non_video_returns_none(self, patch_paths):
        path = patch_paths["sync"] / "tv" / "Foo" / "Season 01" / "Foo - S01E01.srt"
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
    def test_no_pending_immediately_after_bootstrap(self, patch_snapshot, patch_paths):
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
        assert keys == {SnapshotKey(sync_sub="tv", folder="X", season=1, episode=1)}

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
        results, _cleanup = resolve([k], confirm=False)
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

        results, _cleanup = resolve([k], confirm=True)
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

        results, _cleanup = resolve([k], confirm=True)
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

        results, _cleanup = resolve([k], confirm=True)
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

        results, _cleanup = resolve([k], confirm=True)
        assert [r.status for r in results] == ["no_rating_key"]
        # Snapshot still cleared — the file is gone, leaving it in pending
        # would re-prompt the user every page load.
        assert load_snapshot() == set()


# ── grouped_pending wire shape ──────────────────────────────────────────────


class TestGroupedPending:
    def test_movie_entry_shape(self, patch_snapshot, patch_paths, monkeypatch):
        monkeypatch.setattr("synclet.plex.find_in_library", lambda *a, **kw: None)
        monkeypatch.setattr("synclet.plex.episode_rating_keys", lambda show_rk: {})
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

    def test_show_entry_groups_by_season(
        self, patch_snapshot, patch_paths, monkeypatch
    ):
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


# ── cleanup_after_resolve ────────────────────────────────────────────────────


class TestCleanupAfterResolveMovie:
    def test_sweeps_sidecars_and_folder_when_video_is_gone(
        self, patch_snapshot, patch_paths
    ):
        sync = patch_paths["sync"]
        folder = sync / "movies" / "Ghost Movie (2099)"
        folder.mkdir(parents=True)
        # Sidecars only. No video — the user deleted that already.
        (folder / "Ghost Movie.en.srt").write_text("subs")
        (folder / "movie.nfo").write_text("nfo")
        (folder / "poster.jpg").write_bytes(b"\xff")

        counts = cleanup_after_resolve(
            SnapshotKey(sync_sub="movies", folder="Ghost Movie (2099)")
        )
        assert counts == {"removed_files": 3, "removed_dirs": 1}
        assert not folder.exists()

    def test_no_op_when_video_still_present(self, patch_snapshot, patch_paths):
        """Safety latch: don't strip sidecars from a movie still on disk."""
        sync = patch_paths["sync"]
        folder = sync / "movies" / "Ghost Movie (2099)"
        folder.mkdir(parents=True)
        (folder / "Ghost Movie.mkv").write_bytes(b"\0" * 10)
        (folder / "Ghost Movie.en.srt").write_text("subs")

        counts = cleanup_after_resolve(
            SnapshotKey(sync_sub="movies", folder="Ghost Movie (2099)")
        )
        assert counts == {"removed_files": 0, "removed_dirs": 0}
        assert (folder / "Ghost Movie.mkv").exists()
        assert (folder / "Ghost Movie.en.srt").exists()

    def test_no_op_when_folder_missing(self, patch_snapshot, patch_paths):
        counts = cleanup_after_resolve(
            SnapshotKey(sync_sub="movies", folder="Nonexistent")
        )
        assert counts == {"removed_files": 0, "removed_dirs": 0}


class TestCleanupAfterResolveShow:
    def test_sweeps_episode_sidecars_when_video_gone(self, patch_snapshot, patch_paths):
        sync = patch_paths["sync"]
        season = sync / "tv" / "Ghost Show (2099)" / "Season 01"
        season.mkdir(parents=True)
        # Only the S01E01 sidecar remains; user deleted the .mkv.
        (season / "Ghost Show - S01E01 - X.en.srt").write_text("subs")
        # S01E02 video is still here — must NOT touch it.
        (season / "Ghost Show - S01E02 - Y.mkv").write_bytes(b"\0" * 5)
        (season / "Ghost Show - S01E02 - Y.en.srt").write_text("subs2")

        counts = cleanup_after_resolve(
            SnapshotKey(
                sync_sub="tv",
                folder="Ghost Show (2099)",
                season=1,
                episode=1,
            )
        )
        assert counts["removed_files"] == 1
        # season dir is NOT empty (S01E02 still there), so no dir prune
        assert counts["removed_dirs"] == 0
        assert not (season / "Ghost Show - S01E01 - X.en.srt").exists()
        assert (season / "Ghost Show - S01E02 - Y.mkv").exists()
        assert (season / "Ghost Show - S01E02 - Y.en.srt").exists()

    def test_prunes_season_dir_when_last_episode_swept(
        self, patch_snapshot, patch_paths
    ):
        sync = patch_paths["sync"]
        season = sync / "tv" / "Ghost Show (2099)" / "Season 01"
        season.mkdir(parents=True)
        (season / "Ghost Show - S01E01 - X.en.srt").write_text("subs")

        counts = cleanup_after_resolve(
            SnapshotKey(
                sync_sub="tv",
                folder="Ghost Show (2099)",
                season=1,
                episode=1,
            )
        )
        assert counts["removed_files"] == 1
        # Season dir empty -> rmdir; show folder also empty -> rmdir (2 dirs)
        assert counts["removed_dirs"] == 2
        assert not season.exists()
        assert not season.parent.exists()
        # Sub root must NOT be pruned (it's a stable mount point).
        assert (sync / "tv").exists()

    def test_no_op_when_video_for_episode_present(self, patch_snapshot, patch_paths):
        """Safety latch: don't strip sidecars when the video is still there."""
        sync = patch_paths["sync"]
        season = sync / "tv" / "Ghost Show (2099)" / "Season 01"
        season.mkdir(parents=True)
        (season / "Ghost Show - S01E01 - X.mkv").write_bytes(b"\0" * 5)
        (season / "Ghost Show - S01E01 - X.en.srt").write_text("subs")

        counts = cleanup_after_resolve(
            SnapshotKey(
                sync_sub="tv",
                folder="Ghost Show (2099)",
                season=1,
                episode=1,
            )
        )
        assert counts == {"removed_files": 0, "removed_dirs": 0}
        assert (season / "Ghost Show - S01E01 - X.mkv").exists()


class TestResolveIntegratesCleanup:
    def test_resolve_returns_aggregated_cleanup_counts(
        self, patch_snapshot, patch_paths, monkeypatch
    ):
        """resolve() runs cleanup_after_resolve for each key in the batch."""
        sync = patch_paths["sync"]
        # Two movies' worth of orphan sidecars.
        m1 = sync / "movies" / "M One"
        m2 = sync / "movies" / "M Two"
        m1.mkdir(parents=True)
        m2.mkdir(parents=True)
        (m1 / "a.srt").write_text("x")
        (m2 / "b.srt").write_text("y")
        (m2 / "c.nfo").write_text("z")

        save_snapshot(
            {
                SnapshotKey(sync_sub="movies", folder="M One"),
                SnapshotKey(sync_sub="movies", folder="M Two"),
            }
        )
        # Reject path keeps the test cheap (no scrobble mock needed).
        results, cleanup = resolve(
            [
                SnapshotKey(sync_sub="movies", folder="M One"),
                SnapshotKey(sync_sub="movies", folder="M Two"),
            ],
            confirm=False,
        )
        assert len(results) == 2
        assert cleanup == {"removed_files": 3, "removed_dirs": 2}
        assert not m1.exists()
        assert not m2.exists()


# ── mark_watched_scope ──────────────────────────────────────────────────────


class TestMarkWatchedScope:
    def test_movie_scrobbles_movie_rating_key(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"ratingKey": "M-300"} if folder == "Movie" else None,
        )
        monkeypatch.setattr(
            "synclet.plex.scrobble",
            lambda rk, **_: called.append(rk) or True,
        )

        r = mark_watched_scope(lib="movies", folder="Movie", scope="movie")
        assert r["scrobbled"] == 1
        assert r["failed"] == 0
        assert called == ["M-300"]

    def test_movie_returns_no_rating_key_when_plex_blank(self, monkeypatch):
        monkeypatch.setattr("synclet.plex.find_in_library", lambda *a, **kw: None)
        monkeypatch.setattr(
            "synclet.plex.scrobble",
            lambda *a, **kw: pytest.fail("must not call scrobble"),
        )
        r = mark_watched_scope(lib="movies", folder="Unknown", scope="movie")
        assert r["scrobbled"] == 0
        assert r["failed"] == 1
        assert r["results"][0]["status"] == "no_rating_key"

    def test_episode_scrobbles_episode_rating_key(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"ratingKey": "SHOW100"} if folder == "Show" else None,
        )
        monkeypatch.setattr(
            "synclet.plex.episode_rating_keys",
            lambda show_rk: (
                {(1, 3): "EP-1-3", (2, 1): "EP-2-1"} if show_rk == "SHOW100" else {}
            ),
        )
        monkeypatch.setattr(
            "synclet.plex.scrobble",
            lambda rk, **_: called.append(rk) or True,
        )
        r = mark_watched_scope(
            lib="tv",
            folder="Show",
            scope="episode",
            season=1,
            episode=3,
        )
        assert r["scrobbled"] == 1
        assert called == ["EP-1-3"]

    def test_season_scrobbles_only_that_season(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"ratingKey": "SHOW100"},
        )
        monkeypatch.setattr(
            "synclet.plex.episode_rating_keys",
            lambda show_rk: {(1, 1): "E-1-1", (1, 2): "E-1-2", (2, 1): "E-2-1"},
        )
        monkeypatch.setattr(
            "synclet.plex.scrobble",
            lambda rk, **_: called.append(rk) or True,
        )
        r = mark_watched_scope(lib="tv", folder="Show", scope="season", season=1)
        assert r["scrobbled"] == 2
        # Both season-1 keys, none from season 2.
        assert set(called) == {"E-1-1", "E-1-2"}

    def test_series_scrobbles_all_episodes(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"ratingKey": "SHOW100"},
        )
        monkeypatch.setattr(
            "synclet.plex.episode_rating_keys",
            lambda show_rk: {(1, 1): "A", (1, 2): "B", (2, 1): "C"},
        )
        monkeypatch.setattr(
            "synclet.plex.scrobble",
            lambda rk, **_: called.append(rk) or True,
        )
        r = mark_watched_scope(lib="tv", folder="Show", scope="series")
        assert r["scrobbled"] == 3
        assert set(called) == {"A", "B", "C"}

    def test_partial_failure_does_not_abort_batch(self, monkeypatch):
        """If one scrobble fails (network/timeout), the rest still run."""
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"ratingKey": "SHOW100"},
        )
        monkeypatch.setattr(
            "synclet.plex.episode_rating_keys",
            lambda show_rk: {(1, 1): "A", (1, 2): "FAIL", (1, 3): "C"},
        )
        monkeypatch.setattr(
            "synclet.plex.scrobble",
            lambda rk, **_: rk != "FAIL",
        )
        r = mark_watched_scope(lib="tv", folder="Show", scope="series")
        assert r["scrobbled"] == 2
        assert r["failed"] == 1
        # Order preserved by sorted ep_map iteration
        statuses = {(it["season"], it["episode"]): it["status"] for it in r["results"]}
        assert statuses[(1, 1)] == "ok"
        assert statuses[(1, 2)] == "scrobble_failed"
        assert statuses[(1, 3)] == "ok"

    def test_unknown_scope_returns_error(self):
        r = mark_watched_scope(lib="tv", folder="Show", scope="bogus")
        assert "error" in r
        assert r["scrobbled"] == 0

    def test_episode_scope_without_season_episode_errors(self):
        r = mark_watched_scope(lib="tv", folder="Show", scope="episode")
        assert "error" in r
        assert r["scrobbled"] == 0
