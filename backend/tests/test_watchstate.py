"""Tests against the WatchState SQLite schema.

The schema is captured in conftest.py:WATCHSTATE_SCHEMA — if WatchState ships
v03 with breaking column changes, these tests break loudly instead of the
production read silently returning empty maps.
"""

from synclet.watchstate import (
    all_watched_movies,
    all_watched_shows,
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


class TestMovieWatchState:
    def test_watched_movie(self, patch_watchstate):
        assert movie_watch_state("The Boys") is True

    def test_unwatched_movie(self, patch_watchstate):
        assert movie_watch_state("1917") is False

    def test_missing_movie_returns_none(self, patch_watchstate):
        assert movie_watch_state("Nonexistent Film") is None


class TestBulkPreload:
    def test_all_shows(self, patch_watchstate):
        shows = all_watched_shows()
        assert "better call saul" in shows
        assert "after life" in shows
        # 3 BCS episodes
        assert len(shows["better call saul"]) == 3

    def test_all_movies(self, patch_watchstate):
        movies = all_watched_movies()
        assert movies["the boys"] is True
        assert movies["1917"] is False
