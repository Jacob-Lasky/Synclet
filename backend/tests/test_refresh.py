"""Refreshing synced copies when the library file behind them changes."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path

import pytest

MOVIE = "1917 (2019) {tmdb-3}"
SHOW = "Better Call Saul (2015) {tvdb-1}"
BCS_E01 = "Better Call Saul - S01E01 - Uno [WEBDL-1080p].mkv"
BCS_E02 = "Better Call Saul - S01E02 - Mijo [WEBDL-1080p].mkv"
BCS_E01_SUB = "Better Call Saul - S01E01 - Uno.en.srt"
AFTER_LIFE_E01 = (
    "After Life (2019) {tvdb-2}/Season 01/After Life - S01E01 - Episode 1.mkv"
)


def _write(path: Path, size: int, *, age_s: float = 3600) -> Path:
    """A file of `size` bytes, last modified `age_s` ago."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\1" * size)
    _age(path, age_s)
    return path


def _age(path: Path, age_s: float) -> None:
    old = time.time() - age_s
    os.utime(path, (old, old))


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


async def _drain() -> None:
    from synclet import sync_ops

    for job in list(sync_ops._JOBS.values()):
        await _finish(job)


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


@pytest.fixture
def current_movie(patch_paths) -> Path:
    """A synced copy made by a sync of the library as it is now (copy2)."""
    src = patch_paths["media"] / "movies" / MOVIE
    synced = patch_paths["sync"] / "movies" / MOVIE
    synced.mkdir(parents=True)
    for name in ("1917.mkv", "1917.en.srt"):
        shutil.copy2(src / name, synced / name)
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

    def test_event_for_a_current_copy_starts_no_job(self, client, current_movie):
        r = client.post(
            "/api/arr/webhook",
            json={"eventType": "Rename", "movie": {"folderPath": f"/x/movies/{MOVIE}"}},
        )
        assert r.json()["refresh_job_id"] is None


class TestRefreshRoutes:
    def test_on_demand_refresh_reports_plan_and_runs_it(self, client, stale_movie):
        r = client.post("/api/synced/refresh", json={"lib": "movies", "folder": MOVIE})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["libs"] == ["movies"]
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

    def test_stale_lists_differing_titles(self, client, stale_movie, patch_paths):
        # The fixture's library files were just written, so they would still be
        # settling; age them past REFRESH_SETTLE_S.
        for f in (patch_paths["media"] / "movies" / MOVIE).iterdir():
            _age(f, 3600)
        _age(patch_paths["media"] / "tv" / AFTER_LIFE_E01, 3600)
        items = client.get("/api/synced/stale").json()["items"]
        assert [(i["libs"], i["folder"], i["needed"]) for i in items] == [
            (["movies"], MOVIE, True)
        ]


