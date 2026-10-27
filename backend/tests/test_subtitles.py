"""Subtitle inventory is evidence, not a promise that a film needs captions."""

import asyncio

import pytest

from synclet import sync_ops


@pytest.mark.asyncio
async def test_existing_wrong_subtitle_is_not_reported_complete(tmp_path):
    src, dst = tmp_path / "movie.en.forced.srt", tmp_path / "offline.srt"
    src.write_bytes(b"expected captions")
    dst.write_bytes(b"wrong--- captions")  # same size, different content
    job = sync_ops.start_sync(sync_ops.sync_items([(src, dst)]))
    await asyncio.gather(*sync_ops._BACKGROUND_TASKS)
    assert job.status == "error"
    assert dst.read_bytes() == b"wrong--- captions"  # preserve existing copies


@pytest.mark.asyncio
async def test_copy_that_drops_subtitle_cannot_finish_successfully(
    tmp_path, monkeypatch
):
    src, dst = tmp_path / "movie.en.forced.srt", tmp_path / "offline.srt"
    src.write_bytes(b"captions")
    monkeypatch.setattr(sync_ops, "copy_into_place", lambda *args: None)
    job = sync_ops.start_sync(sync_ops.sync_items([(src, dst)]))
    await asyncio.gather(*sync_ops._BACKGROUND_TASKS)
    assert job.status == "error"


def test_selected_missing_subtitle_is_not_silently_dropped(tmp_path):
    with pytest.raises(OSError, match=r"subtitle|Subtitle"):
        sync_ops.sync_items([(tmp_path / "movie.en.forced.srt", tmp_path / "out.srt")])


@pytest.mark.asyncio
async def test_selected_subtitle_vanishes_while_waiting_for_lock(tmp_path):
    src, dst = tmp_path / "movie.en.forced.srt", tmp_path / "out.srt"
    src.write_bytes(b"captions")
    items = sync_ops.sync_items([(src, dst)])
    src.unlink()
    job = sync_ops.start_sync(items, resolve=list)
    await asyncio.gather(*sync_ops._BACKGROUND_TASKS)
    assert job.status == "error"
    assert "disappeared" in job.error


@pytest.mark.asyncio
async def test_existing_valid_subtitle_is_verified_without_rewriting(tmp_path):
    src, dst = tmp_path / "movie.en.srt", tmp_path / "out.srt"
    src.write_bytes(b"captions")
    dst.write_bytes(b"captions")
    before = dst.stat().st_mtime_ns
    job = sync_ops.start_sync(sync_ops.sync_items([(src, dst)]))
    await asyncio.gather(*sync_ops._BACKGROUND_TASKS)
    assert job.status == "done"
    assert job.verified_subtitles == 1
    assert dst.stat().st_mtime_ns == before


def _movie(patch_paths):
    from synclet.scan import scan_title_detail

    detail = scan_title_detail("movies", "1917 (2019) {tmdb-3}")
    assert detail is not None
    return detail


def test_unknown_need_is_not_required_missing(patch_paths, monkeypatch):
    from synclet import subtitles

    monkeypatch.setattr(subtitles, "reported_tracks", lambda *args: {})
    result = subtitles.assess(_movie(patch_paths))
    assert result["videos"][0]["status"] == "tracks_unknown"
    assert result["warnings"] == []
    assert "burned-in" in result["note"]


def test_known_requirement_and_missing_reported_external_are_distinct(
    patch_paths, monkeypatch
):
    from synclet import subtitles

    detail = _movie(patch_paths)
    video = next(f["path"] for f in detail.files if f["is_video"])
    monkeypatch.setattr(subtitles, "reported_tracks", lambda *args: {video: []})
    assert subtitles.assess(detail)["videos"][0]["status"] == "no_forced_track_reported"
    explicit = subtitles.assess(detail, require_forced=True)
    assert explicit["videos"][0]["status"] == "required_forced_missing"
    assert explicit["videos"][0]["requirement"] == "caller"
    monkeypatch.setattr(
        subtitles,
        "reported_tracks",
        lambda *args: {
            video: [{"forced": True, "external": True, "language": "eng"}],
        },
    )
    external = subtitles.assess(detail)
    assert external["videos"][0]["status"] == "required_forced_missing"
    assert external["videos"][0]["requirement"] == "plex_external"


