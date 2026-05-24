"""Tests against the WatchState SQLite schema and the Plex fallback.

The schema is captured in conftest.py:WATCHSTATE_SCHEMA , if WatchState ships
v03 with breaking column changes, these tests break loudly instead of the
production read silently returning empty maps.

The Plex fallback engages when WatchState has no rows for a title (the canonical
case: Plex section 6 / YouTube, which Jake's WatchState daemon does not index).
For those tests the WatchState DB is left intentionally empty for the relevant
title and the Plex client is patched to return controlled fixtures.
"""

import pytest

from synclet import watchstate as ws
from synclet.watchstate import (
    CoverageStat,
    ShowAggregate,
    all_movie_watched,
    all_show_aggregates,
    coverage_counts,
    movie_watch_state,
    show_watch_map,
)


class TestShowWatchMap:
    def test_watched_episodes(self, patch_watchstate):
        m = show_watch_map("Better Call Saul")
        assert m[1, 1] is True
        assert m[1, 2] is True

    def test_unwatched_episode(self, patch_watchstate):
        m = show_watch_map("Better Call Saul")
        assert m[1, 3] is False

    def test_case_insensitive(self, patch_watchstate):
        # COLLATE NOCASE on the SQL query
        m = show_watch_map("BETTER CALL SAUL")
        assert len(m) == 3

    def test_with_year_stripped(self, patch_watchstate):
        # The reader strips trailing (YYYY) before querying
        m = show_watch_map("Better Call Saul (2015)")
        assert len(m) == 3

    def test_missing_show_returns_empty(self, patch_watchstate):
        assert show_watch_map("Does Not Exist") == {}

    def test_missing_show_falls_back_to_plex_when_lib_provided(
        self, patch_watchstate, monkeypatch
    ):
        """WatchState has no YouTube rows; the Plex per-episode map fills in."""
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"ratingKey": "999"},
        )
        monkeypatch.setattr(
            "synclet.plex.episode_watch_map",
            lambda rk: {(1, 1): True, (1, 2): False},
        )
        m = show_watch_map("Some YouTube Show", lib="YouTube", folder="some-yt-show")
        assert m == {(1, 1): True, (1, 2): False}

    def test_fallback_skipped_when_no_lib_folder(self, patch_watchstate, monkeypatch):
        """Legacy call site with title-only must keep its existing semantics."""
        called = {"plex": 0}

        def _spy_find(_lib, _folder):
            called["plex"] += 1
            return None

        monkeypatch.setattr("synclet.plex.find_in_library", _spy_find)
        assert show_watch_map("Does Not Exist") == {}
        assert called["plex"] == 0


class TestMovieWatchState:
    def test_watched_movie(self, patch_watchstate):
        assert movie_watch_state("The Boys") is True

    def test_unwatched_movie(self, patch_watchstate):
        assert movie_watch_state("1917") is False

    def test_missing_movie_returns_none(self, patch_watchstate):
        assert movie_watch_state("Nonexistent Film") is None

    def test_missing_movie_falls_back_to_plex_view_count(
        self, patch_watchstate, monkeypatch
    ):
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"view_count": 3},
        )
        assert (
            movie_watch_state("Missing Movie", lib="movies", folder="missing") is True
        )

    def test_missing_movie_plex_zero_view_count_is_false(
        self, patch_watchstate, monkeypatch
    ):
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"view_count": 0},
        )
        assert (
            movie_watch_state("Missing Movie", lib="movies", folder="missing") is False
        )

    def test_missing_movie_plex_no_match_returns_none(
        self, patch_watchstate, monkeypatch
    ):
        monkeypatch.setattr("synclet.plex.find_in_library", lambda lib, folder: None)
        assert (
            movie_watch_state("Missing Movie", lib="movies", folder="missing") is None
        )


