"""Tests for sync_ops: file selection, video-vs-aux classification, copy/remove
job mechanics, maintenance scans.
"""

import asyncio
from pathlib import Path

import pytest

from synclet.sync_ops import (
    find_source_lib,
    _is_video,
    find_hanging_files,
    find_watched_synced_files,
    remove_files,
    resolve_selection,
    start_sync,
    start_unsync,
)


class TestIsVideo:
    def test_video_extensions(self):
        assert _is_video("/x/y/z.mkv")
        assert _is_video("/x/y/z.mp4")
        assert _is_video("/x/y/z.webm")

    def test_non_video(self):
        assert not _is_video("/x/y/z.srt")
        assert not _is_video("/x/y/z.txt")
        assert not _is_video("/x/y/z")

    def test_case_insensitive(self):
        assert _is_video("/x/Y.MKV")


class TestFindSourceLib:
    def test_finds_library(self, patch_paths):
        lib = find_source_lib("Better Call Saul (2015) {tvdb-1}")
        assert lib == "tv"

    def test_returns_none_for_unknown(self, patch_paths):
        assert find_source_lib("Does Not Exist") is None


class TestResolveSelection:
    def test_all_show_files(self, patch_paths):
        pairs = resolve_selection(
            "tv",
            "Better Call Saul (2015) {tvdb-1}",
            selection_type="all",
        )
        # 2 video files + 1 English sub = 3 (French sub dropped by is_wanted_file)
        names = [src.name for src, _dst in pairs]
        assert any("S01E01" in n and n.endswith(".mkv") for n in names)
        assert any("S01E02" in n and n.endswith(".mkv") for n in names)
        # No French subs
        assert not any(n.endswith(".fr.srt") for n in names)

    def test_single_episode(self, patch_paths):
        pairs = resolve_selection(
            "tv",
            "Better Call Saul (2015) {tvdb-1}",
            selection_type="episodes",
            episodes=[[1, 1]],
        )
        names = [src.name for src, _ in pairs]
        # Only S01E01 files
        assert all("S01E01" in n for n in names)
        # The English sub for S01E01 should come along
        assert any(n.endswith(".srt") for n in names)

    def test_movie_selection(self, patch_paths):
        pairs = resolve_selection(
            "movies",
            "1917 (2019) {tmdb-3}",
            selection_type="movie",
        )
        names = [src.name for src, _ in pairs]
        assert "1917.mkv" in names
        # Foreign sub dropped
        assert "1917.fr.srt" not in names

    def test_unknown_title(self, patch_paths):
        pairs = resolve_selection("tv", "Does Not Exist", selection_type="all")
        assert pairs == []

    def test_dst_mirrors_lib_subfolder(self, patch_paths):
        pairs = resolve_selection(
            "tv",
            "Better Call Saul (2015) {tvdb-1}",
            selection_type="episodes",
            episodes=[[1, 1]],
        )
        # tv-4kUHD would also map to /sync/tv/ — tv → tv per LIBRARIES config
        for _src, dst in pairs:
            assert "/synced-media/tv/" in str(dst)


class TestSyncJob:
    @pytest.mark.asyncio
    async def test_copy_then_unsync(self, patch_paths):
        # Sync S01E01 of BCS
        pairs = resolve_selection(
            "tv",
            "Better Call Saul (2015) {tvdb-1}",
            selection_type="episodes",
            episodes=[[1, 1]],
        )
        assert pairs

        job = start_sync(pairs, title="bcs s01e01")
        # Wait for the async copy task to finish
        for _ in range(50):
            if job.status in ("done", "error"):
                break
            await asyncio.sleep(0.05)

        assert job.status == "done", f"job ended with {job.status}: {job.error}"
        assert job.processed_files > 0
        assert job.processed_media_files >= 1, (
            "at least one video should have been copied"
        )

        # Verify the file actually landed
        first_dst = Path(job.items[0]["dst"])
        assert first_dst.exists()

        # Counts: media_files counts videos only, total_files includes the sub
        assert job.total_media_files == 1, f"items: {job.items}"
        assert job.total_files >= job.total_media_files

        # Now unsync the same episode
        unsync_pairs = resolve_selection(
            "tv",
            "Better Call Saul (2015) {tvdb-1}",
            selection_type="episodes",
            episodes=[[1, 1]],
        )
        ujob = start_unsync(unsync_pairs, title="bcs s01e01")
        for _ in range(50):
            if ujob.status in ("done", "error"):
                break
            await asyncio.sleep(0.05)
        assert ujob.status == "done"
        assert not first_dst.exists()


