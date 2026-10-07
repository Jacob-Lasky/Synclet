"""Source deletion is exercised only against throwaway media and sync trees."""

from __future__ import annotations

from pathlib import Path

import pytest

from synclet import media_delete, pending, sync_ops


@pytest.fixture(autouse=True)
def isolate_jobs(monkeypatch):
    # Other tests may leave a queued job for this fixture title in the global
    # registry. Deletion tests need an empty registry unless testing that guard.
    monkeypatch.setattr(sync_ops, "_JOBS", {})


def test_delete_episode_removes_source_sidecars_and_offline_copy(patch_paths):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    offline = patch_paths["sync"] / "tv" / folder / "Season 01"
    offline.mkdir(parents=True)
    synced_ep = offline / "Better Call Saul - S01E01 - Uno.mkv"
    synced_ep.write_bytes(b"offline")
    pending.save_snapshot({pending.SnapshotKey("tv", folder, 1, 1)})

    plan = media_delete.plan_selection("tv", folder, "episodes", [(1, 1)])
    assert plan.source_file_count == 3  # video, English and foreign subtitles
    assert plan.offline_file_count == 1

    result = media_delete.delete_selection(
        "tv", folder, "episodes", [(1, 1)], plan.fingerprint
    )
    assert result["source_deleted"] == 3
    assert result["offline_deleted"] == 1
    assert not synced_ep.exists()
    assert not any("S01E01" in p.name for p in season.iterdir())
    assert any("S01E02" in p.name for p in season.iterdir())
    assert pending.load_snapshot() == set()


def test_delete_episode_removes_generated_sidecar_directory(patch_paths):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    sidecar = season / "Better Call Saul - S01E01 - Uno.trickplay" / "320 - 10x10"
    sidecar.mkdir(parents=True)
    thumbnail = sidecar / "00001.jpg"
    thumbnail.write_bytes(b"thumbnail")

    plan = media_delete.plan_selection("tv", folder, "episodes", [(1, 1)])

    assert thumbnail in plan.source_files
    result = media_delete.delete_selection(
        "tv", folder, "episodes", [(1, 1)], plan.fingerprint
    )
    assert result["error"] is None
    assert result["source_deleted"] == 4
    assert not sidecar.exists()
    assert any("S01E02" in path.name for path in season.iterdir())


def test_delete_episode_removes_empty_generated_sidecar_directory(patch_paths):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    sidecar = season / "Better Call Saul - S01E01 - Uno.trickplay" / "320 - 10x10"
    sidecar.mkdir(parents=True)

    plan = media_delete.plan_selection("tv", folder, "episodes", [(1, 1)])
    result = media_delete.delete_selection(
        "tv", folder, "episodes", [(1, 1)], plan.fingerprint
    )

    assert result["error"] is None
    assert not sidecar.parent.exists()
    assert any("S01E02" in path.name for path in season.iterdir())


def test_deleting_every_episode_removes_season_metadata_and_folder(patch_paths):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    chapters = season / ".chapters"
    chapters.mkdir()
    (chapters / "Better Call Saul - S01E01_chapters.xml").write_bytes(b"one")
    (chapters / "Better Call Saul - S01E02_chapters.xml").write_bytes(b"two")
    (chapters / "Better Call Saul - S01E03_chapters.xml").write_bytes(b"orphan")
    (season / "Better Call Saul - S01E03-thumb.jpg").write_bytes(b"orphan")
    (season / "Better Call Saul - S01E03.nfo").write_bytes(b"orphan")
    (season / "Better Call Saul - S01E03.srt").write_bytes(b"orphan")
    orphan_trickplay = season / "Better Call Saul - S01E03.trickplay"
    orphan_trickplay.mkdir()
    (orphan_trickplay / "00001.jpg").write_bytes(b"orphan")
    (season / "season.nfo").write_bytes(b"metadata")

    plan = media_delete.plan_selection("tv", folder, "episodes", [(1, 1), (1, 2)])
    result = media_delete.delete_selection(
        "tv", folder, "episodes", [(1, 1), (1, 2)], plan.fingerprint
    )

    assert result["error"] is None
    assert not season.exists()


def test_deleting_season_keeps_unrelated_chapter_xml(patch_paths):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    chapters = season / ".chapters"
    chapters.mkdir()
    unrelated = chapters / "notes.xml"
    unrelated.write_bytes(b"keep")

    plan = media_delete.plan_selection("tv", folder, "episodes", [(1, 1), (1, 2)])
    result = media_delete.delete_selection(
        "tv", folder, "episodes", [(1, 1), (1, 2)], plan.fingerprint
    )

    assert result["error"] is None
    assert unrelated.read_bytes() == b"keep"
    assert season.exists()