class TestBulkAggregates:
    @pytest.fixture(autouse=True)
    def _bust_caches(self, patch_watchstate, monkeypatch):
        # patch_watchstate already invalidates ws caches; clear the plex side
        # too and DEFAULT-STUB section_index so the merge under test does not
        # silently call live Plex on pocket-dev. Tests that need section_index
        # data re-patch this with their fixture.
        from synclet import plex as plex_mod

        plex_mod.section_index.cache_clear()
        monkeypatch.setattr("synclet.plex.section_index", lambda _sec: {})
        yield

    def test_shows_from_watchstate(self):
        shows = all_show_aggregates()
        # 2 BCS episodes watched of 3 indexed.
        assert shows["better call saul"] == ShowAggregate(watched=2, total=3)
        # After Life: 1 episode, watched.
        assert shows["after life"] == ShowAggregate(watched=1, total=1)

    def test_movies_from_watchstate(self):
        movies = all_movie_watched()
        assert movies["the boys"] is True
        assert movies["1917"] is False

    def test_plex_fills_gap_for_untracked_show_section(self, monkeypatch):
        """Section 6 / YouTube has no WatchState rows in the fixture DB. Plex
        section_index Directory entries provide watched/total via
        viewedLeafCount / leafCount so the grid shows real progress."""

        def _section_index(sec):
            if sec == 6:
                return {
                    "casually explained": {
                        "tag": "Directory",
                        "viewed_leaf_count": 11,
                        "leaf_count": 11,
                    },
                    "alien food": {
                        "tag": "Directory",
                        "viewed_leaf_count": 14,
                        "leaf_count": 15,
                    },
                }
            return {}

        monkeypatch.setattr("synclet.plex.section_index", _section_index)

        shows = all_show_aggregates()
        assert shows["casually explained"] == ShowAggregate(watched=11, total=11)
        assert shows["alien food"] == ShowAggregate(watched=14, total=15)
        # WatchState-known show is untouched
        assert shows["better call saul"] == ShowAggregate(watched=2, total=3)

    def test_partial_coverage_max_merge(self, monkeypatch):
        """Regression: a YouTube show with a handful of Jellyfin rows in
        WatchState used to lock the grid to the WatchState shape (e.g. 2/2)
        even when Plex's leafCount said the show has 11 episodes. MAX-merge
        keeps Plex's larger denominator and the larger watched count."""

        def _section_index(sec):
            if sec == 2:
                return {
                    # WatchState shape would say BCS = 2/3. Plex reports the
                    # show as a 1000-episode beast with 999 watched. MAX must
                    # pick Plex's numbers without losing them to "WS wins".
                    "better call saul": {
                        "tag": "Directory",
                        "viewed_leaf_count": 999,
                        "leaf_count": 1000,
                    },
                }
            return {}

        monkeypatch.setattr("synclet.plex.section_index", _section_index)

        shows = all_show_aggregates()
        assert shows["better call saul"] == ShowAggregate(watched=999, total=1000)

    def test_watchstate_count_wins_when_higher_than_plex(self, monkeypatch):
        """Symmetric case: WatchState has Jellyfin-sourced views Plex never
        saw. WatchState's watched count must not be lost to Plex's smaller
        viewed_leaf_count."""

        def _section_index(sec):
            if sec == 2:
                # Plex says BCS has 100 episodes, 1 watched. WatchState has
                # 2 of 3 watched. MAX picks watched=2 (WS) and total=100 (Plex)
                # so the grid honors both: Jellyfin views counted, full
                # episode list visible.
                return {
                    "better call saul": {
                        "tag": "Directory",
                        "viewed_leaf_count": 1,
                        "leaf_count": 100,
                    },
                }
            return {}

        monkeypatch.setattr("synclet.plex.section_index", _section_index)

        shows = all_show_aggregates()
        assert shows["better call saul"] == ShowAggregate(watched=2, total=100)

    def test_plex_fills_gap_for_untracked_movie_section(self, monkeypatch):
        def _section_index(sec):
            if sec == 7:
                return {
                    "dune": {"tag": "Video", "view_count": 4},
                    "barbie": {"tag": "Video", "view_count": 0},
                }
            return {}

        monkeypatch.setattr("synclet.plex.section_index", _section_index)

        movies = all_movie_watched()
        assert movies["dune"] is True
        assert movies["barbie"] is False

    def test_movie_or_merge_lifts_unwatched_to_watched_when_plex_sees_play(
        self, monkeypatch
    ):
        """The WS fixture has 1917=False (unwatched). If Plex has now recorded
        a play (because the user just scrobbled, before WS's daemon polled),
        the OR-merge must surface that as watched , not let WS's stale False
        mask the fresh signal."""

        def _section_index(sec):
            if sec == 1:
                return {"1917": {"tag": "Video", "view_count": 1}}
            return {}

        monkeypatch.setattr("synclet.plex.section_index", _section_index)

        movies = all_movie_watched()
        assert movies["1917"] is True

    def test_movie_watchstate_true_survives_plex_zero(self, monkeypatch):
        """Inverse: WatchState says True (Jellyfin saw the play). Plex never
        registered the view (the user only watches on Jellyfin). OR-merge
        keeps True; Jellyfin's record is not lost to Plex's 0."""

        def _section_index(sec):
            if sec == 1:
                return {"the boys": {"tag": "Video", "view_count": 0}}
            return {}

        monkeypatch.setattr("synclet.plex.section_index", _section_index)

        movies = all_movie_watched()
        assert movies["the boys"] is True