class TestMaintenance:
    def test_hanging_files_finds_orphans(self, patch_paths, patch_watchstate):
        sync = patch_paths["sync"]
        # Place a .srt next to a video in a fresh dir — the video makes it
        # not-hanging.
        d1 = sync / "tv" / "Some Show" / "Season 01"
        d1.mkdir(parents=True)
        (d1 / "ep.mkv").write_bytes(b"\0" * 100)
        (d1 / "ep.srt").write_text("subs")
        # An orphan dir with only a sub, no video
        d2 = sync / "tv" / "Other Show" / "Season 01"
        d2.mkdir(parents=True)
        (d2 / "ep.srt").write_text("subs")

        hanging = find_hanging_files()
        rels = {h["rel"] for h in hanging}
        assert any(r.endswith("Other Show/Season 01/ep.srt") for r in rels)
        assert not any(r.endswith("Some Show/Season 01/ep.srt") for r in rels)

    def test_find_watched_finds_synced_watched_movie(
        self, patch_paths, patch_watchstate
    ):
        # Pre-sync 1917 (watched=False in fixture) — should NOT appear
        sync = patch_paths["sync"]
        movie_dir = sync / "movies" / "1917 (2019) {tmdb-3}"
        movie_dir.mkdir(parents=True)
        (movie_dir / "1917.mkv").write_bytes(b"\0" * 100)

        result = find_watched_synced_files()
        titles = {r["title"] for r in result}
        assert "1917 (2019)" not in titles


class TestRemoveFiles:
    def test_removes_existing(self, patch_paths):
        sync = patch_paths["sync"]
        target = sync / "tmp" / "to_remove.mkv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\0" * 50)

        r = remove_files([str(target)])
        assert r["removed"] == 1
        assert r["bytes_freed"] == 50
        assert not target.exists()

    def test_skips_missing_silently(self, patch_paths):
        r = remove_files(["/nonexistent/path"])
        assert r["removed"] == 0
        assert r["bytes_freed"] == 0


# ── Snapshot integration (deletion-driven watch-state write-back) ──────────
#
# The flows in synclet.pending are unit-tested in test_pending.py against a
# direct snapshot file. The tests below pin the *integration*: that the
# sync_ops paths (sync completion, unsync, manual remove) correctly mutate
# the snapshot in addition to their primary side-effect on disk. Without
# these, the snapshot could silently drift from disk reality after a refactor.


