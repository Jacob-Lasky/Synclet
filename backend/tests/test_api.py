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
        assert "libraries" in body, "frontend depends on this , see store.ts:loadState"
        # Library metadata wire contract
        lib_ids = [lib["id"] for lib in body["libraries"]]
        assert "tv" in lib_ids
        for lib in body["libraries"]:
            # Schema fields the frontend consumes
            assert {"id", "label", "short", "kind", "sync_sub"} <= lib.keys()

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
        """The frontend's Kind type is "show" | "movie" | "youtube" , any
        other value silently misroutes the UI."""
        r = client.get("/api/state")
        kinds = {t["kind"] for t in r.json()["titles"]}
        assert kinds <= {"show", "movie", "youtube"}, f"unexpected kind: {kinds}"

    def test_library_short_labels_are_2_chars(self, client):
        """libraryShort() in store.ts feeds the card badge , short codes must
        be exactly two characters or the layout breaks."""
        r = client.get("/api/state")
        shorts = [(lib["id"], lib["short"]) for lib in r.json()["libraries"]]
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
        # Empty selection , no files match
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
        # Either error path or success path , both should have predictable shape
        if body.get("job_id"):
            assert "total_media_files" in body
            assert "total_bytes" in body


class TestMaintenanceIgnoreRoutes:
    def test_ignore_then_list_then_unignore(self, client):
        # ignore a pending entry via the API
        r = client.post(
            "/api/maintenance/ignore",
            json={
                "kind": "pending",
                "ref": {"sync_sub": "tv", "folder": "X", "season": 1, "episode": 1},
            },
        )
        assert r.status_code == 201
        assert r.json()["ok"] is True

        # list shows the entry
        r = client.get("/api/maintenance/ignored")
        body = r.json()
        assert len(body["pending"]) == 1
        assert body["pending"][0]["folder"] == "X"

        # unignore clears it
        r = client.post(
            "/api/maintenance/unignore",
            json={
                "kind": "pending",
                "ref": {"sync_sub": "tv", "folder": "X", "season": 1, "episode": 1},
            },
        )
        assert r.json()["ok"] is True
        r = client.get("/api/maintenance/ignored")
        assert r.json()["pending"] == []

    def test_unknown_kind_returns_ok_false(self, client):
        r = client.post(
            "/api/maintenance/ignore",
            json={"kind": "bogus", "ref": {}},
        )
        assert r.json()["ok"] is False

    def test_counts_excludes_ignored_pending(self, client, patch_paths):
        from synclet.pending import SnapshotKey, save_snapshot

        # Seed a pending ghost so counts.total >= 1
        save_snapshot({SnapshotKey(sync_sub="tv", folder="Ghost", season=1, episode=1)})

        before = client.get("/api/maintenance/counts").json()
        assert before["pending_items"] >= 1
        baseline = before["pending_items"]

        # Ignore it
        client.post(
            "/api/maintenance/ignore",
            json={
                "kind": "pending",
                "ref": {"sync_sub": "tv", "folder": "Ghost", "season": 1, "episode": 1},
            },
        )

        after = client.get("/api/maintenance/counts").json()
        assert after["pending_items"] == baseline - 1


