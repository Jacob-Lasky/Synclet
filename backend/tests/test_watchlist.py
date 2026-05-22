"""Tests for synclet.watchlist — RSS fetch + fuzzy match against library state.

We mock urllib.request.urlopen (the network seam) and synclet.state.get_state
(the library-state seam) so the fuzzy-match join is the only thing under
test. The RSS XML format is captured from a real Plex watchlist response,
trimmed to the attributes the parser consumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from synclet import watchlist


@dataclass
class _FakeBase:
    """Minimal stand-in for synclet.scan.Title.

    watchlist.py reads `.name`, `.lib`, `.folder`, `.kind` off the title's
    `base` field, plus `.watched_pct` and `.synced_pct` off the wrapper.
    """

    name: str
    lib: str
    folder: str
    kind: str


@dataclass
class _FakeTitle:
    base: _FakeBase
    watched_pct: int
    synced_pct: int


def _make_title(
    name: str,
    *,
    lib: str = "tv",
    folder: str | None = None,
    kind: str = "show",
    watched_pct: int = 0,
    synced_pct: int = 0,
) -> _FakeTitle:
    return _FakeTitle(
        base=_FakeBase(name=name, lib=lib, folder=folder or name, kind=kind),
        watched_pct=watched_pct,
        synced_pct=synced_pct,
    )


_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Plex Watchlist</title>
  <item>
    <title>Better Call Saul</title>
    <category>show</category>
    <guid>plex://show/abc123</guid>
  </item>
  <item>
    <title>Dune: Part Two</title>
    <category>movie</category>
    <guid>plex://movie/def456</guid>
  </item>
  <item>
    <title>Unrelated Future Title</title>
    <category>movie</category>
    <guid>plex://movie/ghi789</guid>
  </item>
</channel>
</rss>
"""

_RSS_XML_MISSING_FIELDS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <item>
    <title>Only Title Present</title>
  </item>
  <item>
    <category>movie</category>
    <guid>plex://movie/no-title</guid>
  </item>
</channel>
</rss>
"""


def _mock_urlopen(payload: bytes):
    """Build a context-manager mock for urllib.request.urlopen."""

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return payload

    def _open(*_args, **_kwargs):
        return _Resp()

    return _open


class TestFetchRss:
    def test_returns_parsed_items(self):
        with patch.object(
            watchlist.urllib.request, "urlopen", _mock_urlopen(_RSS_XML)
        ):
            items = watchlist.fetch_rss()
        assert len(items) == 3
        assert items[0] == {
            "title": "Better Call Saul",
            "category": "show",
            "guid": "plex://show/abc123",
        }
        assert items[1]["title"] == "Dune: Part Two"

    def test_drops_items_without_title(self):
        with patch.object(
            watchlist.urllib.request,
            "urlopen",
            _mock_urlopen(_RSS_XML_MISSING_FIELDS),
        ):
            items = watchlist.fetch_rss()
        # The title-less item is dropped; the title-only one keeps empty
        # strings for category and guid (frontend tolerates).
        assert len(items) == 1
        assert items[0] == {
            "title": "Only Title Present",
            "category": "",
            "guid": "",
        }

    def test_network_error_surfaces_as_error_item(self):
        def _boom(*_a, **_k):
            msg = "RSS unreachable"
            raise OSError(msg)

        with patch.object(watchlist.urllib.request, "urlopen", _boom):
            items = watchlist.fetch_rss()
        assert len(items) == 1
        assert "_error" in items[0]
        assert "RSS unreachable" in items[0]["_error"]

    def test_malformed_xml_surfaces_as_error_item(self):
        with patch.object(
            watchlist.urllib.request,
            "urlopen",
            _mock_urlopen(b"<not-valid-xml"),
        ):
            items = watchlist.fetch_rss()
        assert len(items) == 1
        assert "_error" in items[0]


class TestGetWatchlist:
    def test_propagates_fetch_error(self):
        # If fetch_rss returned an error sentinel, get_watchlist should pass
        # it through without trying to join against state.
        def _boom(*_a, **_k):
            msg = "DNS failure"
            raise OSError(msg)

        with patch.object(watchlist.urllib.request, "urlopen", _boom):
            result = watchlist.get_watchlist()
        assert len(result) == 1
        assert "_error" in result[0]

    def test_matched_item_carries_library_metadata(self):
        # Title in RSS matches a title in state with fuzzy >= 0.8 (exact
        # match scores 2.0). The matched entry should carry lib/folder/etc.
        fake_state = [
            _make_title("Better Call Saul", watched_pct=50, synced_pct=80),
            _make_title(
                "After Life",
                folder="After Life (2019) {tvdb-2}",
                watched_pct=10,
                synced_pct=20,
            ),
        ]
        with (
            patch.object(
                watchlist.urllib.request, "urlopen", _mock_urlopen(_RSS_XML)
            ),
            patch.object(watchlist, "get_state", return_value=fake_state),
        ):
            result = watchlist.get_watchlist()

        bcs = next(e for e in result if e["title"] == "Better Call Saul")
        assert bcs["matched"] is True
        assert bcs["lib"] == "tv"
        assert bcs["folder"] == "Better Call Saul"
        assert bcs["name"] == "Better Call Saul"
        assert bcs["watched_pct"] == 50
        assert bcs["synced_pct"] == 80
        assert bcs["kind"] == "show"

    def test_unmatched_item_omits_library_metadata(self):
        # "Unrelated Future Title" has no fuzzy match >= 0.8 against the
        # tiny state we provide. Entry surfaces in result with matched=False
        # and no lib/folder/etc keys.
        fake_state = [
            _make_title("Better Call Saul"),
        ]
        with (
            patch.object(
                watchlist.urllib.request, "urlopen", _mock_urlopen(_RSS_XML)
            ),
            patch.object(watchlist, "get_state", return_value=fake_state),
        ):
            result = watchlist.get_watchlist()

        unmatched = next(
            e for e in result if e["title"] == "Unrelated Future Title"
        )
        assert unmatched["matched"] is False
        assert "lib" not in unmatched
        assert "folder" not in unmatched

    def test_empty_state_yields_all_unmatched(self):
        # No titles in library = nothing to match against. Every RSS item
        # gets matched=False and minimal payload.
        with (
            patch.object(
                watchlist.urllib.request, "urlopen", _mock_urlopen(_RSS_XML)
            ),
            patch.object(watchlist, "get_state", return_value=[]),
        ):
            result = watchlist.get_watchlist()
        assert len(result) == 3
        assert all(e["matched"] is False for e in result)

    def test_empty_rss_yields_empty_list(self):
        empty_rss = b"""<?xml version="1.0"?>
        <rss><channel></channel></rss>
        """
        with (
            patch.object(
                watchlist.urllib.request, "urlopen", _mock_urlopen(empty_rss)
            ),
            patch.object(watchlist, "get_state", return_value=[]),
        ):
            result = watchlist.get_watchlist()
        assert result == []
