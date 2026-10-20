"""Per-title arr rescans and Connect webhook parsing."""

from __future__ import annotations

import json
import urllib.error

import pytest

from synclet import arr
from tests._http_mocks import FakeUrlopenResponse

BCS = "Better Call Saul (2015) {tvdb-1}"


@pytest.fixture
def sonarr_env(monkeypatch):
    monkeypatch.setenv("SYNCLET_SONARR_URL", "http://sonarr:8989/")
    monkeypatch.setenv("SYNCLET_SONARR_API_KEY", "k")


def _fake_arr(monkeypatch, routes: dict[str, object]):
    """Answer urlopen by path (method + path, query ignored) and record calls."""
    calls: list[tuple[str, str, dict | None]] = []

    def _open(request, timeout):
        url = request.full_url.split("?", 1)
        path = url[0].split("8989", 1)[-1]
        body = json.loads(request.data) if request.data else None
        calls.append((request.get_method(), request.full_url, body))
        assert request.headers["X-api-key"] == "k"
        answer = routes[f"{request.get_method()} {path}"]
        if isinstance(answer, Exception):
            raise answer
        return FakeUrlopenResponse(json.dumps(answer).encode())

    monkeypatch.setattr(arr.urllib.request, "urlopen", _open)
    return calls


def test_rescan_uses_id_filter_and_matches_folder(sonarr_env, monkeypatch):
    calls = _fake_arr(
        monkeypatch,
        {
            "GET /api/v3/series": [{"id": 7, "path": f"/data/media/tv/{BCS}"}],
            "POST /api/v3/command": {"id": 99, "name": "RescanSeries"},
        },
    )
    out = arr.rescan_title("tv", BCS)
    assert out == {
        "requested": True,
        "app": "sonarr",
        "command": "RescanSeries",
        "item_id": 7,
        "command_id": 99,
    }
    assert calls[0][1] == "http://sonarr:8989/api/v3/series?tvdbId=1"
    assert calls[1][2] == {"name": "RescanSeries", "seriesId": 7}


def test_rescan_falls_back_to_full_list_when_id_misses(sonarr_env, monkeypatch):
    lists = iter(
        [
            [{"id": 3, "path": "/data/media/tv-4kUHD/" + BCS}],  # same id, other lib
            [{"id": 3, "path": "/x/other"}, {"id": 8, "path": f"/data/media/tv/{BCS}"}],
        ]
    )

    def _open(request, timeout):
        if request.get_method() == "GET":
            return FakeUrlopenResponse(json.dumps(next(lists)).encode())
        return FakeUrlopenResponse(json.dumps({"id": 1}).encode())

    monkeypatch.setattr(arr.urllib.request, "urlopen", _open)
    assert arr.rescan_title("tv", BCS)["item_id"] == 8


def test_rescan_reports_title_not_in_arr(sonarr_env, monkeypatch):
    _fake_arr(monkeypatch, {"GET /api/v3/series": []})
    out = arr.rescan_title("tv", "Unknown Show")
    assert out["requested"] is False
    assert out["reason"] == "title not found in arr"


def test_rescan_never_raises_when_arr_is_down(sonarr_env, monkeypatch):
    _fake_arr(monkeypatch, {"GET /api/v3/series": urllib.error.URLError("refused")})
    out = arr.rescan_title("tv", BCS)
    assert out["requested"] is False
    assert "refused" in out["reason"]


def test_rescan_movie_uses_radarr_fields(monkeypatch):
    monkeypatch.setenv("SYNCLET_RADARR_URL", "http://radarr:7878")
    monkeypatch.setenv("SYNCLET_RADARR_API_KEY", "k")
    seen: list[str] = []

    def _open(request, timeout):
        seen.append(request.full_url)
        if request.get_method() == "GET":
            payload = [{"id": 5, "path": "/data/media/movies/1917 (2019) {tmdb-3}"}]
        else:
            assert json.loads(request.data) == {"name": "RescanMovie", "movieId": 5}
            payload = {"id": 2}
        return FakeUrlopenResponse(json.dumps(payload).encode())

    monkeypatch.setattr(arr.urllib.request, "urlopen", _open)
    out = arr.rescan_title("movies", "1917 (2019) {tmdb-3}")
    assert out["command"] == "RescanMovie"
    assert seen[0] == "http://radarr:7878/api/v3/movie?tmdbId=3"


def test_rescan_skips_unconfigured_library(monkeypatch):
    monkeypatch.delenv("SYNCLET_SONARR_URL", raising=False)
    assert arr.rescan_title("tv", BCS)["requested"] is False
    assert arr.rescan_title("YouTube", "Channel")["requested"] is False


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"eventType": "Download", "series": {"path": f"/data/media/tv/{BCS}"}},
            ("tv", BCS),
        ),
        (
            {
                "eventType": "Download",
                "movie": {"folderPath": "/m/movies-4kUHD/Dune (2021)"},
            },
            ("movies-4kUHD", "Dune (2021)"),
        ),
        (
            {"eventType": "Test", "series": {"path": "/data/media/tv/Test Title"}},
            ("tv", "Test Title"),
        ),
        ({"eventType": "Test"}, None),
        ({"eventType": "Health"}, None),
        ({"series": {"path": "/data/media/notalib/X"}}, None),
        ({"series": {"path": "/data/media/tv/.."}}, None),
        ({"series": {"path": "/data/media/tv"}}, None),
        ({"series": "not a dict"}, None),
    ],
)
def test_title_from_webhook(payload, expected):
    assert arr.title_from_webhook(payload) == expected


def test_two_items_with_the_same_library_folder_are_ambiguous(sonarr_env, monkeypatch):
    _fake_arr(
        monkeypatch,
        {
            "GET /api/v3/series": [
                {"id": 1, "path": "/archive/tv/Some Channel"},
                {"id": 2, "path": "/data/media/tv/Some Channel"},
            ]
        },
    )
    out = arr.rescan_title("tv", "Some Channel")
    assert out == {
        "requested": False,
        "app": "sonarr",
        "reason": "title not found in arr",
    }
