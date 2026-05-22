"""Tests for the optional static-file serving wired in main.py.

The production Dockerfile sets SYNCLET_STATIC_DIR=/app/static so Litestar
serves the bundled Vue SPA from the same port as the API. Dev leaves it
unset; the dev frontend runs separately on :1313 against this backend.
Both branches need pinning so a future refactor can't accidentally:
  - poach /api/* requests with the static catchall (would return HTML
    instead of JSON, silently breaking the frontend),
  - skip the static handler when it should be registered, or
  - register the static handler when it shouldn't be (dev).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from litestar import Litestar
from litestar.testing import TestClient

from main import build_route_handlers


class TestBuildRouteHandlers:
    """Unit tests for the route-handler list builder."""

    def test_no_static_handler_when_static_dir_is_none(self):
        """Dev: STATIC_DIR unset -> only API handlers, no static router."""
        handlers = build_route_handlers(static_dir=None)
        # All API handlers are decorated functions/coroutines, not Routers.
        # The static router is a Router instance; its absence proves the
        # dev branch is taken.
        from litestar.router import Router

        assert not any(isinstance(h, Router) for h in handlers)

    def test_no_static_handler_when_dir_does_not_exist(self, tmp_path: Path):
        """STATIC_DIR points somewhere that isn't a directory -> ignored.

        Saves a startup crash if the env var points at a stale path on
        production. Logged at info level by the builder.
        """
        bogus = tmp_path / "does-not-exist"
        handlers = build_route_handlers(static_dir=bogus)
        from litestar.router import Router

        assert not any(isinstance(h, Router) for h in handlers)

    def test_static_handler_appended_when_dir_exists(self, tmp_path: Path):
        """Prod: STATIC_DIR points at a real dist/ -> static router added."""
        (tmp_path / "index.html").write_text("<html>spa</html>")
        handlers = build_route_handlers(static_dir=tmp_path)
        from litestar.router import Router

        routers = [h for h in handlers if isinstance(h, Router)]
        assert len(routers) == 1, (
            "exactly one static-files router expected when STATIC_DIR is set"
        )

    def test_static_router_is_last(self, tmp_path: Path):
        """Static router must be appended AFTER the API handlers.

        Litestar resolves overlapping paths by specificity, but if the static
        router were registered first AND we ever switched to a less-specific
        matcher, /api/* could end up being served as HTML. Position is a
        cheap belt to the suspenders.
        """
        (tmp_path / "index.html").write_text("<html>spa</html>")
        handlers = build_route_handlers(static_dir=tmp_path)
        from litestar.router import Router

        # Last handler in the list is the static router.
        assert isinstance(handlers[-1], Router)


class TestRouteSpecificity:
    """Integration tests via TestClient: API > static, even with both registered.

    These tests boot a fully-configured app with the static dir wired in,
    then probe both surfaces. They pin the contract a UI regression test
    can't pin: that the BACKEND keeps API routes JSON and static routes HTML.
    """

    @pytest.fixture
    def app_with_static(self, tmp_path: Path) -> Litestar:
        """Build an app the way main.py would, but with a tmp static dir."""
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        (static_dir / "index.html").write_text(
            "<!doctype html><html><body><div id='app'></div></body></html>"
        )
        # Match the prod Dockerfile's index->404 clone so the SPA shell is
        # the fallback for unknown paths.
        (static_dir / "404.html").write_text(
            "<!doctype html><html><body><div id='app'></div></body></html>"
        )
        # Stub the Plex/section_index caches so /api/state doesn't try to
        # hit the network in the static-router-contract test.
        return Litestar(route_handlers=build_route_handlers(static_dir))

    def test_root_serves_spa_shell(self, app_with_static: Litestar):
        with TestClient(app=app_with_static) as c:
            r = c.get("/")
            assert r.status_code == 200
            assert "<div id='app'>" in r.text

    def test_api_health_still_returns_text_not_html(self, app_with_static: Litestar):
        """Even with the static catchall registered, /api/health is the
        literal API route — must NOT be poached as a missing static file."""
        with TestClient(app=app_with_static) as c:
            r = c.get("/api/health")
            assert r.status_code == 200
            assert r.text == "OK"
            # Crucial: not the SPA shell.
            assert "<div id='app'>" not in r.text

    def test_api_unknown_route_returns_json_404_not_html(
        self, app_with_static: Litestar
    ):
        """Unmatched /api/* paths must return Litestar's JSON 404, not the
        SPA shell. The frontend's `fetch()` wrapper relies on JSON error
        bodies (api.ts:json()); returning HTML would crash the parse."""
        with TestClient(app=app_with_static) as c:
            r = c.get("/api/this-route-does-not-exist")
            assert r.status_code == 404
            assert r.headers["content-type"].startswith("application/json")
            body = r.json()
            assert body["status_code"] == 404

    def test_unknown_non_api_path_serves_spa_fallback(self, app_with_static: Litestar):
        """Non-/api/ paths fall back to 404.html (cloned from index.html in
        the prod Dockerfile). Status is 404 (Litestar's html_mode keeps the
        404 status); body is the SPA shell so Vue mounts and renders."""
        with TestClient(app=app_with_static) as c:
            r = c.get("/some/client/route/that-does-not-exist")
            # Status stays 404 in html_mode; body is the SPA fallback.
            assert r.status_code == 404
            assert "<div id='app'>" in r.text


class TestConfigStaticDir:
    """Pin the config.STATIC_DIR env-parsing contract."""

    def test_unset_env_means_none(self, monkeypatch):
        monkeypatch.delenv("SYNCLET_STATIC_DIR", raising=False)
        import importlib

        from synclet import config

        importlib.reload(config)
        assert config.STATIC_DIR is None

    def test_set_env_means_path(self, monkeypatch):
        monkeypatch.setenv("SYNCLET_STATIC_DIR", "/some/static/dir")
        import importlib

        from synclet import config

        importlib.reload(config)
        assert Path("/some/static/dir") == config.STATIC_DIR
        # Reset for other tests
        monkeypatch.delenv("SYNCLET_STATIC_DIR", raising=False)
        importlib.reload(config)
