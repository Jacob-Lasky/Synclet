"""Tests for the Plex XML parser.

Real responses are large; we capture a minimal shape that reproduces the
attributes the parser actually consumes. If Plex changes the XML schema in
ways that drop these attributes, the test breaks loudly.
"""

import pytest

from synclet import plex
from synclet.plex import find_in_library, section_index
from tests._http_mocks import boom_urlopen, fake_urlopen

# Captured from live Plex `/library/sections/2/all` response, trimmed to two
# items. Real responses have Image/Genre/Role/etc. children we don't parse.
# leafCount / viewedLeafCount / viewCount are the watch counters; we exercise
# both a partially-watched show and a fully-watched one to lock the parser.
_FAKE_TV_SECTION_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="2">
<Directory ratingKey="100" title="Better Call Saul" type="show"
  thumb="/library/metadata/100/thumb/123"
  art="/library/metadata/100/art/123"
  year="2015"
  viewCount="42" leafCount="63" viewedLeafCount="42"
  summary="Jimmy McGill becomes Saul Goodman.">
  <Image alt="x" type="coverPoster" url="/x"/>
</Directory>
<Directory ratingKey="200" title="After Life" type="show"
  thumb="/library/metadata/200/thumb/456"
  art="/library/metadata/200/art/456"
  year="2019"
  leafCount="18" viewedLeafCount="18">
</Directory>
</MediaContainer>
"""


class TestSectionIndex:
    def setup_method(self):
        section_index.cache_clear()

    def test_parses_directory_entries(self, monkeypatch):
        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_TV_SECTION_XML),
        )
        idx = section_index(2)
        assert "better call saul" in idx
        assert "after life" in idx

    def test_extracts_thumb_and_art(self, monkeypatch):
        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_TV_SECTION_XML),
        )
        idx = section_index(2)
        bcs = idx["better call saul"]
        assert bcs["thumb"] == "/library/metadata/100/thumb/123"
        assert bcs["art"] == "/library/metadata/100/art/123"
        assert bcs["ratingKey"] == "100"
        assert bcs["year"] == "2015"

    def test_extracts_watch_counters(self, monkeypatch):
        """Plex's view counters are captured so watchstate can fall back to them
        for libraries the user's WatchState daemon does not index."""
        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_TV_SECTION_XML),
        )
        idx = section_index(2)
        bcs = idx["better call saul"]
        assert bcs["view_count"] == 42
        assert bcs["leaf_count"] == 63
        assert bcs["viewed_leaf_count"] == 42
        # After Life has no viewCount attribute on the Directory; absence → 0.
        af = idx["after life"]
        assert af["view_count"] == 0
        assert af["leaf_count"] == 18
        assert af["viewed_leaf_count"] == 18

    def test_handles_api_failure(self, monkeypatch):
        monkeypatch.setattr("synclet.plex.urllib.request.urlopen", boom_urlopen())
        idx = section_index(99)  # different id, cache miss
        assert idx == {}

    def test_find_in_library_with_year_in_folder(self, monkeypatch):
        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_TV_SECTION_XML),
        )
        # Folder name has year + tvdb cruft; lookup key should be normalized
        result = find_in_library("tv", "Better Call Saul (2015) {tvdb-1}")
        assert result is not None
        assert result["ratingKey"] == "100"

    def test_find_in_library_unknown_lib(self):
        assert find_in_library("nonexistent", "anything") is None


# Episode ratingKey + scrobble


_FAKE_ALL_LEAVES_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="3">
<Video ratingKey="1001" parentIndex="1" index="1" type="episode" title="Uno"
  viewCount="2"/>
<Video ratingKey="1002" parentIndex="1" index="2" type="episode" title="Mijo"
  viewCount="1"/>