class TestPlan:
    def test_in_place_rewrite_is_copied_over_without_removal(self, current_movie):
        from synclet.refresh import plan_refresh

        _write(current_movie / "1917.mkv", 300)  # same name, old size
        plan = plan_refresh("movies", MOVIE)
        assert [Path(c["dst"]).name for c in plan.copies] == ["1917.mkv"]
        assert plan.removals == []

    def test_same_size_rewrite_is_seen_by_a_newer_library_mtime(self, current_movie):
        from synclet.refresh import plan_refresh

        _age(current_movie / "1917.mkv", 7200)  # library file is newer
        copies = plan_refresh("movies", MOVIE).copies
        assert [Path(c["dst"]).name for c in copies] == ["1917.mkv"]

    def test_older_library_mtime_is_not_a_change(self, current_movie, patch_paths):
        from synclet.refresh import plan_refresh

        _age(patch_paths["media"] / "movies" / MOVIE / "1917.mkv", 7200)
        assert not plan_refresh("movies", MOVIE).needed

    def test_show_refreshes_only_synced_episodes(self, patch_paths):
        from synclet.refresh import plan_refresh

        synced = patch_paths["sync"] / "tv" / SHOW / "Season 01"
        _write(synced / "Better Call Saul - S01E01 - Uno [HDTV-720p].mkv", 10)
        _write(synced / "Better Call Saul - S02E05 - Gone.mkv", 10)
        _write(synced / "season-poster.jpg", 3)
        plan = plan_refresh("tv", SHOW)
        assert plan.libs == ["tv"]
        assert {Path(c["dst"]).name for c in plan.copies} == {BCS_E01, BCS_E01_SUB}
        assert all(c["items"] == ["S01E01"] for c in plan.copies)
        assert [Path(r["path"]).name for r in plan.removals] == [
            "Better Call Saul - S01E01 - Uno [HDTV-720p].mkv"
        ]
        # S02E05 is not in the library any more: the offline copy is kept.
        assert plan.skipped == [
            {"item": "S02E05", "reason": "no library video; offline copy kept"}
        ]

    def test_multi_episode_file_is_removed_once_both_episodes_refresh(
        self, patch_paths
    ):
        from synclet.refresh import plan_refresh

        synced = patch_paths["sync"] / "tv" / SHOW / "Season 01"
        combined = _write(synced / "Better Call Saul - S01E01E02 - old.mkv", 10)
        plan = plan_refresh("tv", SHOW)
        assert {Path(c["dst"]).name for c in plan.copies} == {
            BCS_E01,
            BCS_E01_SUB,
            BCS_E02,
        }
        assert plan.removals == [
            {"items": ["S01E01", "S01E02"], "path": str(combined), "size": 10}
        ]

    def test_multi_episode_file_stays_when_one_episode_cannot_refresh(
        self, patch_paths
    ):
        from synclet.refresh import plan_refresh

        (patch_paths["media"] / "tv" / SHOW / "Season 01" / BCS_E02).unlink()
        synced = patch_paths["sync"] / "tv" / SHOW / "Season 01"
        _write(synced / "Better Call Saul - S01E01E02 - old.mkv", 10)
        plan = plan_refresh("tv", SHOW)
        assert plan.removals == []
        assert [s["item"] for s in plan.skipped] == ["S01E02"]

    def test_recently_changed_library_file_waits_to_settle(self, stale_movie):
        from synclet.refresh import plan_refresh

        plan = plan_refresh("movies", MOVIE, settle_s=600)
        assert not plan.needed
        assert "waiting for it to settle" in plan.skipped[0]["reason"]

    def test_future_library_mtime_does_not_wait_forever(self, stale_movie, patch_paths):
        from synclet.refresh import plan_refresh

        for f in (patch_paths["media"] / "movies" / MOVIE).iterdir():
            _age(f, -86400)
        assert plan_refresh("movies", MOVIE, settle_s=600).needed

    def test_unsynced_title_has_nothing_to_do(self, patch_paths):
        from synclet.refresh import plan_refresh

        plan = plan_refresh("movies", MOVIE)
        assert plan.to_dict()["needed"] is False
        assert plan.skipped == []
        assert plan_refresh("nowhere", MOVIE).libs == []

    def test_title_gone_from_library_is_kept(self, patch_paths):
        from synclet.refresh import plan_refresh

        _write(patch_paths["sync"] / "movies" / "Gone (2001)" / "Gone.mkv", 5)
        plan = plan_refresh("movies", "Gone (2001)")
        assert not plan.needed
        assert plan.skipped == [
            {"item": "movie", "reason": "no library video; offline copy kept"}
        ]

    def test_directory_at_a_wanted_name_is_not_a_copy(self, stale_movie):
        from synclet.refresh import plan_refresh

        (stale_movie / "1917.mkv").mkdir()
        plan = plan_refresh("movies", MOVIE)
        assert "1917.mkv" in {Path(c["dst"]).name for c in plan.copies}

    def test_symlinked_synced_title_is_ignored(self, patch_paths, tmp_path):
        from synclet.refresh import plan_refresh

        outside = _write(tmp_path / "outside" / "1917 old.mkv", 5).parent
        (patch_paths["sync"] / "movies").mkdir(parents=True)
        (patch_paths["sync"] / "movies" / MOVIE).symlink_to(outside)
        assert plan_refresh("movies", MOVIE).removals == []

    def test_memo_is_not_trusted_for_sizes(self, patch_paths, current_movie):
        """A rewrite with no arr event must be seen through a memoized tree."""
        from synclet.refresh import plan_refresh

        assert not plan_refresh("movies", MOVIE, live=False).needed  # memoizes
        _write(patch_paths["media"] / "movies" / MOVIE / "1917.mkv", 2048, age_s=0)
        copies = plan_refresh("movies", MOVIE, live=False).copies
        assert [c["size"] for c in copies] == [2048]

    def test_memo_naming_a_gone_file_falls_back_to_a_live_scan(
        self, patch_paths, stale_movie
    ):
        from synclet.refresh import plan_refresh

        plan_refresh("movies", MOVIE, live=False)  # memoizes the tree
        lib = patch_paths["media"] / "movies" / MOVIE
        (lib / "1917.mkv").rename(lib / "1917 [v2].mkv")
        plan = plan_refresh("movies", MOVIE, live=False)
        assert "1917 [v2].mkv" in {Path(c["dst"]).name for c in plan.copies}


