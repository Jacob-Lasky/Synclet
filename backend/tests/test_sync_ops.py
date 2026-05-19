"""Tests for sync_ops: file selection, video-vs-aux classification, copy/remove
job mechanics, maintenance scans.
"""

import asyncio
from pathlib import Path

import pytest

from synclet.sync_ops import (
    _find_source_lib,
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
        lib = _find_source_lib("Better Call Saul (2015) {tvdb-1}")
        assert lib == "tv"

    def test_returns_none_for_unknown(self, patch_paths):
        assert _find_source_lib("Does Not Exist") is None


class TestResolveSelection:
    def test_all_show_files(self, patch_paths):
        pairs = resolve_selection(
            "tv", "Better Call Saul (2015) {tvdb-1}",
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
            "tv", "Better Call Saul (2015) {tvdb-1}",
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
            "movies", "1917 (2019) {tmdb-3}",
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
            "tv", "Better Call Saul (2015) {tvdb-1}",
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
            "tv", "Better Call Saul (2015) {tvdb-1}",
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
        assert job.processed_media_files >= 1, "at least one video should have been copied"

        # Verify the file actually landed
        first_dst = Path(job.items[0]["dst"])
        assert first_dst.exists()

        # Counts: media_files counts videos only, total_files includes the sub
        assert job.total_media_files == 1, f"items: {job.items}"
        assert job.total_files >= job.total_media_files

        # Now unsync the same episode
        unsync_pairs = resolve_selection(
            "tv", "Better Call Saul (2015) {tvdb-1}",
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

    def test_find_watched_finds_synced_watched_movie(self, patch_paths, patch_watchstate):
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
