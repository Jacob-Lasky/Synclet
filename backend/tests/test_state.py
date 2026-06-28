"""Tests for synclet.state grid aggregation, focused on the title-drift join.

The grid keys watch aggregates by watchstate_key. When Plex stylizes a title
(PLUR1BUS for Pluribus) the folder's key misses, so _lookup_watch falls back to
find_in_library (which joins by external id) and retries with Plex's canonical
title key. Without that fallback the row reads 0% watched.
"""

from __future__ import annotations

from synclet import state
from synclet.scan import Title
from synclet.watchstate import ShowAggregate


def _title(folder: str, *, lib: str = "tv", kind: str = "show") -> Title:
    return Title(
        id=f"{lib}/{folder}",
        lib=lib,
        folder=folder,
        name=folder,
        kind=kind,
        year=None,
        ep_count=10,
        synced_files=0,
        has_synced=False,
    )


class TestLookupWatch:
    def test_direct_title_key_hit(self, monkeypatch):
        # Folder key matches the aggregate directly; find_in_library not needed.
        def _boom(*a, **k):  # pragma: no cover - must not be called
            raise AssertionError("find_in_library should not be called on a hit")

        monkeypatch.setattr(state, "find_in_library", _boom)
        agg = ShowAggregate(watched=7, total=10)
        table = {"pluribus": agg}
        assert (
            state._lookup_watch(_title("Pluribus (2025) {tvdb-436457}"), table) is agg
        )

    def test_falls_back_to_canonical_key_via_find_in_library(self, monkeypatch):
        # Folder key "pluribus" misses; Plex canonical title is the stylized
        # "PLUR1BUS" (key "plur1bus"), which the aggregate is keyed under.
        monkeypatch.setattr(
            state, "find_in_library", lambda lib, folder: {"title": "PLUR1BUS"}
        )
        agg = ShowAggregate(watched=4, total=10)
        table = {"plur1bus": agg}
        assert (
            state._lookup_watch(_title("Pluribus (2025) {tvdb-436457}"), table) is agg
        )

    def test_returns_none_when_unresolvable(self, monkeypatch):
        monkeypatch.setattr(state, "find_in_library", lambda lib, folder: None)
        assert state._lookup_watch(_title("Ghost Show {tvdb-1}"), {}) is None

    def test_movies_table_of_bools(self, monkeypatch):
        # The same helper serves the movie table ({key: bool}); a fallback hit
        # returns the bool so the caller's `1 if ... else 0` stays correct.
        monkeypatch.setattr(
            state, "find_in_library", lambda lib, folder: {"title": "SE7EN"}
        )
        table = {"se7en": True}
        t = _title("Se7en (1995) {tmdb-807}", lib="movies", kind="movie")
        assert state._lookup_watch(t, table) is True
