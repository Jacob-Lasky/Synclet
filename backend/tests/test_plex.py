"""Tests for the Plex XML parser.

Real responses are large; we capture a minimal shape that reproduces the
attributes the parser actually consumes. If Plex changes the XML schema in
ways that drop these attributes, the test breaks loudly.
"""

import pytest

from synclet import plex
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


# ── section_index edge cases ─────────────────────────────────────────────────


_SECTION_WITH_NOISE_XML = b"""<?xml version="1.0"?>
<MediaContainer size="3">
<!-- A real Plex response can include sibling tags we don't care about. -->
<Image alt="background" type="art" url="/x"/>
<Directory ratingKey="500" type="show">
  <!-- title attribute missing entirely: must be skipped, not crash -->
</Directory>
<Video ratingKey="600" title="Real Movie" type="movie"
  thumb="/library/metadata/600/thumb/1" year="2024"/>
</MediaContainer>
"""


class _NoiseSectionResp:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return _SECTION_WITH_NOISE_XML


class TestSectionIndexEdgeCases:
    def setup_method(self):
        section_index.cache_clear()

    def test_skips_non_video_or_directory_tags(self, monkeypatch):
        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            lambda *a, **kw: _NoiseSectionResp(),
        )
        idx = section_index(7)
        # <Image> at the top is ignored; the title-less Directory is skipped;
        # only the Video survives.
        assert list(idx.keys()) == ["real movie"]


# ── fetch_thumb_bytes / fetch_art_bytes ─────────────────────────────────────


_FAKE_JPEG_BYTES = b"\xff\xd8\xff\xe0FAKEJPEG"


class _ImageResp:
    def __init__(self, payload: bytes, content_type: str = "image/jpeg"):
        self._payload = payload
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return self._payload


@pytest.fixture
def plex_with_meta(monkeypatch, tmp_path):
    """Patch THUMB_CACHE to a tmp dir and stub find_in_library to return a
    meta dict for the synthetic title. Tests that need the cache or HTTP
    seam build on this.
    """
    monkeypatch.setattr(plex, "THUMB_CACHE", tmp_path / "thumb-cache")
    monkeypatch.setattr(
        plex,
        "find_in_library",
        lambda lib, folder: {
            "thumb": "/library/metadata/100/thumb/abc",
            "art": "/library/metadata/100/art/def",
            "ratingKey": "100",
        },
    )
    return tmp_path / "thumb-cache"


class TestFetchThumbBytes:
    def test_fetch_writes_cache_and_returns_bytes(self, monkeypatch, plex_with_meta):
        monkeypatch.setattr(
            plex.urllib.request,
            "urlopen",
            lambda *a, **kw: _ImageResp(_FAKE_JPEG_BYTES),
        )
        result = plex.fetch_thumb_bytes("tv", "Better Call Saul")
        assert result is not None
        data, ct = result
        assert data == _FAKE_JPEG_BYTES
        assert ct == "image/jpeg"
        # Cache populated for next call
        cache_file = plex_with_meta / "tv__Better Call Saul.jpg"
        assert cache_file.exists()
        assert cache_file.read_bytes() == _FAKE_JPEG_BYTES

    def test_cache_hit_skips_network(self, monkeypatch, plex_with_meta):
        # Pre-seed the cache; urlopen is patched to raise so a network attempt
        # would fail the test loudly.
        cache_file = plex_with_meta / "tv__Cached Show.jpg"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(_FAKE_JPEG_BYTES)

        def _no_network(*_a, **_k):
            msg = "urlopen should not be called on cache hit"
            raise AssertionError(msg)

        monkeypatch.setattr(plex.urllib.request, "urlopen", _no_network)
        result = plex.fetch_thumb_bytes("tv", "Cached Show")
        assert result == (_FAKE_JPEG_BYTES, "image/jpeg")

    def test_returns_none_when_meta_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(plex, "THUMB_CACHE", tmp_path / "tc")
        monkeypatch.setattr(plex, "find_in_library", lambda lib, folder: None)
        assert plex.fetch_thumb_bytes("tv", "Unknown") is None

    def test_returns_none_when_thumb_missing_from_meta(self, monkeypatch, tmp_path):
        monkeypatch.setattr(plex, "THUMB_CACHE", tmp_path / "tc")
        monkeypatch.setattr(
            plex,
            "find_in_library",
            lambda lib, folder: {"ratingKey": "100"},  # no thumb key
        )
        assert plex.fetch_thumb_bytes("tv", "No Thumb") is None

    def test_returns_none_on_network_error(self, monkeypatch, plex_with_meta):
        def _boom(*_a, **_k):
            msg = "network gone"
            raise OSError(msg)

        monkeypatch.setattr(plex.urllib.request, "urlopen", _boom)
        assert plex.fetch_thumb_bytes("tv", "Better Call Saul") is None