class TestCoverageCounts:
    def test_show_section_reports_observed_and_expected(
        self, patch_watchstate, monkeypatch
    ):
        """For show sections, expected_rows = sum(leafCount) across the section;
        observed = count of state rows joining by title. The gap is what
        signals 'WatchState daemon is not indexing this section'."""
        from synclet import plex as plex_mod

        plex_mod.section_index.cache_clear()
        monkeypatch.setattr(
            "synclet.plex.section_index",
            lambda sec: (
                {
                    "casually explained": {"tag": "Directory", "leaf_count": 11},
                    "alien food": {"tag": "Directory", "leaf_count": 15},
                }
                if sec == 6
                else {}
            ),
        )
        counts = coverage_counts()
        assert counts["YouTube"] == CoverageStat(watchstate_rows=0, expected_rows=26)

    def test_tracked_show_section_counts_real_rows(self, patch_watchstate, monkeypatch):
        """tv section has Better Call Saul with 3 BCS episode rows in the
        fixture DB. expected_rows reflects Plex's reported leafCount, not the
        observed count."""
        from synclet import plex as plex_mod

        plex_mod.section_index.cache_clear()

        def _section_index(sec):
            if sec == 2:
                return {
                    "better call saul": {"tag": "Directory", "leaf_count": 63},
                }
            return {}

        monkeypatch.setattr("synclet.plex.section_index", _section_index)
        counts = coverage_counts()
        assert counts["tv"] == CoverageStat(watchstate_rows=3, expected_rows=63)

    def test_movie_section_uses_video_count_as_expected(
        self, patch_watchstate, monkeypatch
    ):
        """For movie kinds, expected_rows = number of Video items in section.
        One row per movie if WatchState saw it at all."""
        from synclet import plex as plex_mod

        plex_mod.section_index.cache_clear()

        def _section_index(sec):
            if sec == 1:
                return {
                    "the boys": {"tag": "Video"},
                    "1917": {"tag": "Video"},
                    "missing movie": {"tag": "Video"},
                }
            return {}

        monkeypatch.setattr("synclet.plex.section_index", _section_index)
        counts = coverage_counts()
        # WatchState fixture has 2 movie rows (The Boys, 1917); Plex section
        # advertises 3 Video items, so expected reflects the gap.
        assert counts["movies"] == CoverageStat(watchstate_rows=2, expected_rows=3)


def test_invalidate_cache_clears_aggregate_caches(patch_watchstate):
    """Ensure the new merged-cache entries are cleared too, not just the
    legacy WatchState-only ones."""
    # Prime the caches.
    all_show_aggregates()
    all_movie_watched()
    assert all_show_aggregates.cache_info().currsize == 1
    assert all_movie_watched.cache_info().currsize == 1

    ws.invalidate_cache()

    assert all_show_aggregates.cache_info().currsize == 0
    assert all_movie_watched.cache_info().currsize == 0


# ── Parallel section_index fetch ─────────────────────────────────────────────


class TestFetchIndicesParallel:
    """The threadpool wrapper around section_index. The aggregate functions
    rely on the order of returned indices matching the order of input section
    ids (zip with strict=True in coverage_counts), so the contract under test
    is "order preserved + concurrent execution."
    """

    def test_empty_list_returns_empty(self):
        from synclet.watchstate import _fetch_indices_parallel

        assert _fetch_indices_parallel([]) == []

    def test_preserves_input_order(self, monkeypatch):
        """coverage_counts zips libraries with these indices via strict=True;
        misordering would surface there as a wrong-library badge."""
        from synclet import watchstate as ws_mod

        # Capture the section_index calls and return distinguishable results.
        def _fake_section_index(sec):
            return {f"section-{sec}-title": {"tag": "Directory"}}

        monkeypatch.setattr("synclet.plex.section_index", _fake_section_index)
        out = ws_mod._fetch_indices_parallel([12, 2, 7, 6, 1])
        # Each result's only key encodes its section id; verify order matches
        # input order, not some thread-completion-order shuffle.
        keys = [next(iter(d.keys())) for d in out]
        assert keys == [
            "section-12-title",
            "section-2-title",
            "section-7-title",
            "section-6-title",
            "section-1-title",
        ]

    def test_runs_concurrently_not_serially(self, monkeypatch):
        """The whole reason this helper exists. Each fake section sleeps;
        a serial fetch of N sections would take N * sleep_time, parallel
        should be ~sleep_time."""
        import time

        from synclet import watchstate as ws_mod

        sleep_s = 0.15

        def _slow_section_index(sec):
            time.sleep(sleep_s)
            return {f"sec-{sec}": {}}

        monkeypatch.setattr("synclet.plex.section_index", _slow_section_index)
        start = time.monotonic()
        ws_mod._fetch_indices_parallel([1, 2, 3, 4, 5])
        elapsed = time.monotonic() - start
        # 5 calls serial would be 5*sleep_s = 0.75s. Concurrent should be ~sleep_s
        # plus pool overhead. 2.5x sleep_s is a generous ceiling that catches
        # accidental re-serialization without false-positiving on CI jitter.
        assert elapsed < 2.5 * sleep_s, (
            f"_fetch_indices_parallel took {elapsed:.3f}s, "
            f"suggests serial execution (5*{sleep_s}={5 * sleep_s:.3f}s expected serial)"
        )
