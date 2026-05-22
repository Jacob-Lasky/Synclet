"""Tests for the URL/text → local-title resolver.

Plex metadata lookup is patched out , we don't hit the real Plex server.
The state lookup uses the fixture media tree.
"""

import pytest

from synclet.resolve import resolve_url


@pytest.fixture
def state_loaded(patch_paths, patch_watchstate):
    """Force the state cache to warm up so _by_name has data to search."""
    from synclet.state import get_state

    get_state(force=True)


class TestResolveUrl:
    def test_empty_string(self, state_loaded):
        r = resolve_url("")
        assert r["found"] is False
        assert r["reason"] == "empty"

    def test_text_search_match(self, state_loaded):
        r = resolve_url("better call saul")
        assert r["found"] is True
        assert r["name"] == "Better Call Saul (2015)"
        assert r["via"] == "text_search"

    def test_text_search_no_match(self, state_loaded):
        r = resolve_url("absolutely nothing matches this")
        assert r["found"] is False
        assert r["reason"] == "no_match"

    def test_imdb_url_unsupported(self, state_loaded):
        r = resolve_url("https://www.imdb.com/title/tt1234567/")
        assert r["found"] is False
        assert r["reason"] == "imdb_url_unsupported"
        assert r["imdb_id"] == "tt1234567"

    def test_jellyfin_url_unsupported(self, state_loaded):
        r = resolve_url("http://jellyfin.local/web/#!/details?id=abc123-def")
        assert r["found"] is False
        assert r["reason"] == "jellyfin_url_unsupported"

    def test_plex_url_metadata_fail(self, state_loaded, monkeypatch):
        """When the Plex API call fails, we surface a clear reason rather than crash."""
        monkeypatch.setattr("synclet.resolve.get_metadata", lambda _: None)
        r = resolve_url(
            "https://app.plex.tv/desktop/#!/details?key=%2Flibrary%2Fmetadata%2F12345"
        )
        assert r["found"] is False
        assert r["reason"] == "plex_metadata_lookup_failed"
        assert r["ratingKey"] == "12345"

    def test_plex_show_url(self, state_loaded, monkeypatch):
        """Plex returns a show; we map back to local folder via title."""
        monkeypatch.setattr(
            "synclet.resolve.get_metadata",
            lambda _: {
                "ratingKey": "12345",
                "title": "Better Call Saul",
                "type": "show",
                "location": None,
            },
        )
        r = resolve_url(
            "https://app.plex.tv/desktop/#!/details?key=%2Flibrary%2Fmetadata%2F12345"
        )
        assert r["found"] is True
        assert r["name"] == "Better Call Saul (2015)"
        assert r["via"] == "plex_metadata"

    def test_plex_episode_walks_to_show(self, state_loaded, monkeypatch):
        """For an episode-type metadata key, we use grandparentTitle (the show)."""
        monkeypatch.setattr(
            "synclet.resolve.get_metadata",
            lambda _: {
                "ratingKey": "99",
                "title": "Uno",
                "type": "episode",
                "grandparentTitle": "Better Call Saul",
                "parentTitle": "Season 1",
            },
        )
        r = resolve_url("/library/metadata/99")
        assert r["found"] is True
        assert r["name"] == "Better Call Saul (2015)"