class TestFetchArtBytes:
    def test_fetch_writes_cache_and_returns_bytes(self, monkeypatch, plex_with_meta):
        monkeypatch.setattr(
            plex.urllib.request,
            "urlopen",
            lambda *a, **kw: _ImageResp(_FAKE_JPEG_BYTES, "image/png"),
        )
        result = plex.fetch_art_bytes("tv", "Better Call Saul")
        assert result is not None
        data, ct = result
        assert data == _FAKE_JPEG_BYTES
        assert ct == "image/png"
        # Art cache uses a __art suffix so it doesn't collide with thumb
        cache_file = plex_with_meta / "tv__Better Call Saul__art.jpg"
        assert cache_file.exists()

    def test_returns_none_when_art_missing_from_meta(self, monkeypatch, tmp_path):
        monkeypatch.setattr(plex, "THUMB_CACHE", tmp_path / "tc")
        monkeypatch.setattr(
            plex,
            "find_in_library",
            lambda lib, folder: {"ratingKey": "100"},  # no art key
        )
        assert plex.fetch_art_bytes("tv", "No Art") is None

    def test_returns_none_on_network_error(self, monkeypatch, plex_with_meta):
        def _boom(*_a, **_k):
            msg = "network gone"
            raise OSError(msg)

        monkeypatch.setattr(plex.urllib.request, "urlopen", _boom)
        assert plex.fetch_art_bytes("tv", "Better Call Saul") is None


# ── episode_rating_keys edge cases ──────────────────────────────────────────


_LEAVES_WITH_NOISE_XML = b"""<?xml version="1.0"?>
<MediaContainer size="3">
<!-- a non-Video element among the leaves -->
<Directory ratingKey="9999" title="not an episode"/>
<!-- non-numeric indexes should be skipped, not crash -->
<Video ratingKey="bad" parentIndex="abc" index="1" type="episode"/>
<Video ratingKey="2002" parentIndex="2" index="3" type="episode"/>
</MediaContainer>
"""


class _LeavesNoiseResp:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return _LEAVES_WITH_NOISE_XML


class TestEpisodeRatingKeysEdgeCases:
    def setup_method(self):
        from synclet.plex import episode_rating_keys

        episode_rating_keys.cache_clear()

    def test_skips_non_video_and_unparseable_indexes(self, monkeypatch):
        from synclet.plex import episode_rating_keys

        monkeypatch.setattr(
            plex.urllib.request,
            "urlopen",
            lambda *a, **kw: _LeavesNoiseResp(),
        )
        m = episode_rating_keys("777")
        # Only the well-formed Video makes it in; the Directory and the
        # non-numeric parentIndex are skipped.
        assert m == {(2, 3): "2002"}


# ── get_metadata ────────────────────────────────────────────────────────────


_METADATA_SHOW_XML = b"""<?xml version="1.0"?>
<MediaContainer size="1">
<Directory ratingKey="100" type="show" title="Better Call Saul"
  year="2015" thumb="/x" summary="legal drama">
  <Location id="1" path="/media/tv/Better Call Saul (2015)"/>
</Directory>
</MediaContainer>
"""

_METADATA_EPISODE_XML = b"""<?xml version="1.0"?>
<MediaContainer size="1">
<Video ratingKey="1001" type="episode" title="Uno"
  grandparentTitle="Better Call Saul" parentTitle="Season 1"
  librarySectionID="2" year="2015"/>
</MediaContainer>
"""

_METADATA_EMPTY_XML = b"""<?xml version="1.0"?>
<MediaContainer size="0"/>
"""


def _xml_resp(payload: bytes):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return payload

    return lambda *a, **kw: _Resp()


class TestGetMetadata:
    def test_returns_show_metadata_with_location(self, monkeypatch):
        monkeypatch.setattr(
            plex.urllib.request, "urlopen", _xml_resp(_METADATA_SHOW_XML)
        )
        meta = plex.get_metadata("100")
        assert meta is not None
        assert meta["title"] == "Better Call Saul"
        assert meta["type"] == "show"
        assert meta["year"] == "2015"
        assert meta["location"] == "/media/tv/Better Call Saul (2015)"

    def test_returns_episode_metadata_with_parent_info(self, monkeypatch):
        monkeypatch.setattr(
            plex.urllib.request, "urlopen", _xml_resp(_METADATA_EPISODE_XML)
        )
        meta = plex.get_metadata("1001")
        assert meta is not None
        assert meta["title"] == "Uno"
        assert meta["grandparentTitle"] == "Better Call Saul"
        assert meta["parentTitle"] == "Season 1"
        assert meta["librarySectionID"] == "2"
        # Episode has no Location child; absence is None, not an error
        assert "location" not in meta

    def test_returns_none_on_network_error(self, monkeypatch):
        def _boom(*_a, **_k):
            msg = "no plex"
            raise OSError(msg)

        monkeypatch.setattr(plex.urllib.request, "urlopen", _boom)
        assert plex.get_metadata("any") is None

    def test_returns_none_on_empty_container(self, monkeypatch):
        monkeypatch.setattr(
            plex.urllib.request, "urlopen", _xml_resp(_METADATA_EMPTY_XML)
        )
        assert plex.get_metadata("doesnt-exist") is None