class TestSharedSyncFolder:
    """movies and movies-4kUHD both sync into movies/."""

    @pytest.fixture
    def uhd(self, patch_paths) -> Path:
        uhd = patch_paths["media"] / "movies-4kUHD" / MOVIE
        _write(uhd / "1917 [2160p].mkv", 4096)
        _write(uhd / "1917.en.srt", 2)  # same subtitle name as the HD library
        return uhd

    def test_owner_is_the_library_holding_the_synced_video_name(self, uhd, patch_paths):
        from synclet.refresh import plan_refresh

        synced = patch_paths["sync"] / "movies" / MOVIE
        _write(synced / "1917 [2160p].mkv", 100)  # rewritten in place
        plan = plan_refresh("movies", MOVIE)
        assert plan.libs == ["movies-4kUHD"]
        assert sorted(c["src"] for c in plan.copies) == [
            str(uhd / "1917 [2160p].mkv"),
            str(uhd / "1917.en.srt"),
        ]

    def test_a_shared_subtitle_name_does_not_pick_the_owner(self, uhd, patch_paths):
        from synclet.refresh import plan_refresh

        synced = patch_paths["sync"] / "movies" / MOVIE
        _write(synced / "1917 [old 2160p].mkv", 100)
        _write(synced / "1917.en.srt", 2)  # also in the HD library
        plan = plan_refresh("movies", MOVIE)
        assert not plan.needed
        assert "movies and movies-4kUHD" in plan.skipped[0]["reason"]

    def test_only_the_library_with_the_episode_can_own_it(self, patch_paths):
        from synclet.refresh import plan_refresh

        # The show is in both TV libraries, but only HD has S01E01.
        _write(patch_paths["media"] / "tv-4kUHD" / SHOW / "Season 02" / "S02E01.mkv", 9)
        _write(patch_paths["sync"] / "tv" / SHOW / "Season 01" / "S01E01 old.mkv", 9)
        plan = plan_refresh("tv", SHOW)
        assert plan.libs == ["tv"]
        assert {Path(c["dst"]).name for c in plan.copies} == {BCS_E01, BCS_E01_SUB}


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
        assert MOVIE in refresh._failed_at

    @pytest.mark.asyncio
    async def test_failed_removal_is_reported_and_others_still_go(
        self, patch_paths, stale_movie, monkeypatch
    ):
        from synclet import refresh

        real_unlink = Path.unlink

        def unlink(self, missing_ok=False):
            if self.suffix == ".srt" and "old" in self.name:
                raise PermissionError("read-only")
            real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", unlink)
        job = refresh.start_refresh(refresh.plan_refresh("movies", MOVIE))
        assert job is not None
        await _finish(job)
        assert job.status == "error"
        assert "removing 1917 old.en.srt" in job.error
        assert "1917 old.mkv" not in _names(stale_movie)

    @pytest.mark.asyncio
    async def test_job_replans_when_it_gets_the_folder(self, patch_paths, stale_movie):
        """A refresh queued behind an unsync acts on what the unsync left."""
        from synclet import refresh, sync_ops

        unsync = sync_ops.start_unsync(
            sync_ops.unsync_items(sync_ops.synced_title_files("movies", MOVIE)),
            title=sync_ops.job_title("movies", MOVIE),
        )
        job = refresh.start_refresh(refresh.plan_refresh("movies", MOVIE))
        assert job is not None
        await _drain()
        assert unsync.status == "done"
        assert job.status == "done"
        assert not (stale_movie / "1917.mkv").exists()  # nothing copied back

    @pytest.mark.asyncio
    async def test_unsync_queued_behind_a_refresh_removes_the_new_copy(
        self, patch_paths, stale_movie
    ):
        from synclet import refresh, sync_ops

        job = refresh.start_refresh(refresh.plan_refresh("movies", MOVIE))
        resolve = lambda: sync_ops.resolve_unsync_selection(  # noqa: E731
            "movies", MOVIE, selection_type="movie"
        )
        unsync = sync_ops.start_unsync(
            sync_ops.unsync_items(resolve()),
            title=sync_ops.job_title("movies", MOVIE),
            resolve=resolve,
        )
        await _drain()
        assert job is not None
        assert job.status == "done"
        assert unsync.status == "done"
        assert not stale_movie.exists() or _names(stale_movie) == set()

    @pytest.mark.asyncio
    async def test_a_queued_refresh_absorbs_later_requests(
        self, patch_paths, stale_movie, monkeypatch
    ):
        import threading

        from synclet import refresh
        from synclet.fs_helpers import copy_into_place

        release = threading.Event()

        def slow_copy(src, dst):
            release.wait(5)
            copy_into_place(src, dst)

        monkeypatch.setattr(refresh, "copy_into_place", slow_copy)
        plan = refresh.plan_refresh("movies", MOVIE)
        running = refresh.start_refresh(plan)
        assert running is not None
        for _ in range(500):
            if running.status == "running":
                break
            await asyncio.sleep(0.01)
        queued = refresh.start_refresh(plan)
        assert queued is not None
        assert queued is not running
        assert queued.status == "queued"
        assert refresh.start_refresh(plan) is queued
        release.set()
        await _drain()
        assert running.status == "done"
        assert queued.status == "done"
        assert queued.total_files == 0  # re-planned: nothing left to do