def test_embedded_is_reported_and_foreign_does_not_satisfy_english(
    patch_paths, monkeypatch
):
    from synclet import subtitles

    detail = _movie(patch_paths)
    video = next(f["path"] for f in detail.files if f["is_video"])
    track = {"forced": True, "external": False, "language": "eng"}
    monkeypatch.setattr(subtitles, "reported_tracks", lambda *args: {video: [track]})
    assert (
        subtitles.assess(detail, require_forced=True)["videos"][0]["status"]
        == "embedded_forced_reported"
    )
    track["language"] = "spa"
    assert (
        subtitles.assess(detail, require_forced=True)["videos"][0]["status"]
        == "required_forced_missing"
    )


def test_forced_sidecar_checks_actual_existence_and_offline_content(
    patch_paths, monkeypatch
):

    from synclet import subtitles
    from synclet.scan import scan_title_detail

    monkeypatch.setattr(subtitles, "reported_tracks", lambda *args: {})
    path = (
        patch_paths["media"] / "movies" / "1917 (2019) {tmdb-3}" / "1917.en.forced.srt"
    )
    path.write_bytes(b"forced captions")
    detail = scan_title_detail("movies", "1917 (2019) {tmdb-3}")
    assert detail is not None
    assert (
        subtitles.assess(detail, require_forced=True)["videos"][0]["status"]
        == "forced_sidecar_available"
    )
    path.unlink()  # a stale listing is not proof a subtitle exists
    check = subtitles.assess(detail, require_forced=True)
    assert check["videos"][0]["status"] == "required_forced_missing"
    sidecar = next(
        s for s in check["videos"][0]["sidecars"] if s["name"].endswith("forced.srt")
    )
    assert sidecar["source_exists"] is False
    assert sidecar["offline"] == "not_verified"


def test_metadata_is_scoped_to_exact_media_part(monkeypatch):
    import xml.etree.ElementTree as ET  # noqa: S405 (literal test fixture)

    from synclet import subtitles

    monkeypatch.setattr(subtitles.plex, "cached_rating_key", lambda *args: "42")
    calls = []

    def get_xml(path):
        calls.append(path)
        return ET.fromstring(  # noqa: S314 (literal test fixture)
            '<MediaContainer><Video><Media><Part file="/movie.mkv"><Stream streamType="3" languageCode="eng" forced="1" index="3"/><Stream streamType="3" languageCode="eng" forced="1" key="/library/streams/1"/><Stream streamType="2"/></Part></Media></Video></MediaContainer>'
        )

    monkeypatch.setattr(subtitles.plex, "_get_xml", get_xml)
    result = subtitles.reported_tracks("movies", "movie")
    assert calls == ["/library/metadata/42"]
    assert result["/movie.mkv"] == [
        {"language": "eng", "forced": True, "external": False},
        {"language": "eng", "forced": True, "external": True},
    ]


def test_required_forced_sync_returns_conflict_before_job(client):
    response = client.post(
        "/api/sync",
        json={
            "lib": "movies",
            "folder": "1917 (2019) {tmdb-3}",
            "selection_type": "movie",
            "require_forced": True,
        },
    )
    assert response.status_code == 409
    assert not sync_ops._JOBS


def test_subtitle_only_request_never_copies_a_video(client, patch_paths):
    import time

    response = client.post(
        "/api/sync",
        json={
            "lib": "movies",
            "folder": "1917 (2019) {tmdb-3}",
            "selection_type": "movie",
            "subtitles_only": True,
        },
    )
    assert response.status_code == 201
    job = sync_ops.get_job(response.json()["job_id"])
    assert job is not None
    for _ in range(100):
        if job.status in ("done", "error"):
            break
        time.sleep(0.01)
    assert job.status == "done", job.error
    assert job.total_media_files == 0
    assert job.verified_subtitles == 1
    dest = patch_paths["sync"] / "movies" / "1917 (2019) {tmdb-3}"
    assert not list(dest.glob("*.mkv"))
    assert (dest / "1917.en.srt").read_bytes() == b"en"


@pytest.mark.asyncio
async def test_preflight_rechecks_requirement_inside_lock(tmp_path):
    src, dst = tmp_path / "movie.en.forced.srt", tmp_path / "out.srt"
    src.write_bytes(b"captions")

    def preflight():
        raise OSError("English forced subtitle source missing")

    job = sync_ops.start_sync(sync_ops.sync_items([(src, dst)]), preflight=preflight)
    await asyncio.gather(*sync_ops._BACKGROUND_TASKS)
    assert job.status == "error"
    assert not dst.exists()