def test_duplicate_episode_in_sibling_season_keeps_remaining_metadata(patch_paths):
    folder = "Better Call Saul (2015) {tvdb-1}"
    title = patch_paths["media"] / "tv" / folder
    season = title / "Season 01"
    duplicate_season = title / "Season 1"
    duplicate_season.mkdir()
    (duplicate_season / "Better Call Saul - S01E02.mkv").write_bytes(b"duplicate")
    nfo = season / "Better Call Saul - S01E02.nfo"
    nfo.write_bytes(b"keep")
    season_nfo = season / "season.nfo"
    season_nfo.write_bytes(b"keep")
    chapters = season / ".chapters"
    chapters.mkdir()
    chapter = chapters / "Better Call Saul - S01E02_chapters.xml"
    chapter.write_bytes(b"keep")

    plan = media_delete.plan_selection("tv", folder, "episodes", [(1, 1)])
    result = media_delete.delete_selection(
        "tv", folder, "episodes", [(1, 1)], plan.fingerprint
    )

    assert result["error"] is None
    assert nfo.is_file()
    assert nfo.read_bytes() == b"keep"
    assert chapter.is_file()
    assert chapter.read_bytes() == b"keep"
    assert season_nfo.read_bytes() == b"keep"
    assert (season / "Better Call Saul - S01E02 - Mijo [WEBDL-1080p].mkv").exists()


def test_season_cleanup_keeps_metadata_for_video_in_sibling_folder(patch_paths):
    folder = "Better Call Saul (2015) {tvdb-1}"
    title = patch_paths["media"] / "tv" / folder
    season = title / "Season 01"
    sibling = title / "Season 1"
    sibling.mkdir()
    (sibling / "Better Call Saul - S01E03.mkv").write_bytes(b"video")
    chapters = season / ".chapters"
    chapters.mkdir()
    chapter = chapters / "Better Call Saul - S01E03_chapters.xml"
    chapter.write_bytes(b"keep")
    season_nfo = season / "season.nfo"
    season_nfo.write_bytes(b"keep")

    plan = media_delete.plan_selection("tv", folder, "episodes", [(1, 1), (1, 2)])
    result = media_delete.delete_selection(
        "tv", folder, "episodes", [(1, 1), (1, 2)], plan.fingerprint
    )

    assert result["error"] is None
    assert chapter.is_file()
    assert chapter.read_bytes() == b"keep"
    assert season_nfo.is_file()
    assert sibling.is_dir()


def test_duplicate_video_episode_identity_is_not_deletable(patch_paths):
    folder = "Better Call Saul (2015) {tvdb-1}"
    title = patch_paths["media"] / "tv" / folder
    sibling = title / "Season 1"
    sibling.mkdir()
    (sibling / "Better Call Saul - S01E02.mkv").write_bytes(b"duplicate")

    with pytest.raises(ValueError, match="duplicate episode identity"):
        media_delete.plan_selection("tv", folder, "episodes", [(1, 2)])


@pytest.mark.parametrize(
    "unindexed_name", ["Better Call Saul 1x03.mkv", "Better Call Saul - S01E03.ts"]
)
def test_unindexed_video_prevents_season_metadata_cleanup(patch_paths, unindexed_name):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    unindexed = season / unindexed_name
    unindexed.write_bytes(b"video")
    subtitle = season / "Better Call Saul - S01E03.srt"
    subtitle.write_bytes(b"keep")
    season_nfo = season / "season.nfo"
    season_nfo.write_bytes(b"keep")

    plan = media_delete.plan_selection("tv", folder, "episodes", [(1, 1), (1, 2)])
    result = media_delete.delete_selection(
        "tv", folder, "episodes", [(1, 1), (1, 2)], plan.fingerprint
    )

    assert result["error"] is None
    assert unindexed.read_bytes() == b"video"
    assert subtitle.is_file()
    assert subtitle.read_bytes() == b"keep"
    assert season_nfo.is_file()