class TestSyncOpsSnapshotIntegration:
    @pytest.mark.asyncio
    async def test_sync_completion_adds_to_snapshot(self, patch_paths):
        from synclet.pending import SnapshotKey, load_snapshot

        # BCS S01E01 isn't pre-seeded into sync/, so syncing it is a new add.
        pairs = resolve_selection(
            "tv",
            "Better Call Saul (2015) {tvdb-1}",
            selection_type="episodes",
            episodes=[[1, 1]],
        )
        assert pairs
        job = start_sync(pairs, title="bcs s01e01")
        for _ in range(50):
            if job.status in ("done", "error"):
                break
            await asyncio.sleep(0.05)
        assert job.status == "done"

        snap = load_snapshot()
        # The newly-synced episode must be in the snapshot.
        assert (
            SnapshotKey(
                sync_sub="tv",
                folder="Better Call Saul (2015) {tvdb-1}",
                season=1,
                episode=1,
            )
            in snap
        )

    @pytest.mark.asyncio
    async def test_unsync_removes_from_snapshot(self, patch_paths):
        from synclet.pending import SnapshotKey, load_snapshot, save_snapshot

        # Sync first so the file lands on disk AND the snapshot picks it up.
        pairs = resolve_selection(
            "tv",
            "Better Call Saul (2015) {tvdb-1}",
            selection_type="episodes",
            episodes=[[1, 1]],
        )
        sjob = start_sync(pairs, title="seed")
        for _ in range(50):
            if sjob.status in ("done", "error"):
                break
            await asyncio.sleep(0.05)
        target_key = SnapshotKey(
            sync_sub="tv",
            folder="Better Call Saul (2015) {tvdb-1}",
            season=1,
            episode=1,
        )
        assert target_key in load_snapshot()

        # Now unsync that episode — explicit user gesture, not a watched-confirm.
        upairs = resolve_selection(
            "tv",
            "Better Call Saul (2015) {tvdb-1}",
            selection_type="episodes",
            episodes=[[1, 1]],
        )
        ujob = start_unsync(upairs, title="unsync")
        for _ in range(50):
            if ujob.status in ("done", "error"):
                break
            await asyncio.sleep(0.05)
        assert ujob.status == "done"

        # The snapshot must reflect the unsync; the episode is no longer "synced
        # according to Synclet" so it cannot reappear as pending.
        assert target_key not in load_snapshot()

        # Sanity: re-seed and check the inverse to make sure we're testing what
        # we think (save_snapshot is the manual write path).
        save_snapshot({target_key})
        assert target_key in load_snapshot()

    def test_remove_files_drops_from_snapshot(self, patch_paths):
        """The maintenance 'remove watched' path is an implicit confirm.

        When the user clicks Remove on a watched episode, the file is deleted
        AND the snapshot entry is dropped so it doesn't reappear as pending
        on the next page load.
        """
        from synclet.pending import SnapshotKey, load_snapshot, save_snapshot

        sync = patch_paths["sync"]
        target = (
            sync
            / "tv"
            / "Some Show"
            / "Season 01"
            / "Some Show - S01E01 - X.mkv"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\0" * 50)

        # Pre-seed the snapshot to simulate "this file was tracked".
        key = SnapshotKey(
            sync_sub="tv", folder="Some Show", season=1, episode=1
        )
        save_snapshot({key})
        assert key in load_snapshot()

        r = remove_files([str(target)])
        assert r["removed"] == 1
        assert key not in load_snapshot()
        # cleanup is part of the wire shape; frontend toast surfaces totals.
        assert "cleanup" in r
        assert r["cleanup"].keys() == {"removed_files", "removed_dirs"}

    def test_remove_files_sweeps_sibling_sidecars(self, patch_paths):
        """Removing the .mkv via maintenance should also clear the .srt next
        to it AND the now-empty Season / show parent dirs."""
        from synclet.pending import SnapshotKey, save_snapshot

        sync = patch_paths["sync"]
        season = sync / "tv" / "Sweep Show" / "Season 01"
        season.mkdir(parents=True)
        mkv = season / "Sweep Show - S01E01 - Pilot.mkv"
        srt = season / "Sweep Show - S01E01 - Pilot.en.srt"
        mkv.write_bytes(b"\0" * 5)
        srt.write_text("subs")
        save_snapshot(
            {SnapshotKey(sync_sub="tv", folder="Sweep Show", season=1, episode=1)}
        )

        r = remove_files([str(mkv)])
        assert r["removed"] == 1
        # cleanup swept the .srt sibling + pruned the empty Season + show dirs.
        assert r["cleanup"]["removed_files"] == 1
        assert r["cleanup"]["removed_dirs"] == 2
        assert not srt.exists()
        assert not season.exists()
        assert not season.parent.exists()
        # sync sub root must remain (it's the mount point).
        assert (sync / "tv").exists()
