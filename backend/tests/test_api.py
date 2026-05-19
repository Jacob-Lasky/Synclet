"""End-to-end smoke tests via Litestar's TestClient.

Validates the wire contract that the frontend depends on. If a field is
renamed or its shape changes, the contract breaks here loudly instead of
in the user's browser silently.
"""

import pytest
from litestar.testing import TestClient


@pytest.fixture
def client(patch_paths, patch_watchstate, monkeypatch):
    """A TestClient pointed at the real Litestar app, with paths repointed
    at the fixture tree and Plex calls mocked away."""
    # Stub Plex API so tests don't depend on network
    monkeypatch.setattr("synclet.plex._get_xml", lambda *a, **kw: None)
    # Reset Plex section_index cache (lru_cache)
    from synclet.plex import section_index
    section_index.cache_clear()

    from main import app
    with TestClient(app=app) as c:
        yield c


class TestHealthRoute:
    def test_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.text == "OK"


class TestStateRoute:
    def test_returns_titles_disk_libraries(self, client):
        r = client.get("/api/state")
        assert r.status_code == 200
        body = r.json()
        assert "titles" in body
        assert "disk" in body
        assert "libraries" in body, "frontend depends on this — see store.ts:loadState"
        # Library metadata wire contract
        lib_ids = [l["id"] for l in body["libraries"]]
        assert "tv" in lib_ids
        for l in body["libraries"]:
            # Schema fields the frontend consumes
            assert {"id", "label", "short", "kind", "sync_sub"} <= l.keys()

    def test_title_shape(self, client):
        r = client.get("/api/state")
        body = r.json()
        assert body["titles"], "fixture should have produced titles"
        t = body["titles"][0]
        # Wire-contract fields the frontend types.ts depends on
        required = {
            "id", "lib", "folder", "name", "kind", "year",
            "ep_count", "synced_files", "has_synced",
            "watched_count", "watched_pct", "synced_pct",
        }
        assert required <= t.keys(), f"missing: {required - t.keys()}"

    def test_kind_values_are_known(self, client):
        """The frontend's Kind type is "show" | "movie" | "youtube" — any
        other value silently misroutes the UI."""
        r = client.get("/api/state")
        kinds = {t["kind"] for t in r.json()["titles"]}
        assert kinds <= {"show", "movie", "youtube"}, f"unexpected kind: {kinds}"

    def test_library_short_labels_are_2_chars(self, client):
        """libraryShort() in store.ts feeds the card badge — short codes must
        be exactly two characters or the layout breaks."""
        r = client.get("/api/state")
        shorts = [(l["id"], l["short"]) for l in r.json()["libraries"]]
        for lib_id, short in shorts:
            assert len(short) == 2, f"{lib_id} short label is {short!r}, not 2 chars"


class TestTitleRoute:
    def test_show_detail(self, client):
        r = client.get("/api/title/tv/Better%20Call%20Saul%20(2015)%20%7Btvdb-1%7D")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "show"
        assert body["seasons"]

    def test_missing_title_404(self, client):
        r = client.get("/api/title/tv/Does%20Not%20Exist")
        assert r.status_code == 404


class TestSyncRoute:
    def test_no_matches_returns_error_field(self, client):
        # Empty selection — no files match
        r = client.post("/api/sync", json={
            "lib": "tv",
            "folder": "Does Not Exist",
            "selection_type": "all",
        })
        body = r.json()
        assert body["job_id"] is None
        assert "error" in body

    def test_response_shape_includes_media_files(self, client):
        """The toasts ('5 items') depend on total_media_files being in the
        response. If the field disappears, the UI silently says '0 items'."""
        r = client.post("/api/sync", json={
            "lib": "tv",
            "folder": "Better Call Saul (2015) {tvdb-1}",
            "selection_type": "episodes",
            "episodes": [[1, 1]],
        })
        body = r.json()
        # Either error path or success path — both should have predictable shape
        if body.get("job_id"):
            assert "total_media_files" in body
            assert "total_bytes" in body


class TestResolveRoute:
    def test_text_match(self, client):
        r = client.post("/api/resolve-link", json={"url": "better call saul"})
        body = r.json()
        assert body["found"] is True
        assert body["lib"] == "tv"

    def test_empty(self, client):
        r = client.post("/api/resolve-link", json={"url": ""})
        body = r.json()
        assert body["found"] is False
        assert body["reason"] == "empty"

    def test_known_reason_strings(self, client):
        """The frontend (LinkPasteModal:reasonLabel) maps these strings to
        user-facing labels. If a reason string changes here without updating
        the modal, users see raw codes."""
        # Reasons that need to round-trip
        from synclet.resolve import resolve_url
        for url, expected_reason in [
            ("", "empty"),
            ("https://www.imdb.com/title/tt9/", "imdb_url_unsupported"),
            ("http://jellyfin/web/#!/details?id=abc", "jellyfin_url_unsupported"),
        ]:
            r = resolve_url(url)
            assert r["reason"] == expected_reason