def test_unknown_directory_prevents_season_metadata_cleanup(patch_paths):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    extras = season / "Extras"
    extras.mkdir()
    hidden_video = extras / "Behind the scenes.mkv"
    hidden_video.write_bytes(b"video")
    season_nfo = season / "season.nfo"
    season_nfo.write_bytes(b"keep")

    plan = media_delete.plan_selection("tv", folder, "episodes", [(1, 1), (1, 2)])
    result = media_delete.delete_selection(
        "tv", folder, "episodes", [(1, 1), (1, 2)], plan.fingerprint
    )

    assert result["error"] is None
    assert hidden_video.read_bytes() == b"video"
    assert season_nfo.is_file()


def test_mismatched_episode_filename_is_not_deletable(patch_paths):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    original = season / "Better Call Saul - S01E02 - Mijo [WEBDL-1080p].mkv"
    mismatched = season / "Better Call Saul - S02E02 - Mijo.mkv"
    original.rename(mismatched)
    season_nfo = season / "season.nfo"
    season_nfo.write_bytes(b"keep")

    with pytest.raises(ValueError, match="episode filename disagrees"):
        media_delete.plan_selection("tv", folder, "episodes", [(1, 1), (1, 2)])
    assert mismatched.read_bytes()
    assert season_nfo.is_file()


