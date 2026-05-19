"""End-to-end smoke tests via Litestar's TestClient.

Validates the wire contract that the frontend depends on. If a field is
renamed or its shape changes, the contract breaks here loudly instead of
in the user's browser silently.
"""

import pytest
from litestar.testing import TestClient


@pytest.fixture
def client(patch_paths, patch_watchstate, tmp_path, monkeypatch):
    """A TestClient pointed at the real Litestar app, with paths repointed
    at the fixture tree and Plex calls mocked away."""
    # Stub Plex API so tests don't depend on network
    monkeypatch.setattr("synclet.plex._get_xml", lambda *a, **kw: None)
    # Reset Plex section_index cache (lru_cache)
    from synclet.plex import episode_rating_keys, section_index

    section_index.cache_clear()
    episode_rating_keys.cache_clear()

    # Point the snapshot at a tmp file so the test does not write into
    # /app/data on the dev machine (which may not exist or may collide with
    # the live stack).
    snap = tmp_path / "snapshot.json"
    monkeypatch.setattr("synclet.config.SNAPSHOT_FILE", snap)
    monkeypatch.setattr("synclet.pending.SNAPSHOT_FILE", snap)

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
            "id",
            "lib",
            "folder",
            "name",
            "kind",
            "year",
            "ep_count",
            "synced_files",
            "has_synced",
            "watched_count",
            "watched_pct",
            "synced_pct",
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
        r = client.post(
            "/api/sync",
            json={
                "lib": "tv",
                "folder": "Does Not Exist",
                "selection_type": "all",
            },
        )
        body = r.json()
        assert body["job_id"] is None
        assert "error" in body

    def test_response_shape_includes_media_files(self, client):
        """The toasts ('5 items') depend on total_media_files being in the
        response. If the field disappears, the UI silently says '0 items'."""
        r = client.post(
            "/api/sync",
            json={
                "lib": "tv",
                "folder": "Better Call Saul (2015) {tvdb-1}",
                "selection_type": "episodes",
                "episodes": [[1, 1]],
            },
        )
        body = r.json()
        # Either error path or success path — both should have predictable shape
        if body.get("job_id"):
            assert "total_media_files" in body
            assert "total_bytes" in body


class TestMaintenancePendingRoute:
    def test_empty_initially(self, client):
        # First call bootstraps the snapshot from current disk state.
        # Pre-seeded After Life is in snapshot AND on disk -> no pending.
        r = client.get("/api/maintenance/pending")
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_deletion_shows_up(self, client, patch_paths):
        from synclet.pending import SnapshotKey, save_snapshot

        save_snapshot(
            {
                SnapshotKey(
                    sync_sub="tv",
                    folder="After Life (2019) {tvdb-2}",
                    season=1,
                    episode=1,
                ),
                SnapshotKey(sync_sub="tv", folder="Ghost", season=1, episode=2),
            }
        )
        r = client.get("/api/maintenance/pending")
        body = r.json()
        # Only the ghost (not the After Life that's still on disk)
        assert len(body["items"]) == 1
        g = body["items"][0]
        # ── Wire contract: MaintenanceView.vue + types.ts:PendingGroup ──────
        # These exact field names are consumed by the frontend. Renaming any
        # of them without a matching frontend change silently breaks the UI;
        # this assertion is the contract test for that seam.
        required_group = {
            "sync_sub", "folder", "title", "kind", "lib", "rating_key", "seasons",
        }
        assert required_group <= g.keys(), f"missing group fields: {required_group - g.keys()}"
        assert g["folder"] == "Ghost"
        assert g["kind"] == "show", "frontend dispatches on kind == 'movie' | 'show' | 'youtube'"

        required_season = {"season", "episodes"}
        s = g["seasons"][0]
        assert required_season <= s.keys(), f"missing season fields: {required_season - s.keys()}"

        required_episode = {
            "season", "episode", "already_watched_in_plex", "episode_rating_key", "title",
        }
        e = s["episodes"][0]
        assert required_episode <= e.keys(), (
            f"missing episode fields: {required_episode - e.keys()}"
        )
        assert e["episode"] == 2

    def test_movie_wire_shape(self, client, patch_paths):
        """A movie group has no `seasons` key; frontend dispatches on `kind`."""
        from synclet.pending import SnapshotKey, save_snapshot

        save_snapshot({SnapshotKey(sync_sub="movies", folder="Ghost Movie")})
        r = client.get("/api/maintenance/pending")
        body = r.json()
        movie = next(g for g in body["items"] if g["folder"] == "Ghost Movie")
        # Movie shape distinct from show shape: no seasons, has already_watched_in_plex
        # at the group level (since a movie is one media item, not a tree).
        assert movie["kind"] == "movie"
        assert "seasons" not in movie
        assert "already_watched_in_plex" in movie


class TestMaintenanceResolveRoute:
    def test_reject_clears_snapshot_entry(self, client, monkeypatch, patch_paths):
        from synclet.pending import SnapshotKey, load_snapshot, save_snapshot

        save_snapshot({SnapshotKey(sync_sub="tv", folder="Ghost", season=1, episode=1)})
        monkeypatch.setattr(
            "synclet.plex.scrobble",
            lambda *a, **kw: pytest.fail("reject must not scrobble"),
        )

        r = client.post(
            "/api/maintenance/resolve",
            json={
                "items": [
                    {
                        "sync_sub": "tv",
                        "folder": "Ghost",
                        "season": 1,
                        "episode": 1,
                    }
                ],
                "action": "reject",
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["results"][0]["status"] == "rejected"
        assert load_snapshot() == set()

    def test_confirm_calls_scrobble_and_returns_per_item_status(
        self, client, monkeypatch, patch_paths
    ):
        """Confirm path: scrobble fires per item, status reflects success/failure."""
        from synclet.pending import SnapshotKey, load_snapshot, save_snapshot

        calls: list[str] = []

        def _scrobble(rating_key, **_kw):
            calls.append(rating_key)
            return True

        monkeypatch.setattr("synclet.plex.scrobble", _scrobble)
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"ratingKey": "777"} if folder == "Movie X" else None,
        )
        (patch_paths["media"] / "movies" / "Movie X").mkdir(parents=True)

        save_snapshot({SnapshotKey(sync_sub="movies", folder="Movie X")})

        r = client.post(
            "/api/maintenance/resolve",
            json={
                "items": [{"sync_sub": "movies", "folder": "Movie X"}],
                "action": "confirm",
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["results"][0]["status"] == "ok"
        assert calls == ["777"]
        assert load_snapshot() == set()

    def test_unknown_action_returns_error(self, client):
        r = client.post(
            "/api/maintenance/resolve",
            json={"items": [], "action": "explode"},
        )
        body = r.json()
        assert "error" in body
        assert body["results"] == []


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
