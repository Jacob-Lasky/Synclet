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