def test_season_metadata_waits_until_all_videos_are_deleted(patch_paths, monkeypatch):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    season_nfo = season / "season.nfo"
    season_nfo.write_bytes(b"keep while videos remain")
    original_unlink = Path.unlink

    def fail_video_unlink(path, *args, **kwargs):
        if path.suffix.lower() == ".mkv":
            raise OSError("simulated video unlink failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_video_unlink)
    plan = media_delete.plan_selection("tv", folder, "episodes", [(1, 1), (1, 2)])
    result = media_delete.delete_selection(
        "tv", folder, "episodes", [(1, 1), (1, 2)], plan.fingerprint
    )

    assert "simulated video unlink failure" in result["error"]
    assert season_nfo.is_file()
    assert season_nfo.read_bytes() == b"keep while videos remain"


def test_deleting_one_episode_preserves_other_chapters_and_season_metadata(
    patch_paths,
):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    chapters = season / ".chapters"
    chapters.mkdir()
    first = chapters / "Better Call Saul - S01E01_chapters.xml"
    second = chapters / "Better Call Saul - S01E02_chapters.xml"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    nfo = season / "season.nfo"
    nfo.write_bytes(b"metadata")

    plan = media_delete.plan_selection("tv", folder, "episodes", [(1, 1)])
    result = media_delete.delete_selection(
        "tv", folder, "episodes", [(1, 1)], plan.fingerprint
    )

    assert result["error"] is None
    assert not first.exists()
    assert second.read_bytes() == b"two"
    assert nfo.read_bytes() == b"metadata"
    assert season.exists()


def test_delete_refuses_symlinked_episode_sidecar_directory(patch_paths):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    outside = patch_paths["tmp"] / "outside"
    outside.mkdir()
    untouched = outside / "thumb.jpg"
    untouched.write_bytes(b"keep")
    (season / "Better Call Saul - S01E01 - Uno.trickplay").symlink_to(outside)

    with pytest.raises(ValueError, match="symlinked media"):
        media_delete.plan_selection("tv", folder, "episodes", [(1, 1)])
    assert untouched.read_bytes() == b"keep"


def test_delete_refuses_unrecognized_episode_directory(patch_paths):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    extras = season / "Better Call Saul - S01E01 extras"
    extras.mkdir()
    other_episode = extras / "Better Call Saul - S01E02.mkv"
    other_episode.write_bytes(b"keep")

    with pytest.raises(ValueError, match="episode directory"):
        media_delete.plan_selection("tv", folder, "episodes", [(1, 1)])
    assert other_episode.read_bytes() == b"keep"


@pytest.mark.parametrize("extension", [".mkv", ".ts"])
def test_delete_refuses_video_inside_trickplay_directory(patch_paths, extension):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    sidecar = season / "Better Call Saul - S01E01.trickplay"
    sidecar.mkdir()
    other_episode = sidecar / f"Better Call Saul - S01E02{extension}"
    other_episode.write_bytes(b"keep")

    with pytest.raises(ValueError, match="unexpected file inside episode metadata"):
        media_delete.plan_selection("tv", folder, "episodes", [(1, 1)])
    assert other_episode.read_bytes() == b"keep"


def test_delete_scans_each_selected_season_once(patch_paths, monkeypatch):
    folder = "Better Call Saul (2015) {tvdb-1}"
    season = patch_paths["media"] / "tv" / folder / "Season 01"
    original = Path.iterdir
    calls = 0

    def counting_iterdir(path):
        nonlocal calls
        if path == season:
            calls += 1
        return original(path)

    monkeypatch.setattr(Path, "iterdir", counting_iterdir)
    plan = media_delete.plan_selection("tv", folder, "episodes", [(1, 1), (1, 2)])
    assert plan.source_file_count > 0
    # scan_title_detail makes one pass; selection should make only one more.
    assert calls == 2


def test_delete_refuses_stale_preview_without_touching_media(patch_paths):
    folder = "1917 (2019) {tmdb-3}"
    movie = patch_paths["media"] / "movies" / folder / "1917.mkv"
    plan = media_delete.plan_selection("movies", folder, "movie", [])
    movie.write_bytes(b"changed")

    with pytest.raises(ValueError, match="changed since preview"):
        media_delete.delete_selection("movies", folder, "movie", [], plan.fingerprint)
    assert movie.exists()


@pytest.mark.parametrize("folder", ["../tv", "..", "a/b", "\\evil"])
def test_delete_refuses_unsafe_title_path(patch_paths, folder):
    with pytest.raises(ValueError):
        media_delete.plan_selection("tv", folder, "episodes", [(1, 1)])


def test_delete_refuses_symlinked_media(patch_paths):
    outside = patch_paths["tmp"] / "outside.mkv"
    outside.write_bytes(b"keep")
    folder = "1917 (2019) {tmdb-3}"
    linked = patch_paths["media"] / "movies" / folder / "outside.mkv"
    linked.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        media_delete.plan_selection("movies", folder, "movie", [])
    assert outside.read_bytes() == b"keep"


def test_delete_refuses_symlinked_sync_library(patch_paths):
    outside = patch_paths["tmp"] / "outside-sync"
    (patch_paths["sync"] / "tv").rename(outside)
    (patch_paths["sync"] / "tv").symlink_to(outside)
    folder = "Better Call Saul (2015) {tvdb-1}"
    video = (
        patch_paths["media"]
        / "tv"
        / folder
        / "Season 01"
        / "Better Call Saul - S01E01 - Uno [WEBDL-1080p].mkv"
    )

    with pytest.raises(ValueError, match="symlinked offline"):
        media_delete.plan_selection("tv", folder, "episodes", [(1, 1)])
    assert video.exists()


def test_deleting_every_episode_preserves_unrelated_title_files(patch_paths):
    folder = "Better Call Saul (2015) {tvdb-1}"
    title = patch_paths["media"] / "tv" / folder
    extra = title / "poster.jpg"
    extra.write_bytes(b"poster")
    episodes = [(1, 1), (1, 2)]

    plan = media_delete.plan_selection("tv", folder, "episodes", episodes)
    assert extra not in plan.source_files
    result = media_delete.delete_selection(
        "tv", folder, "episodes", episodes, plan.fingerprint
    )

    assert result["title_remaining"] is True
    assert extra.read_bytes() == b"poster"


def test_delete_refuses_active_copy(patch_paths, monkeypatch):
    folder = "1917 (2019) {tmdb-3}"
    movie = patch_paths["media"] / "movies" / folder / "1917.mkv"
    plan = media_delete.plan_selection("movies", folder, "movie", [])
    monkeypatch.setattr(sync_ops, "has_active_job_for_title", lambda title: True)

    with pytest.raises(ValueError, match="active sync job"):
        media_delete.delete_selection("movies", folder, "movie", [], plan.fingerprint)
    assert movie.exists()


def test_partial_delete_reports_completed_files_and_remaining_media(
    patch_paths, monkeypatch
):
    folder = "1917 (2019) {tmdb-3}"
    title = patch_paths["media"] / "movies" / folder
    plan = media_delete.plan_selection("movies", folder, "movie", [])
    assert plan.source_files[-1].suffix == ".mkv"
    unlink = Path.unlink

    def fail_second_file(path, *args, **kwargs):
        if path.name == "1917.fr.srt":
            raise PermissionError(13, "Permission denied", str(path))
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_second_file)
    result = media_delete.delete_selection(
        "movies", folder, "movie", [], plan.fingerprint
    )

    assert result["source_deleted"] == 1
    assert result["source_bytes"] == 2
    assert result["error"] == "Stopped at 1917.fr.srt: Permission denied"
    assert result["title_remaining"] is True
    assert (title / "1917.mkv").exists()