class TestMaintenanceCountsRoute:
    def test_returns_all_keys(self, client):
        r = client.get("/api/maintenance/counts")
        assert r.status_code == 200
        body = r.json()
        # Wire contract: TabBar consumes `total`; per-category counts let the
        # UI deep-link without a second round-trip.
        required = {"watched_titles", "hanging_files", "pending_items", "total"}
        assert required <= body.keys(), f"missing: {required - body.keys()}"

    def test_total_equals_sum_of_categories(self, client, patch_paths):
        from synclet.pending import SnapshotKey, save_snapshot

        # Seed one ghost pending so total is non-zero.
        save_snapshot({SnapshotKey(sync_sub="tv", folder="Ghost", season=1, episode=1)})
        r = client.get("/api/maintenance/counts")
        body = r.json()
        assert body["total"] == (
            body["watched_titles"] + body["hanging_files"] + body["pending_items"]
        )
        # The ghost we seeded shows in pending_items.
        assert body["pending_items"] >= 1


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
            "sync_sub",
            "folder",
            "title",
            "kind",
            "lib",
            "rating_key",
            "seasons",
        }
        assert required_group <= g.keys(), (
            f"missing group fields: {required_group - g.keys()}"
        )
        assert g["folder"] == "Ghost"
        assert g["kind"] == "show", (
            "frontend dispatches on kind == 'movie' | 'show' | 'youtube'"
        )

        required_season = {"season", "episodes"}
        s = g["seasons"][0]
        assert required_season <= s.keys(), (
            f"missing season fields: {required_season - s.keys()}"
        )

        required_episode = {
            "season",
            "episode",
            "already_watched_in_plex",
            "episode_rating_key",
            "title",
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
        # Cleanup summary is always present so the frontend toast can show it.
        assert "cleanup" in body
        assert body["cleanup"].keys() == {"removed_files", "removed_dirs"}
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


class TestScrobbleRoute:
    def test_movie_scrobble(self, client, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"ratingKey": "M-9"} if folder == "M" else None,
        )
        monkeypatch.setattr(
            "synclet.plex.scrobble",
            lambda rk, **_: called.append(rk) or True,
        )
        r = client.post(
            "/api/scrobble",
            json={"lib": "movies", "folder": "M", "scope": "movie"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["scrobbled"] == 1
        assert body["failed"] == 0
        assert called == ["M-9"]

    def test_episode_scrobble(self, client, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"ratingKey": "S-1"},
        )
        monkeypatch.setattr(
            "synclet.plex.episode_rating_keys",
            lambda show_rk: {(1, 1): "E-1-1"},
        )
        monkeypatch.setattr(
            "synclet.plex.scrobble",
            lambda rk, **_: called.append(rk) or True,
        )
        r = client.post(
            "/api/scrobble",
            json={
                "lib": "tv",
                "folder": "Show",
                "scope": "episode",
                "season": 1,
                "episode": 1,
            },
        )
        body = r.json()
        assert body["scrobbled"] == 1
        assert called == ["E-1-1"]

    def test_unknown_scope_returns_error(self, client):
        r = client.post(
            "/api/scrobble",
            json={"lib": "movies", "folder": "X", "scope": "bogus"},
        )
        body = r.json()
        assert "error" in body
        assert body["scrobbled"] == 0

    def test_watched_false_routes_to_unscrobble(self, client, monkeypatch):
        """watched=False over the wire must reach plex.unscrobble, not scrobble."""
        unscrobbled: list[str] = []
        monkeypatch.setattr(
            "synclet.plex.find_in_library",
            lambda lib, folder: {"ratingKey": "M-9"},
        )
        monkeypatch.setattr(
            "synclet.plex.scrobble",
            lambda *a, **kw: pytest.fail("scrobble must not run when watched=False"),
        )
        monkeypatch.setattr(
            "synclet.plex.unscrobble",
            lambda rk, **_: unscrobbled.append(rk) or True,
        )
        r = client.post(
            "/api/scrobble",
            json={
                "lib": "movies",
                "folder": "M",
                "scope": "movie",
                "watched": False,
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["scrobbled"] == 1
        assert unscrobbled == ["M-9"]


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


class TestTitleRouteMovie:
    """api_title has a separate movie path (top-level watched, no episodes)."""

    def test_movie_includes_watched_flag(self, client):
        r = client.get("/api/title/movies/1917%20(2019)%20%7Btmdb-3%7D")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "movie"
        # 1917 was set up as unwatched in the watchstate fixture
        assert "watched" in body
        assert body["watched"] is False


class TestPosterRoutes:
    """api_thumb / api_art both proxy bytes from Plex, falling back to a
    404-with-empty-body when meta or bytes are unavailable."""

    def test_thumb_returns_404_when_meta_missing(self, client):
        # _get_xml is stubbed to None in the client fixture, so find_in_library
        # returns None -> fetch_thumb_bytes returns None -> route hands back
        # an empty 404 with image/jpeg.
        r = client.get("/api/plex/thumb/tv/Nothing")
        assert r.status_code == 404
        assert r.content == b""
        assert r.headers["content-type"].startswith("image/jpeg")

    def test_art_returns_404_when_meta_missing(self, client):
        r = client.get("/api/plex/art/tv/Nothing")
        assert r.status_code == 404
        assert r.content == b""

    def test_thumb_returns_bytes_when_fetch_succeeds(self, client, monkeypatch):
        # main.py imports fetch_thumb_bytes by name, so we patch the symbol
        # in main's namespace, not the source module.
        monkeypatch.setattr(
            "main.fetch_thumb_bytes",
            lambda lib, folder: (b"PNGBYTES", "image/png"),
        )
        r = client.get("/api/plex/thumb/tv/Anything")
        assert r.status_code == 200
        assert r.content == b"PNGBYTES"
        assert r.headers["content-type"] == "image/png"
        # Caching contract for the frontend
        assert "max-age=86400" in r.headers["cache-control"]

    def test_art_returns_bytes_when_fetch_succeeds(self, client, monkeypatch):
        monkeypatch.setattr(
            "main.fetch_art_bytes",
            lambda lib, folder: (b"JPGBYTES", "image/jpeg"),
        )
        r = client.get("/api/plex/art/tv/Anything")
        assert r.status_code == 200
        assert r.content == b"JPGBYTES"


class TestUnsyncRoute:
    def test_no_matches_returns_error(self, client):
        r = client.post(
            "/api/unsync",
            json={
                "lib": "tv",
                "folder": "Does Not Exist",
                "selection_type": "all",
            },
        )
        body = r.json()
        assert body["job_id"] is None
        assert body["error"] == "no files matched"


class TestJobsRoutes:
    def test_unknown_job_returns_404(self, client):
        r = client.get("/api/jobs/nonexistent")
        assert r.status_code == 404

    def test_list_jobs_returns_array(self, client):
        r = client.get("/api/jobs")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["jobs"], list)

    def test_known_job_returns_state(self, client, monkeypatch):
        # Inject a fake job into the module store so the GET succeeds.
        from synclet import sync_ops
        from synclet.sync_ops import Job

        job = Job(id="abc123", op="sync", status="done", total_files=2)
        monkeypatch.setitem(sync_ops._JOBS, "abc123", job)
        r = client.get("/api/jobs/abc123")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "abc123"
        assert body["status"] == "done"


class TestSyncedRoute:
    def test_returns_synced_titles_with_new_unwatched(self, client):
        # The conftest fixture pre-syncs After Life S01E01; the watchstate
        # fixture marks that episode watched (so no new_unwatched for it).
        r = client.get("/api/synced")
        assert r.status_code == 200
        items = r.json()["items"]
        # At least one synced title from the fixture
        assert items
        folders = [it["folder"] for it in items]
        assert any("After Life" in f for f in folders)
        # Wire-contract fields the frontend depends on
        for it in items:
            assert {
                "title",
                "folder",
                "lib",
                "kind",
                "size_bytes",
                "new_unwatched",
            } <= it.keys()


class TestWatchlistRoute:
    def test_forwards_watchlist_payload(self, client, monkeypatch):
        # get_watchlist hits Plex's RSS, so we stub it. The route's job is
        # forward-payload-as-items: Watchlist.vue reads items[].title /
        # .matched / .lib, so the test asserts on that exact shape.
        payload = [
            {
                "title": "Better Call Saul",
                "matched": True,
                "lib": "tv",
                "folder": "Better Call Saul (2015) {tvdb-1}",
                "watched_pct": 50,
                "synced_pct": 80,
            },
            {
                "title": "Unmatched Future Title",
                "matched": False,
                "category": "movie",
                "guid": "plex://x",
            },
        ]
        monkeypatch.setattr("main.get_watchlist", lambda: payload)
        r = client.get("/api/watchlist")
        assert r.status_code == 200
        assert r.json() == {"items": payload}

    def test_empty_watchlist_returns_empty_items(self, client, monkeypatch):
        monkeypatch.setattr("main.get_watchlist", list)
        r = client.get("/api/watchlist")
        assert r.status_code == 200
        assert r.json() == {"items": []}


class TestMaintenanceWatchedAndHanging:
    def test_watched_returns_items_array(self, client):
        r = client.get("/api/maintenance/watched")
        assert r.status_code == 200
        assert isinstance(r.json()["items"], list)

    def test_hanging_returns_items_array(self, client):
        r = client.get("/api/maintenance/hanging")
        assert r.status_code == 200
        assert isinstance(r.json()["items"], list)


class TestRefreshRoute:
    def test_invalidates_and_returns_ok(self, client):
        r = client.post("/api/refresh", json={})
        assert r.status_code == 201
        assert r.json() == {"ok": True}

    def test_clears_maint_cache_not_just_state_cache(self, client):
        """Regression: /api/refresh used to only call state.invalidate(),
        leaving the maint_cache (which backs the Maintenance tab AND now
        /api/synced + /api/watchlist) holding pre-refresh data for up to
        STATE_CACHE_TTL seconds. The fix calls maint_cache.invalidate()
        alongside state.invalidate()."""
        from synclet import maint_cache

        # Prime a cache entry via the public API so we know the cache has
        # at least one resident value going in.
        maint_cache.get_cached("sentinel", lambda: {"primed": True})
        assert "sentinel" in maint_cache._cache
        r = client.post("/api/refresh", json={})
        assert r.status_code == 201

        # The post-refresh cache must be empty: state.invalidate() alone
        # would NOT have cleared this; maint_cache.invalidate() does.
        assert maint_cache._cache == {}


class TestSyncthingOverviewRoute:
    def test_unconfigured_returns_empty(self, client, monkeypatch):
        from synclet import syncthing

        monkeypatch.setattr(syncthing, "is_configured", lambda: False)
        r = client.get("/api/syncthing/overview")
        assert r.status_code == 200
        body = r.json()
        assert body == {"configured": False, "folders": []}

    def test_configured_returns_folders(self, client, monkeypatch):
        from synclet import syncthing

        monkeypatch.setattr(syncthing, "is_configured", lambda: True)

        async def _fake_overview():
            return [{"folder_id": "default", "percent": 100.0, "devices": []}]

        monkeypatch.setattr(syncthing, "overview", _fake_overview)
        r = client.get("/api/syncthing/overview")
        assert r.status_code == 200
        body = r.json()
        assert body["configured"] is True
        assert body["folders"][0]["folder_id"] == "default"


class TestCoverageRoute:
    """The frontend uses /api/coverage to surface 'WatchState not tracking
    section N' banners for libraries that fall through to Plex-direct reads.
    A zero count is the signal."""

    def test_returns_per_library_counts(self, client):
        r = client.get("/api/coverage")
        assert r.status_code == 200
        body = r.json()
        assert "libraries" in body
        # One entry per LIBRARIES config key. Each entry surfaces enough for
        # the frontend to render its 'not tracked' banner without re-deriving
        # config.
        by_id = {entry["id"]: entry for entry in body["libraries"]}
        assert "YouTube" in by_id
        yt = by_id["YouTube"]
        assert yt["section"] == 6
        assert yt["kind"] == "youtube"
        assert yt["label"] == "YouTube"
        # The two-number contract the frontend uses to decide when to render
        # the banner: zero observed and zero expected is a Plex-unreachable
        # test stub, but the keys must always be present.
        assert "watchstate_rows" in yt
        assert "expected_rows" in yt
        assert by_id["tv"]["watchstate_rows"] >= 0
        assert yt["watchstate_rows"] == 0


class TestShortLabelHelper:
    """Direct tests for the _short_label helper (lines 141-144 are only
    reached for single-word and zero-word labels, which the live fixture
    doesn't include)."""

    def test_single_word_label_uses_two_char_prefix(self):
        from main import _short_label

        assert _short_label("anime", "Anime") == "AN"

    def test_empty_label_returns_placeholder(self):
        from main import _short_label

        # All whitespace gets filtered to an empty word list -> "??"
        assert _short_label("blank", "   ") == "??"


class TestWarmPlexCaches:
    """The on_startup hook that primes section_index for every Plex-backed
    library in parallel. Critical that:
      - every library's section is touched (else cold first-paint stays slow),
      - a thrown section_index doesn't abort startup (else a broken Plex auth
        token bricks the whole app),
      - the calls actually overlap (else this just shifts cost from request-
        time to startup-time without buying anything).
    """

    @pytest.fixture(autouse=True)
    def _clear_caches(self):
        from synclet.plex import section_index

        section_index.cache_clear()
        yield
        section_index.cache_clear()

    @pytest.mark.asyncio
    async def test_calls_section_index_for_every_library(self, monkeypatch):
        from synclet.config import LIBRARIES

        called: list[int] = []

        def _track(sec):
            called.append(sec)
            return {}

        monkeypatch.setattr("synclet.plex.section_index", _track)
        # The hook imports section_index from synclet.plex inside main.py at
        # module load, so the monkeypatch on synclet.plex.section_index has
        # to be mirrored on the main-module ref too.
        monkeypatch.setattr("main.section_index", _track)
        # The derived-cache prefetches (get_synced / get_watchlist) also call
        # section_index transitively via watchstate aggregates. Stub them so
        # this test is scoped to the section-warming portion of the hook;
        # the derived prefetch has its own dedicated test.
        monkeypatch.setattr("main.get_synced", list)
        monkeypatch.setattr("main.get_watchlist", list)

        from main import warm_plex_caches

        await warm_plex_caches()

        # Every Plex-backed library got a section_index call exactly once.
        expected = sorted(info["plex_section"] for info in LIBRARIES.values())
        assert sorted(called) == expected

    @pytest.mark.asyncio
    async def test_one_failing_section_does_not_abort_startup(self, monkeypatch):
        """return_exceptions=True is the contract: a section that raises is
        logged but does NOT propagate, so app boot is decoupled from Plex
        availability."""
        from synclet.config import LIBRARIES

        survivors: list[int] = []

        def _flaky(sec):
            if sec == next(iter(LIBRARIES.values()))["plex_section"]:
                raise RuntimeError("plex auth blew up")
            survivors.append(sec)
            return {}

        monkeypatch.setattr("synclet.plex.section_index", _flaky)
        monkeypatch.setattr("main.section_index", _flaky)
        # Scope this test to section_index error isolation; stub derived
        # prefetches.
        monkeypatch.setattr("main.get_synced", list)
        monkeypatch.setattr("main.get_watchlist", list)

        from main import warm_plex_caches

        # Should NOT raise.
        await warm_plex_caches()
        # The other sections still ran.
        assert len(survivors) == len(LIBRARIES) - 1

    @pytest.mark.asyncio
    async def test_runs_concurrently(self, monkeypatch):
        """asyncio.gather + asyncio.to_thread is the load-bearing concurrency.
        Slow synchronous section_index calls overlap in the default executor
        so total wall-time is roughly one round-trip, not N of them."""
        import asyncio
        import time

        from synclet.config import LIBRARIES

        sleep_s = 0.15

        def _slow(_sec):
            time.sleep(sleep_s)
            return {}

        monkeypatch.setattr("synclet.plex.section_index", _slow)
        monkeypatch.setattr("main.section_index", _slow)
        # Concurrency assertion is about the section-index gather; stub the
        # derived prefetch so it doesn't add unrelated wall-time.
        monkeypatch.setattr("main.get_synced", list)
        monkeypatch.setattr("main.get_watchlist", list)

        from main import warm_plex_caches

        start = asyncio.get_running_loop().time()
        await warm_plex_caches()
        elapsed = asyncio.get_running_loop().time() - start

        # N=len(LIBRARIES) sections serial would be N*sleep_s. Parallel: ~sleep_s.
        n = len(LIBRARIES)
        ceiling = 2.5 * sleep_s
        assert elapsed < ceiling, (
            f"warm_plex_caches took {elapsed:.3f}s for {n} sections "
            f"(serial would be {n * sleep_s:.3f}s, parallel ceiling {ceiling:.3f}s)"
        )

    @pytest.mark.asyncio
    async def test_also_primes_synced_and_watchlist(self, monkeypatch):
        """After section_index warms, the hook prefetches get_synced and
        get_watchlist so the first user click on either tab hits a warm
        maint_cache instead of paying the full build cost."""
        # Stub section_index away so we focus on the derived prefetch.
        monkeypatch.setattr("synclet.plex.section_index", lambda _sec: {})
        monkeypatch.setattr("main.section_index", lambda _sec: {})

        synced_calls: list[int] = []
        watchlist_calls: list[int] = []

        def _fake_synced():
            synced_calls.append(1)
            return [{"title": "fake"}]

        def _fake_watchlist():
            watchlist_calls.append(1)
            return [{"title": "fake"}]

        monkeypatch.setattr("main.get_synced", _fake_synced)
        monkeypatch.setattr("main.get_watchlist", _fake_watchlist)

        from main import warm_plex_caches

        await warm_plex_caches()
        assert len(synced_calls) == 1
        assert len(watchlist_calls) == 1


class TestSyncedAndWatchlistCacheBehavior:
    """The /api/synced + /api/watchlist routes are now served by maint_cache.
    These tests pin the cache contract end-to-end so a future refactor that
    removes the caching surfaces here, not as a 12-second tab click in
    production.

    No autouse cache-bust fixture: the `client` fixture's lifespan fires
    warm_plex_caches which itself populates the cache via the REAL _build.
    We invalidate after that runs and then monkeypatch _build so the
    counting wrapper sees the next request.
    """

    def test_synced_second_request_does_not_rebuild(self, client, monkeypatch):
        from synclet import maint_cache
        from synclet import synced as synced_mod

        build_count = {"n": 0}
        original_build = synced_mod._build

        def _counting_build():
            build_count["n"] += 1
            return original_build()

        # Clear the warm-hook's cached entry, THEN patch _build, so the next
        # request rebuilds via the counting wrapper.
        maint_cache.invalidate()
        monkeypatch.setattr(synced_mod, "_build", _counting_build)

        r1 = client.get("/api/synced")
        r2 = client.get("/api/synced")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json() == r2.json()
        # Cache hit on second request: build ran exactly once.
        assert build_count["n"] == 1

    def test_synced_force_rebuilds_via_invalidate(self, client, monkeypatch):
        """Mutation seams use maint_cache.invalidate() to force a rebuild;
        confirming the contract works from the consumer side."""
        from synclet import maint_cache
        from synclet import synced as synced_mod

        build_count = {"n": 0}
        original_build = synced_mod._build

        def _counting_build():
            build_count["n"] += 1
            return original_build()

        maint_cache.invalidate()
        monkeypatch.setattr(synced_mod, "_build", _counting_build)

        client.get("/api/synced")
        maint_cache.invalidate()
        client.get("/api/synced")
        assert build_count["n"] == 2

    def test_watchlist_second_request_does_not_rebuild(self, client, monkeypatch):
        from synclet import maint_cache
        from synclet import watchlist as watchlist_mod

        build_count = {"n": 0}
        original_build = watchlist_mod._build

        def _counting_build():
            build_count["n"] += 1
            return original_build()

        maint_cache.invalidate()
        monkeypatch.setattr(watchlist_mod, "_build", _counting_build)
        # Stub the RSS fetch so the build does not hit Plex.tv in tests.
        monkeypatch.setattr(
            watchlist_mod, "fetch_rss", lambda: [{"_error": "no network in tests"}]
        )

        r1 = client.get("/api/watchlist")
        r2 = client.get("/api/watchlist")
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Cache hit: build ran exactly once across both reads.
        assert build_count["n"] == 1


class TestEventLoopNotBlockedByHandlers:
    """Regression for the production hang: a blocking handler froze uvicorn's
    single event loop, so one slow/stuck call (e.g. a stalled FUSE walk that no
    socket timeout can interrupt) made every request — including /api/health —
    hang, flipping the container unhealthy and presenting as "isn't loading".

    The fix offloads blocking handler work via asyncio.to_thread. This test
    pins that contract against a REAL uvicorn server on a socket (an in-process
    AsyncTestClient cannot detect loop-blocking — issuing the concurrent probe
    also needs the very loop that's blocked). While /api/synced is stuck in its
    blocking body, /api/health must still answer promptly over a second
    connection. If a future refactor drops the to_thread (running the blocking
    call directly on the loop), the health probe blocks behind the stuck
    handler until the 30s release and the assertion below fails.
    """

    def test_health_responds_while_synced_handler_is_stuck(
        self, patch_paths, patch_watchstate, monkeypatch
    ):
        import socket
        import threading
        import time
        from concurrent.futures import ThreadPoolExecutor

        import httpx
        import uvicorn

        monkeypatch.setattr("synclet.plex._get_xml", lambda *a, **kw: None)

        entered = threading.Event()
        release = threading.Event()

        def _stuck_synced():
            # Simulate a stalled shfs walk: signal we're running, then block.
            entered.set()
            release.wait(timeout=30)
            return []

        # Free port for the throwaway server.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        from main import app

        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            # Wait until the server (and its on_startup warm, which uses the
            # REAL get_synced) is accepting requests.
            for _ in range(100):
                try:
                    if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.1)
            else:
                raise AssertionError("server did not come up")

            # NOW swap in the stuck builder. Handlers resolve main.get_synced at
            # call time, so this affects the next /api/synced request only.
            monkeypatch.setattr("main.get_synced", _stuck_synced)

            with ThreadPoolExecutor(max_workers=1) as ex:
                synced_fut = ex.submit(
                    lambda: httpx.get(f"{base}/api/synced", timeout=30)
                )
                assert entered.wait(5), "stuck /api/synced handler never ran"

                # The smoking gun: with /api/synced stuck, health must still
                # answer over a second connection within a couple seconds. If
                # the loop were blocked, this would hang until the 30s release
                # and raise ReadTimeout.
                t0 = time.monotonic()
                health = httpx.get(f"{base}/api/health", timeout=5)
                elapsed = time.monotonic() - t0
                assert health.status_code == 200
                assert health.text == "OK"
                assert elapsed < 3, (
                    f"health blocked behind stuck handler ({elapsed:.1f}s)"
                )

                release.set()
                synced = synced_fut.result(timeout=10)
                assert synced.status_code == 200
                assert synced.json() == {"items": []}
        finally:
            release.set()
            server.should_exit = True
            server_thread.join(timeout=10)