class TestBackgroundRefresh:
    @pytest.mark.asyncio
    async def test_pass_starts_one_due_refresh_and_backs_off_failures(
        self, patch_paths, stale_movie, monkeypatch
    ):
        from synclet import maint_cache, refresh

        monkeypatch.setattr("synclet.config.REFRESH_SETTLE_S", 0)
        tv = patch_paths["sync"] / "tv" / SHOW / "Season 01"
        _write(tv / "Better Call Saul - S01E01 - Uno [HDTV-720p].mkv", 10)
        refresh.register_builders()
        maint_cache.run_refresh_cycle(full=True)
        refresh._failed_at[MOVIE] = time.time()
        assert await refresh.start_due_refreshes() == [f"tv/{SHOW}"]
        # One at a time: nothing else starts while that one is active.
        assert await refresh.start_due_refreshes() == []
        await _drain()

        refresh._failed_at.clear()
        assert await refresh.start_due_refreshes() == [f"movies/{MOVIE}"]
        await _drain()
        assert _names(stale_movie) == {"1917.mkv", "1917.en.srt", "poster.jpg"}

    @pytest.mark.asyncio
    async def test_nothing_due_before_the_first_check(self, patch_paths):
        from synclet import refresh

        assert await refresh.start_due_refreshes() == []

    def test_one_title_failing_to_plan_does_not_hide_the_others(
        self, patch_paths, stale_movie, monkeypatch
    ):
        from synclet import refresh

        monkeypatch.setattr("synclet.config.REFRESH_SETTLE_S", 0)
        real = refresh.plan_refresh

        def flaky(sync_sub, folder, **kw):
            if sync_sub == "tv":
                raise OSError("share hiccup")
            return real(sync_sub, folder, **kw)

        monkeypatch.setattr(refresh, "plan_refresh", flaky)
        assert [i["folder"] for i in refresh._find_stale_uncached()] == [MOVIE]


class TestEdges:
    def test_library_file_vanishing_mid_plan_skips_the_item(
        self, stale_movie, monkeypatch
    ):
        from synclet import refresh

        real = refresh._file_stat
        monkeypatch.setattr(
            refresh,
            "_file_stat",
            lambda p: None if p.name == "1917.mkv" and "media" in p.parts else real(p),
        )
        plan = refresh.plan_refresh("movies", MOVIE)
        assert not plan.needed
        assert plan.skipped == [
            {
                "item": "movie",
                "reason": "library file vanished while planning; try again",
            }
        ]

    @pytest.mark.asyncio
    async def test_superseded_file_already_gone_is_fine(self, stale_movie):
        from synclet import refresh, sync_ops

        plan = refresh.plan_refresh("movies", MOVIE)
        (stale_movie / "1917 old.mkv").unlink()
        job = sync_ops.new_job("refresh", plan.job_title, plan.copies)
        assert await refresh._execute(job, plan) == {}

    @pytest.mark.asyncio
    async def test_planning_error_in_the_job_is_reported(
        self, stale_movie, monkeypatch
    ):
        from synclet import refresh

        plan = refresh.plan_refresh("movies", MOVIE)

        def broken(*a, **kw):
            raise OSError("share gone")

        monkeypatch.setattr(refresh, "plan_refresh", broken)
        job = refresh.start_refresh(plan)
        assert job is not None
        await _finish(job)
        assert (job.status, job.error) == ("error", "share gone")
        assert "1917 old.mkv" in _names(stale_movie)

    @pytest.mark.asyncio
    async def test_background_skips_settling_and_backed_off_titles(
        self, patch_paths, stale_movie, monkeypatch
    ):
        from synclet import maint_cache, refresh

        # The show is only settling (needed=False); the movie is backed off.
        tv = patch_paths["sync"] / "tv" / SHOW / "Season 01"
        _write(tv / "Better Call Saul - S01E01 - Uno [HDTV-720p].mkv", 10)
        for f in (patch_paths["media"] / "movies" / MOVIE).iterdir():
            _age(f, 3600)
        refresh.register_builders()
        maint_cache.run_refresh_cycle(full=True)
        refresh._failed_at[MOVIE] = time.time()
        assert await refresh.start_due_refreshes() == []
