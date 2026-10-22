"""Refreshing synced copies when the library file behind them changes."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

MOVIE = "1917 (2019) {tmdb-3}"
SHOW = "Better Call Saul (2015) {tvdb-1}"


def _write(path: Path, size: int, *, age_s: float = 3600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\1" * size)
    old = time.time() - age_s
    os.utime(path, (old, old))
    return path


def _names(path: Path) -> set[str]:
    return {p.name for p in path.iterdir()}


def _wait_jobs(client) -> None:
    deadline = time.monotonic() + 5
    jobs: list[dict] = []
    while time.monotonic() < deadline:
        jobs = client.get("/api/jobs").json()["jobs"]
        if jobs and all(j["status"] in ("done", "error") for j in jobs):
            return
        time.sleep(0.05)
    raise AssertionError(f"jobs did not finish: {jobs}")


async def _finish(job) -> None:
    for _ in range(500):
        if job.status in ("done", "error"):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"job did not finish: {job.to_dict()}")


@pytest.fixture
def stale_movie(patch_paths) -> Path:
    """A synced copy of an older release than the library now holds.

    The library has `1917.mkv` (1024 B) with an English sub, the synced folder
    still has the superseded `1917 old.mkv` and its sub, as after an upgrade.
    """
    synced = patch_paths["sync"] / "movies" / MOVIE
    _write(synced / "1917 old.mkv", 500)
    _write(synced / "1917 old.en.srt", 10)
    _write(synced / "poster.jpg", 7)
    return synced


class TestWebhookRefresh:
    def test_download_event_replaces_superseded_synced_copy(self, client, stale_movie):
        r = client.post(
            "/api/arr/webhook",
            json={
                "eventType": "Download",
                "isUpgrade": True,
                "movie": {"folderPath": f"/data/media/movies/{MOVIE}"},
            },
        )
        assert r.status_code == 201, r.text
        _wait_jobs(client)

        # The current library video and English sub, at their library size;
        # the superseded copies gone; a non-media file left alone.
        assert _names(stale_movie) == {"1917.mkv", "1917.en.srt", "poster.jpg"}
        assert (stale_movie / "1917.mkv").stat().st_size == 1024

    def test_file_delete_event_alone_leaves_the_synced_copy(self, client, stale_movie):
        r = client.post(
            "/api/arr/webhook",
            json={
                "eventType": "MovieFileDelete",
                "movie": {"folderPath": f"/data/media/movies/{MOVIE}"},
            },
        )
        assert "refresh_job_id" not in r.json()
        assert client.get("/api/jobs").json()["jobs"] == []
        assert "1917 old.mkv" in _names(stale_movie)

    def test_event_for_a_current_copy_starts_no_job(self, client, patch_paths):
        synced = patch_paths["sync"] / "movies" / MOVIE
        src = patch_paths["media"] / "movies" / MOVIE
        for name in ("1917.mkv", "1917.en.srt"):
            _write(synced / name, (src / name).stat().st_size)
        r = client.post(
            "/api/arr/webhook",
            json={"eventType": "Rename", "movie": {"folderPath": str(src)}},
        )
        assert r.json()["refresh_job_id"] is None


class TestRefreshRoutes:
    def test_on_demand_refresh_reports_plan_and_runs_it(self, client, stale_movie):
        r = client.post("/api/synced/refresh", json={"lib": "movies", "folder": MOVIE})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["lib"] == "movies"
        assert body["job_id"]
        assert {Path(c["dst"]).name for c in body["copies"]} == {
            "1917.mkv",
            "1917.en.srt",
        }
        assert {Path(x["path"]).name for x in body["removals"]} == {
            "1917 old.mkv",
            "1917 old.en.srt",
        }
        _wait_jobs(client)
        job = client.get(f"/api/jobs/{body['job_id']}").json()
        assert job["op"] == "refresh"
        assert job["status"] == "done"
        assert _names(stale_movie) == {"1917.mkv", "1917.en.srt", "poster.jpg"}

    def test_on_demand_refresh_rejects_bad_title(self, client):
        r = client.post("/api/synced/refresh", json={"lib": "movies", "folder": ".."})
        assert r.status_code == 400

    def test_stale_lists_differing_titles(self, client, stale_movie, monkeypatch):
        monkeypatch.setattr("synclet.config.REFRESH_SETTLE_S", 0)
        items = client.get("/api/synced/stale").json()["items"]
        assert [(i["lib"], i["folder"], i["needed"]) for i in items] == [
            ("movies", MOVIE, True)
        ]


class TestPlan:
    def test_in_place_rewrite_is_copied_over_without_removal(self, patch_paths):
        from synclet.refresh import plan_refresh

        synced = patch_paths["sync"] / "movies" / MOVIE
        _write(synced / "1917.mkv", 300)  # same name, old size
        _write(synced / "1917.en.srt", 2)
        plan = plan_refresh("movies", MOVIE)
        assert [Path(c["dst"]).name for c in plan.copies] == ["1917.mkv"]
        assert plan.removals == []

    def test_show_refreshes_only_synced_episodes(self, patch_paths):
        from synclet.refresh import plan_refresh

        synced = patch_paths["sync"] / "tv" / SHOW / "Season 01"
        _write(synced / "Better Call Saul - S01E01 - Uno [HDTV-720p].mkv", 10)
        _write(synced / "Better Call Saul - S02E05 - Gone.mkv", 10)
        plan = plan_refresh("tv", SHOW)
        assert plan.lib == "tv"
        assert {Path(c["dst"]).name for c in plan.copies} == {
            "Better Call Saul - S01E01 - Uno [WEBDL-1080p].mkv",
            "Better Call Saul - S01E01 - Uno.en.srt",
        }
        assert all(c["item"] == "S01E01" for c in plan.copies)
        assert [Path(r["path"]).name for r in plan.removals] == [
            "Better Call Saul - S01E01 - Uno [HDTV-720p].mkv"
        ]
        # S02E05 is not in the library any more: the offline copy is kept.
        assert plan.skipped == [
            {"item": "S02E05", "reason": "no library video; offline copy kept"}
        ]

    def test_recently_changed_library_file_waits_to_settle(self, patch_paths):
        from synclet.refresh import plan_refresh

        _write(patch_paths["sync"] / "movies" / MOVIE / "1917 old.mkv", 5)
        plan = plan_refresh("movies", MOVIE, settle_s=600)
        assert not plan.needed
        assert "waiting for it to settle" in plan.skipped[0]["reason"]

    def test_unsynced_title_has_nothing_to_do(self, patch_paths):
        from synclet.refresh import plan_refresh

        plan = plan_refresh("movies", MOVIE)
        assert plan.to_dict()["needed"] is False
        assert plan.skipped == []
        assert plan_refresh("nowhere", MOVIE).lib is None

    def test_title_gone_from_library_is_kept(self, patch_paths):
        from synclet.refresh import plan_refresh

        _write(patch_paths["sync"] / "movies" / "Gone (2001)" / "Gone.mkv", 5)
        plan = plan_refresh("movies", "Gone (2001)")
        assert not plan.needed
        assert plan.skipped == [
            {"item": "movie", "reason": "title is not in the library"}
        ]


class TestSharedSyncFolder:
    """movies and movies-4kUHD both sync into movies/."""

    @pytest.fixture
    def both(self, patch_paths) -> dict[str, Path]:
        uhd = patch_paths["media"] / "movies-4kUHD" / MOVIE
        _write(uhd / "1917 [2160p].mkv", 4096)
        return {"synced": patch_paths["sync"] / "movies" / MOVIE, "uhd": uhd}

    def test_owner_is_the_library_holding_the_synced_name(self, both):
        from synclet.refresh import plan_refresh

        _write(both["synced"] / "1917 [2160p].mkv", 100)  # rewritten in place
        plan = plan_refresh("movies", MOVIE)
        assert plan.lib == "movies-4kUHD"
        assert [c["src"] for c in plan.copies] == [
            str(both["uhd"] / "1917 [2160p].mkv")
        ]

    def test_unknown_owner_is_left_alone(self, both):
        from synclet.refresh import plan_refresh

        _write(both["synced"] / "1917 [old].mkv", 100)
        plan = plan_refresh("movies", MOVIE)
        assert plan.lib is None
        assert not plan.needed
        assert "movies and movies-4kUHD" in plan.skipped[0]["reason"]


class TestRunRefresh:
    @pytest.mark.asyncio
    async def test_failed_copy_keeps_the_superseded_file(
        self, patch_paths, stale_movie, monkeypatch
    ):
        from synclet import refresh

        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(refresh, "copy_into_place", boom)
        job = refresh.start_refresh(refresh.plan_refresh("movies", MOVIE))
        assert job is not None
        await _finish(job)
        assert job.status == "error"
        assert "disk full" in job.error
        assert "1917 old.mkv" in _names(stale_movie)
        assert f"movies/{MOVIE}" in refresh._failed_at

    @pytest.mark.asyncio
    async def test_source_changing_mid_copy_keeps_the_superseded_file(
        self, patch_paths, stale_movie, monkeypatch
    ):
        from synclet import refresh
        from synclet.fs_helpers import copy_into_place

        src_video = patch_paths["media"] / "movies" / MOVIE / "1917.mkv"

        def copy_then_grow(src, dst):
            copy_into_place(src, dst)
            if src == src_video:
                src_video.write_bytes(b"\2" * 2048)

        monkeypatch.setattr(refresh, "copy_into_place", copy_then_grow)
        job = refresh.start_refresh(refresh.plan_refresh("movies", MOVIE))
        assert job is not None
        await _finish(job)
        assert job.status == "error"
        assert "changed during the copy" in job.error
        assert "1917 old.mkv" in _names(stale_movie)

    @pytest.mark.asyncio
    async def test_busy_title_is_not_refreshed_twice(self, patch_paths, stale_movie):
        from synclet import refresh

        plan = refresh.plan_refresh("movies", MOVIE)
        first = refresh.start_refresh(plan)
        assert first is not None
        assert refresh.start_refresh(plan) is None
        await _finish(first)
        assert first.status == "done"


class TestBackgroundRefresh:
    @pytest.mark.asyncio
    async def test_pass_starts_due_refreshes_and_backs_off_failures(
        self, patch_paths, stale_movie, monkeypatch
    ):
        from synclet import maint_cache, refresh

        monkeypatch.setattr("synclet.config.REFRESH_SETTLE_S", 0)
        refresh.register_builders()
        maint_cache.run_refresh_cycle(full=True)
        refresh._failed_at[f"movies/{MOVIE}"] = time.time()
        assert await refresh.start_due_refreshes() == []

        refresh._failed_at.clear()
        assert await refresh.start_due_refreshes() == [f"movies/{MOVIE}"]
        for job in list(refresh.sync_ops._JOBS.values()):
            await _finish(job)
        assert _names(stale_movie) == {"1917.mkv", "1917.en.srt", "poster.jpg"}

    @pytest.mark.asyncio
    async def test_nothing_due_before_the_first_check(self, patch_paths):
        from synclet import refresh

        assert await refresh.start_due_refreshes() == []