def test_source_vanish_during_selection_is_reported_to_caller(client, monkeypatch):
    def missing(_pairs):
        raise OSError("Selected subtitle source missing: movie.en.forced.srt")

    monkeypatch.setattr(sync_ops, "sync_items", missing)
    response = client.post(
        "/api/sync",
        json={
            "lib": "movies",
            "folder": "1917 (2019) {tmdb-3}",
            "selection_type": "movie",
        },
    )
    assert response.status_code == 409
    assert "Source files or required subtitles" in response.json()["detail"]


def test_title_preflight_query_distinguishes_explicit_need(client):
    from urllib.parse import quote

    url = "/api/title/movies/" + quote("1917 (2019) {tmdb-3}")
    regular = client.get(url).json()["subtitle_check"]
    required = client.get(url + "?require_forced=true").json()["subtitle_check"]
    assert regular["warnings"] == []
    assert required["videos"][0]["status"] == "required_forced_missing"


def test_cold_subtitle_assessment_never_fetches_a_plex_section(monkeypatch):
    from synclet import plex, subtitles

    plex.section_index.cache_clear()
    plex._section_id_index.cache_clear()
    monkeypatch.setattr(plex, "_KNOWN_TITLE_KEYS", {}, raising=False)
    monkeypatch.setattr(plex, "_load_disk_cache", lambda **kwargs: None)
    calls = []

    def get_xml(path, **kwargs):
        calls.append(path)
        return None

    monkeypatch.setattr(plex, "_get_xml", get_xml)
    assert subtitles.reported_tracks("movies", "Cold Movie (2023) {tmdb-882569}") == {}
    assert calls == [], "Cold subtitle checks must not warm the Plex section cache"


def test_initial_scan_failure_is_a_safe_conflict(client, monkeypatch):
    from litestar.testing import TestClient

    def vanished(*args, **kwargs):
        raise OSError("private diagnostic must not reach user")

    monkeypatch.setattr(sync_ops, "resolve_selection", vanished)
    with TestClient(app=client.app, raise_server_exceptions=False) as request:
        response = request.post(
            "/api/sync",
            json={
                "lib": "movies",
                "folder": "1917 (2019) {tmdb-3}",
                "selection_type": "movie",
            },
        )
    assert response.status_code == 409
    assert "private diagnostic" not in response.text
    assert not sync_ops._JOBS


@pytest.mark.asyncio
async def test_lock_rescan_selection_failure_is_a_safe_conflict(tmp_path):
    src, dst = tmp_path / "movie.en.forced.srt", tmp_path / "out.srt"
    src.write_bytes(b"captions")

    def changed():
        raise OSError("private lock-rescan diagnostic")

    job = sync_ops.start_sync(sync_ops.sync_items([(src, dst)]), resolve=changed)
    await asyncio.gather(*sync_ops._BACKGROUND_TASKS)
    assert job.status == "error"
    assert job.to_dict().get("error_status") == 409
    assert "private lock-rescan diagnostic" not in job.error
    assert not dst.exists()


def test_warm_subtitle_identity_uses_only_one_target_metadata_read(monkeypatch):
    import xml.etree.ElementTree as ET  # noqa: S405 (literal fixture)

    from synclet import plex, subtitles

    monkeypatch.setattr(plex, "_KNOWN_TITLE_KEYS", {})
    monkeypatch.setattr(
        plex,
        "section_index",
        lambda section: {
            "movie": {"ratingKey": "42", "title": "Movie"},
        },
    )
    resolved = plex.find_in_library("movies", "Movie")
    assert resolved is not None
    assert resolved["ratingKey"] == "42"

    def forbidden(*args, **kwargs):
        raise AssertionError("subtitle assessment initiated library resolution")

    monkeypatch.setattr(plex, "section_index", forbidden)
    monkeypatch.setattr(plex, "_load_disk_cache", forbidden)
    calls = []

    def get_xml(path):
        calls.append(path)
        return ET.fromstring(  # noqa: S314 (literal fixture)
            '<MediaContainer><Video><Media><Part file="/exact/movie.mkv"/></Media></Video></MediaContainer>'
        )

    monkeypatch.setattr(plex, "_get_xml", get_xml)
    assert subtitles.reported_tracks("movies", "Movie") == {"/exact/movie.mkv": []}
    assert calls == ["/library/metadata/42"]
