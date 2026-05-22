"""Tests for the Plex XML parser.

Real responses are large; we capture a minimal shape that reproduces the
attributes the parser actually consumes. If Plex changes the XML schema in
ways that drop these attributes, the test breaks loudly.
"""


from synclet.plex import find_in_library, section_index

# Captured from live Plex `/library/sections/2/all` response, trimmed to two
# items. Real responses have Image/Genre/Role/etc. children we don't parse.
_FAKE_TV_SECTION_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="2">
<Directory ratingKey="100" title="Better Call Saul" type="show"
  thumb="/library/metadata/100/thumb/123"
  art="/library/metadata/100/art/123"
  year="2015"
  summary="Jimmy McGill becomes Saul Goodman.">
  <Image alt="x" type="coverPoster" url="/x"/>
</Directory>
<Directory ratingKey="200" title="After Life" type="show"
  thumb="/library/metadata/200/thumb/456"
  art="/library/metadata/200/art/456"
  year="2019">
</Directory>
</MediaContainer>
"""


def _mock_urlopen(_):
    """Return an object whose .read() yields our fixture XML."""

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return _FAKE_TV_SECTION_XML

    return _Resp()


class TestSectionIndex:
    def setup_method(self):
        section_index.cache_clear()

    def test_parses_directory_entries(self, monkeypatch):
        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen", lambda *a, **kw: _mock_urlopen(None)
        )
        idx = section_index(2)
        assert "better call saul" in idx
        assert "after life" in idx

    def test_extracts_thumb_and_art(self, monkeypatch):
        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen", lambda *a, **kw: _mock_urlopen(None)
        )
        idx = section_index(2)
        bcs = idx["better call saul"]
        assert bcs["thumb"] == "/library/metadata/100/thumb/123"
        assert bcs["art"] == "/library/metadata/100/art/123"
        assert bcs["ratingKey"] == "100"
        assert bcs["year"] == "2015"

    def test_handles_api_failure(self, monkeypatch):
        def _raise(*a, **kw):
            raise OSError("network gone")

        monkeypatch.setattr("synclet.plex.urllib.request.urlopen", _raise)
        idx = section_index(99)  # different id → cache miss
        assert idx == {}

    def test_find_in_library_with_year_in_folder(self, monkeypatch):
        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen", lambda *a, **kw: _mock_urlopen(None)
        )
        # Folder name has year + tvdb cruft; lookup key should be normalized
        result = find_in_library("tv", "Better Call Saul (2015) {tvdb-1}")
        assert result is not None
        assert result["ratingKey"] == "100"

    def test_find_in_library_unknown_lib(self):
        assert find_in_library("nonexistent", "anything") is None


# ── Episode ratingKey + scrobble ─────────────────────────────────────────────


_FAKE_ALL_LEAVES_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="3">
<Video ratingKey="1001" parentIndex="1" index="1" type="episode" title="Uno"/>
<Video ratingKey="1002" parentIndex="1" index="2" type="episode" title="Mijo"/>
<Video ratingKey="2001" parentIndex="2" index="1" type="episode" title="Switch"/>
</MediaContainer>
"""


class _AllLeavesResp:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self):
        return _FAKE_ALL_LEAVES_XML


class TestEpisodeRatingKeys:
    def setup_method(self):
        from synclet.plex import episode_rating_keys

        episode_rating_keys.cache_clear()

    def test_returns_mapping(self, monkeypatch):
        from synclet.plex import episode_rating_keys

        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen", lambda *a, **kw: _AllLeavesResp()
        )
        m = episode_rating_keys("100")
        assert m[1, 1] == "1001"
        assert m[1, 2] == "1002"
        assert m[2, 1] == "2001"

    def test_handles_api_failure(self, monkeypatch):
        from synclet.plex import episode_rating_keys

        def _raise(*_a, **_kw):
            raise OSError("network gone")

        monkeypatch.setattr("synclet.plex.urllib.request.urlopen", _raise)
        # Different key -> cache miss -> empty.
        assert episode_rating_keys("999") == {}


class _ScrobbleResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class TestScrobble:
    def test_calls_plex_with_identifier_and_key(self, monkeypatch):
        from synclet.plex import scrobble

        captured: list[str] = []

        def _capture(url, timeout=8):
            captured.append(url)
            return _ScrobbleResp()

        monkeypatch.setattr("synclet.plex.urllib.request.urlopen", _capture)
        ok = scrobble("4242")
        assert ok is True
        assert len(captured) == 1
        u = captured[0]
        assert "/:/scrobble" in u
        assert "identifier=com.plexapp.plugins.library" in u
        assert "key=4242" in u
        # The X-Plex-Token query parameter must be present (auth)
        assert "X-Plex-Token=" in u

    def test_returns_false_on_network_error(self, monkeypatch):
        from synclet.plex import scrobble

        def _raise(*_a, **_kw):
            raise OSError("network gone")

        monkeypatch.setattr("synclet.plex.urllib.request.urlopen", _raise)
        assert scrobble("4242") is False

    def test_returns_false_on_non_2xx(self, monkeypatch):
        from synclet.plex import scrobble

        class _Resp404:
            status = 404

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen", lambda *a, **kw: _Resp404()
        )
        assert scrobble("4242") is False
