"""Tests for backend/synclet/syncthing.py.

We mock the module-level `_get` helper rather than reaching into httpx,
because the join logic is what matters; the HTTP transport is a thin
shell. Each test provides canned upstream responses keyed by REST path
plus query params.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from synclet import config, syncthing


def _params_key(params: dict[str, str] | None) -> str:
    if not params:
        return ""
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()))


def _make_mock_get(responses: dict[tuple[str, str], Any]):
    """Returns a coroutine that satisfies syncthing._get's signature.

    Lookup is keyed by (path, sorted-query-string). Any unmocked lookup
    raises KeyError loudly so missing fixtures fail fast.
    """

    async def mock_get(
        _client: Any,
        path: str,
        params: dict[str, str] | None = None,
    ) -> Any:
        key = (path, _params_key(params))
        if key not in responses:
            msg = f"unmocked Syncthing call: {key}"
            raise KeyError(msg)
        return responses[key]

    return mock_get


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "SYNCTHING_URL", "http://syncthing.test:8384")
    monkeypatch.setattr(config, "SYNCTHING_API_KEY", "test-api-key")


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.setattr(config, "SYNCTHING_URL", None)
    monkeypatch.setattr(config, "SYNCTHING_API_KEY", None)


class TestIsConfigured:
    def test_both_set(self, configured):
        assert syncthing.is_configured() is True

    def test_neither_set(self, unconfigured):
        assert syncthing.is_configured() is False

    def test_only_url_set(self, monkeypatch):
        monkeypatch.setattr(config, "SYNCTHING_URL", "http://x:8384")
        monkeypatch.setattr(config, "SYNCTHING_API_KEY", None)
        assert syncthing.is_configured() is False

    def test_only_key_set(self, monkeypatch):
        monkeypatch.setattr(config, "SYNCTHING_URL", None)
        monkeypatch.setattr(config, "SYNCTHING_API_KEY", "k")
        assert syncthing.is_configured() is False


class TestOverviewUnconfigured:
    async def test_returns_empty(self, unconfigured):
        result = await syncthing.overview()
        assert result == []


class TestOverviewHappyPath:
    async def test_single_folder_two_remote_devices(self, configured, monkeypatch):
        responses = {
            ("/rest/config/folders", ""): [
                {
                    "id": "default",
                    "label": "Media",
                    "devices": [
                        {"deviceID": "DEV_OWN"},
                        {"deviceID": "DEV_LAPTOP"},
                        {"deviceID": "DEV_PHONE"},
                    ],
                },
            ],
            ("/rest/config/devices", ""): [
                {"deviceID": "DEV_OWN", "name": "Tower"},
                {"deviceID": "DEV_LAPTOP", "name": "Laptop"},
                {"deviceID": "DEV_PHONE", "name": "Phone"},
            ],
            ("/rest/system/connections", ""): {
                "this": {"deviceID": "DEV_OWN"},
                "connections": {
                    "DEV_LAPTOP": {"connected": True, "paused": False},
                    "DEV_PHONE": {"connected": False, "paused": False},
                },
            },
            ("/rest/db/status", "folder=default"): {
                "globalBytes": 1000,
                "inSyncBytes": 750,
                "needBytes": 250,
                "state": "syncing",
            },
            ("/rest/db/completion", "device=DEV_LAPTOP&folder=default"): {
                "completion": 100.0,
                "needBytes": 0,
            },
            ("/rest/db/completion", "device=DEV_PHONE&folder=default"): {
                "completion": 60.0,
                "needBytes": 400,
            },
        }
        monkeypatch.setattr(syncthing, "_get", _make_mock_get(responses))

        result = await syncthing.overview()

        assert len(result) == 1
        folder = result[0]
        assert folder["folder_id"] == "default"
        assert folder["label"] == "Media"
        assert folder["percent"] == 75.0
        assert folder["in_sync_bytes"] == 750
        assert folder["global_bytes"] == 1000
        assert folder["need_bytes"] == 250
        assert folder["state"] == "syncing"

        # Own device is excluded; remote devices preserve insertion order.
        assert len(folder["devices"]) == 2
        laptop, phone = folder["devices"]
        assert laptop["device_id"] == "DEV_LAPTOP"
        assert laptop["name"] == "Laptop"
        assert laptop["completion"] == 100.0
        assert laptop["connected"] is True
        assert laptop["paused"] is False
        assert phone["device_id"] == "DEV_PHONE"
        assert phone["completion"] == 60.0
        assert phone["need_bytes"] == 400
        assert phone["connected"] is False


class TestOverviewEdgeCases:
    async def test_zero_global_bytes_yields_100_percent(self, configured, monkeypatch):
        """Empty folder is 100% in sync by convention; division-by-zero guard."""
        responses = {
            ("/rest/config/folders", ""): [
                {"id": "empty", "label": "Empty", "devices": [{"deviceID": "DEV_OWN"}]},
            ],
            ("/rest/config/devices", ""): [{"deviceID": "DEV_OWN", "name": "Tower"}],
            ("/rest/system/connections", ""): {
                "this": {"deviceID": "DEV_OWN"},
                "connections": {},
            },
            ("/rest/db/status", "folder=empty"): {
                "globalBytes": 0,
                "inSyncBytes": 0,
                "needBytes": 0,
                "state": "idle",
            },
        }
        monkeypatch.setattr(syncthing, "_get", _make_mock_get(responses))

        result = await syncthing.overview()
        assert result[0]["percent"] == 100.0
        assert result[0]["devices"] == []

    async def test_no_folders_returns_empty(self, configured, monkeypatch):
        responses = {
            ("/rest/config/folders", ""): [],
            ("/rest/config/devices", ""): [],
            ("/rest/system/connections", ""): {
                "this": {"deviceID": "DEV_OWN"},
                "connections": {},
            },
        }
        monkeypatch.setattr(syncthing, "_get", _make_mock_get(responses))

        result = await syncthing.overview()
        assert result == []

    async def test_device_without_friendly_name_falls_back_to_id_prefix(
        self,
        configured,
        monkeypatch,
    ):
        """Matches Syncthing GUI behavior when the user did not set a name."""
        responses = {
            ("/rest/config/folders", ""): [
                {
                    "id": "default",
                    "label": "Media",
                    "devices": [
                        {"deviceID": "DEV_OWN"},
                        {"deviceID": "ABCDEFGHIJK"},
                    ],
                },
            ],
            ("/rest/config/devices", ""): [
                {"deviceID": "DEV_OWN", "name": "Tower"},
                {"deviceID": "ABCDEFGHIJK", "name": ""},
            ],
            ("/rest/system/connections", ""): {
                "this": {"deviceID": "DEV_OWN"},
                "connections": {},
            },
            ("/rest/db/status", "folder=default"): {
                "globalBytes": 100,
                "inSyncBytes": 100,
                "needBytes": 0,
                "state": "idle",
            },
            ("/rest/db/completion", "device=ABCDEFGHIJK&folder=default"): {
                "completion": 100.0,
                "needBytes": 0,
            },
        }
        monkeypatch.setattr(syncthing, "_get", _make_mock_get(responses))

        result = await syncthing.overview()
        assert result[0]["devices"][0]["name"] == "ABCDEFG"


class TestOverviewErrorHandling:
    async def test_unreachable_returns_empty(self, configured, monkeypatch):
        async def raise_connect_error(*_args, **_kwargs):
            msg = "syncthing down"
            raise httpx.ConnectError(msg)

        monkeypatch.setattr(syncthing, "_get", raise_connect_error)

        result = await syncthing.overview()
        assert result == []

    async def test_http_500_returns_empty(self, configured, monkeypatch):
        async def raise_http_error(*_args, **_kwargs):
            request = httpx.Request("GET", "http://syncthing.test")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError(
                "server err", request=request, response=response
            )

        monkeypatch.setattr(syncthing, "_get", raise_http_error)

        result = await syncthing.overview()
        assert result == []

    async def test_malformed_response_returns_empty(self, configured, monkeypatch):
        """A response missing an expected key surfaces as empty, not 500."""
        responses = {
            ("/rest/config/folders", ""): [{"label": "Missing ID Field"}],
            ("/rest/config/devices", ""): [],
            ("/rest/system/connections", ""): {"connections": {}},
        }
        monkeypatch.setattr(syncthing, "_get", _make_mock_get(responses))

        result = await syncthing.overview()
        assert result == []