<Video ratingKey="2001" parentIndex="2" index="1" type="episode" title="Switch"/>
</MediaContainer>
"""


class TestEpisodeRatingKeys:
    def setup_method(self):
        from synclet.plex import episode_rating_keys

        episode_rating_keys.cache_clear()

    def test_returns_mapping(self, monkeypatch):
        from synclet.plex import episode_rating_keys

        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_ALL_LEAVES_XML),
        )
        m = episode_rating_keys("100")
        assert m[1, 1] == "1001"
        assert m[1, 2] == "1002"
        assert m[2, 1] == "2001"

    def test_handles_api_failure(self, monkeypatch):
        from synclet.plex import episode_rating_keys

        monkeypatch.setattr("synclet.plex.urllib.request.urlopen", boom_urlopen())
        # Different key, cache miss, empty result.
        assert episode_rating_keys("999") == {}


class TestEpisodeWatchMap:
    def setup_method(self):
        from synclet.plex import episode_watch_map

        episode_watch_map.cache_clear()

    def test_watched_only_when_view_count_positive(self, monkeypatch):
        from synclet.plex import episode_watch_map

        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_ALL_LEAVES_XML),
        )
        m = episode_watch_map("100")
        # viewCount=2 and 1 → watched; missing viewCount on (2,1) → unwatched.
        assert m[1, 1] is True
        assert m[1, 2] is True
        assert m[2, 1] is False

    def test_handles_api_failure(self, monkeypatch):
        from synclet.plex import episode_watch_map

        monkeypatch.setattr("synclet.plex.urllib.request.urlopen", boom_urlopen())
        assert episode_watch_map("999") == {}


class TestInvalidateWatchCaches:
    def test_clears_section_index_and_episode_watch_map(self, monkeypatch):
        """After a scrobble, both caches must repopulate from Plex on the next
        read. Without invalidation the show-level view_count counters stay stale
        and the user's mark-watched gesture appears to revert on reload."""
        from synclet.plex import (
            episode_watch_map,
            invalidate_watch_caches,
            section_index,
        )

        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_TV_SECTION_XML),
        )
        # Prime section_index.
        assert "better call saul" in section_index(2)
        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_ALL_LEAVES_XML),
        )
        # Prime episode_watch_map.
        assert episode_watch_map("100")

        invalidate_watch_caches()

        # cache_info()'s currsize → 0 confirms eviction; functional behavior
        # after a re-fetch is exercised by the upstream callers' tests.
        assert section_index.cache_info().currsize == 0
        assert episode_watch_map.cache_info().currsize == 0


class TestScrobble:
    def test_calls_plex_with_identifier_and_key(self, monkeypatch):
        from synclet.plex import scrobble

        captured: list[str] = []

        def _capture(url, timeout=8):
            captured.append(url)
            from tests._http_mocks import FakeUrlopenResponse

            return FakeUrlopenResponse(b"", status=200)

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

        monkeypatch.setattr("synclet.plex.urllib.request.urlopen", boom_urlopen())
        assert scrobble("4242") is False

    def test_returns_false_on_non_2xx(self, monkeypatch):
        from synclet.plex import scrobble

        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(b"", status=404),
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


class TestSectionIndexEdgeCases:
    def setup_method(self):
        section_index.cache_clear()

    def test_skips_non_video_or_directory_tags(self, monkeypatch):
        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_SECTION_WITH_NOISE_XML),
        )
        idx = section_index(7)
        # <Image> at the top is ignored, the title-less Directory is skipped,
        # only the Video survives.
        assert list(idx.keys()) == ["real movie"]


# fetch_thumb_bytes / fetch_art_bytes


_FAKE_JPEG_BYTES = b"\xff\xd8\xff\xe0FAKEJPEG"


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
            fake_urlopen(_FAKE_JPEG_BYTES),
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
        # Pre-seed the cache, then patch urlopen to raise so a network attempt
        # would fail the test loudly.
        cache_file = plex_with_meta / "tv__Cached Show.jpg"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(_FAKE_JPEG_BYTES)

        monkeypatch.setattr(
            plex.urllib.request,
            "urlopen",
            boom_urlopen("urlopen should not be called on cache hit"),
        )
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
        monkeypatch.setattr(plex.urllib.request, "urlopen", boom_urlopen())
        assert plex.fetch_thumb_bytes("tv", "Better Call Saul") is None


class TestFetchArtBytes:
    def test_fetch_writes_cache_and_returns_bytes(self, monkeypatch, plex_with_meta):
        monkeypatch.setattr(
            plex.urllib.request,
            "urlopen",
            fake_urlopen(_FAKE_JPEG_BYTES, headers={"Content-Type": "image/png"}),
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
        monkeypatch.setattr(plex.urllib.request, "urlopen", boom_urlopen())
        assert plex.fetch_art_bytes("tv", "Better Call Saul") is None


# episode_rating_keys edge cases


_LEAVES_WITH_NOISE_XML = b"""<?xml version="1.0"?>
<MediaContainer size="4">
<!-- a non-Video element among the leaves -->
<Directory ratingKey="9999" title="not an episode"/>
<!-- non-numeric indexes should be skipped, not crash -->
<Video ratingKey="bad" parentIndex="abc" index="1" type="episode"/>
<Video ratingKey="2002" parentIndex="2" index="3" type="episode"/>
<!-- bogus viewCount must default to 0 (unwatched) without crashing -->
<Video ratingKey="2003" parentIndex="2" index="4" type="episode" viewCount="oops"/>
</MediaContainer>
"""


class TestEpisodeRatingKeysEdgeCases:
    def setup_method(self):
        from synclet.plex import episode_rating_keys, episode_watch_map

        episode_rating_keys.cache_clear()
        episode_watch_map.cache_clear()

    def test_skips_non_video_and_unparseable_indexes(self, monkeypatch):
        from synclet.plex import episode_rating_keys

        monkeypatch.setattr(
            plex.urllib.request,
            "urlopen",
            fake_urlopen(_LEAVES_WITH_NOISE_XML),
        )
        m = episode_rating_keys("777")
        # The Directory and the non-numeric parentIndex are skipped; the two
        # well-formed Videos survive.
        assert m == {(2, 3): "2002", (2, 4): "2003"}

    def test_episode_watch_map_treats_bogus_view_count_as_unwatched(self, monkeypatch):
        from synclet.plex import episode_watch_map

        monkeypatch.setattr(
            plex.urllib.request,
            "urlopen",
            fake_urlopen(_LEAVES_WITH_NOISE_XML),
        )
        m = episode_watch_map("777")
        # Bogus viewCount → default 0 → False, without raising.
        assert m == {(2, 3): False, (2, 4): False}


# get_metadata


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


class TestGetMetadata:
    def test_returns_show_metadata_with_location(self, monkeypatch):
        monkeypatch.setattr(
            plex.urllib.request, "urlopen", fake_urlopen(_METADATA_SHOW_XML)
        )
        meta = plex.get_metadata("100")
        assert meta is not None
        assert meta["title"] == "Better Call Saul"
        assert meta["type"] == "show"
        assert meta["year"] == "2015"
        assert meta["location"] == "/media/tv/Better Call Saul (2015)"

    def test_returns_episode_metadata_with_parent_info(self, monkeypatch):
        monkeypatch.setattr(
            plex.urllib.request, "urlopen", fake_urlopen(_METADATA_EPISODE_XML)
        )
        meta = plex.get_metadata("1001")
        assert meta is not None
        assert meta["title"] == "Uno"
        assert meta["grandparentTitle"] == "Better Call Saul"
        assert meta["parentTitle"] == "Season 1"
        assert meta["librarySectionID"] == "2"
        # Episode has no Location child, absence is None, not an error.
        assert "location" not in meta

    def test_returns_none_on_network_error(self, monkeypatch):
        monkeypatch.setattr(plex.urllib.request, "urlopen", boom_urlopen("no plex"))
        assert plex.get_metadata("any") is None

    def test_returns_none_on_empty_container(self, monkeypatch):
        monkeypatch.setattr(
            plex.urllib.request, "urlopen", fake_urlopen(_METADATA_EMPTY_XML)
        )
        assert plex.get_metadata("doesnt-exist") is None


# ── Disk-cache layer for section_index ───────────────────────────────────────


class TestSectionIndexDiskCache:
    """The disk-cache wrapper around section_index is the load-bearing piece
    of the cold-load fix: after a container restart, the next /api/state hit
    has to read the section index from /data/.plex-section-cache.json instead
    of paying the full Plex round-trip. These tests pin the contract.
    """

    @pytest.fixture(autouse=True)
    def _isolated_disk_cache(self, monkeypatch, tmp_path):
        # Point the disk cache at a tmp file per-test so suites can't cross-
        # contaminate. Also clear the in-process lru_cache so the disk layer
        # is what's being exercised, not stale in-memory state from an earlier
        # test in the same module.
        monkeypatch.setattr(plex, "PLEX_CACHE_FILE", tmp_path / "plex-cache.json")
        plex.section_index.cache_clear()
        yield

    def test_disk_hit_bypasses_plex(self, monkeypatch):
        """Fresh disk cache → section_index returns the disk data WITHOUT
        hitting the network. Critical for the cold-restart speedup."""
        import json
        import time

        plex.PLEX_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with plex.PLEX_CACHE_FILE.open("w") as f:
            json.dump(
                {
                    "ts": time.time(),
                    "sections": {
                        "2": {
                            "from-disk": {
                                "ratingKey": "999",
                                "tag": "Directory",
                                "leaf_count": 3,
                                "viewed_leaf_count": 2,
                                "view_count": 0,
                            }
                        }
                    },
                },
                f,
            )

        # If section_index reached urllib, this would explode the test.
        monkeypatch.setattr("synclet.plex.urllib.request.urlopen", boom_urlopen())

        idx = plex.section_index(2)
        assert "from-disk" in idx
        assert idx["from-disk"]["ratingKey"] == "999"

    def test_stale_disk_cache_triggers_refetch(self, monkeypatch):
        """Disk file older than PLEX_CACHE_TTL_S is treated as a miss."""
        import json

        plex.PLEX_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with plex.PLEX_CACHE_FILE.open("w") as f:
            # ts well outside the 1h default TTL.
            json.dump({"ts": 0, "sections": {"2": {"stale": {}}}}, f)

        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_TV_SECTION_XML),
        )
        idx = plex.section_index(2)
        # Refetch happened: BCS is from the XML, "stale" is dropped.
        assert "better call saul" in idx
        assert "stale" not in idx

    def test_corrupt_disk_cache_falls_through_to_plex(self, monkeypatch):
        """Garbled JSON on disk must not block startup — _load_disk_cache
        returns None and the network fetch repopulates."""
        plex.PLEX_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        plex.PLEX_CACHE_FILE.write_text("{not json at all")

        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_TV_SECTION_XML),
        )
        idx = plex.section_index(2)
        assert "better call saul" in idx

    def test_missing_disk_cache_falls_through_to_plex(self, monkeypatch):
        """First-ever cold start has no file. Must not raise."""
        assert not plex.PLEX_CACHE_FILE.exists()
        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_TV_SECTION_XML),
        )
        idx = plex.section_index(2)
        assert "better call saul" in idx

    def test_plex_fetch_writes_to_disk(self, monkeypatch):
        """A successful Plex round-trip persists to disk so the NEXT restart
        skips the network."""
        import json

        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_TV_SECTION_XML),
        )
        plex.section_index(2)

        assert plex.PLEX_CACHE_FILE.exists()
        with plex.PLEX_CACHE_FILE.open() as f:
            raw = json.load(f)
        assert "2" in raw["sections"]
        assert "better call saul" in raw["sections"]["2"]

    def test_multiple_sections_merge_on_disk(self, monkeypatch):
        """Two section_index calls for different ids share one disk file;
        neither overwrites the other."""
        import json

        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_TV_SECTION_XML),
        )
        plex.section_index(2)
        plex.section_index(12)  # different id; same XML for the fixture

        with plex.PLEX_CACHE_FILE.open() as f:
            raw = json.load(f)
        assert set(raw["sections"].keys()) == {"2", "12"}

    def test_empty_plex_result_skips_disk_write(self, monkeypatch):
        """If Plex returns no data (broken section, auth failure), don't
        persist an empty dict — that would block legitimate refetch retries
        until TTL expiry."""
        monkeypatch.setattr("synclet.plex.urllib.request.urlopen", boom_urlopen())
        idx = plex.section_index(99)
        assert idx == {}
        assert not plex.PLEX_CACHE_FILE.exists()

    def test_invalidate_watch_caches_removes_disk_file(self, monkeypatch):
        """Scrobble invalidates lru_cache; the disk file must follow so a
        container restart inside the TTL window doesn't resurrect the pre-
        scrobble view counts."""
        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_TV_SECTION_XML),
        )
        plex.section_index(2)
        assert plex.PLEX_CACHE_FILE.exists()

        plex.invalidate_watch_caches()
        assert not plex.PLEX_CACHE_FILE.exists()
        assert plex.section_index.cache_info().currsize == 0

    def test_invalidate_when_disk_file_already_gone_is_noop(self):
        """Don't raise FileNotFoundError when called before any cache exists."""
        assert not plex.PLEX_CACHE_FILE.exists()
        plex.invalidate_watch_caches()  # must not raise

    def test_atomic_write_uses_tmp_then_rename(self, monkeypatch):
        """A crash partway through the write must not leave a partial JSON
        file. _save_disk_cache_entry writes to a .tmp suffix then renames."""
        monkeypatch.setattr(
            "synclet.plex.urllib.request.urlopen",
            fake_urlopen(_FAKE_TV_SECTION_XML),
        )
        plex.section_index(2)
        # No leftover .tmp after success.
        tmp = plex.PLEX_CACHE_FILE.with_suffix(plex.PLEX_CACHE_FILE.suffix + ".tmp")
        assert not tmp.exists()
        assert plex.PLEX_CACHE_FILE.exists()

    def test_parallel_writes_do_not_lose_sections(self):
        """Two threads calling _save_disk_cache_entry concurrently for
        different sections must both end up on disk. Without _DISK_CACHE_LOCK,
        the read-modify-write race would cause the later writer's "merge" to
        start from a pre-other-thread view of the file and drop the earlier
        section."""
        import concurrent.futures
        import json

        # Spawn many writers across many sections so the race window is wide.
        section_ids = list(range(20))
        payloads = {sec: {f"title-{sec}": {"sec": sec}} for sec in section_ids}

        def _write(sec: int) -> None:
            plex._save_disk_cache_entry(sec, payloads[sec])

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            # list() forces all to complete and surfaces any exception.
            list(ex.map(_write, section_ids))

        with plex.PLEX_CACHE_FILE.open() as f:
            on_disk = json.load(f)
        # Every section written got persisted; no losers.
        assert set(on_disk["sections"].keys()) == {str(s) for s in section_ids}
