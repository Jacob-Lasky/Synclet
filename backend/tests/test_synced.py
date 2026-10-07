"""Tests for the two-phase /api/synced assembly (synclet.synced).

Phase 1 (the synced list + sizes) is served immediately; phase 2 (per-show
new-unwatched badges) is built only by the background loop and layered on once
present. These tests pin that contract directly against the builders.
"""

from __future__ import annotations

import pytest

from synclet import maint_cache, synced


@pytest.fixture(autouse=True)
def _clean_cache():
    maint_cache.clear()
    yield
    maint_cache.clear()


def test_two_phase_list_first_then_enrichment(
    patch_paths, patch_watchstate, monkeypatch
):
    # Keep the Plex-direct fallback offline so the enrichment build is pure
    # WatchState + local scan, no network.
    monkeypatch.setattr("synclet.plex.section_index", lambda *a, **k: {})

    # Phase 1: the list is present immediately, enrichment is not yet built.
    payload = synced.get_synced()
    assert payload["enriched"] is False
    assert payload["items"], "the synced list must be served on first read"
    assert all(it["new_unwatched"] == [] for it in payload["items"])
    # Every item carries the wire-contract fields.
    for it in payload["items"]:
        assert {
            "title",
            "folder",
            "lib",
            "kind",
            "size_bytes",
            "synced_episodes",
            "mtime",
            "new_unwatched",
        } <= it.keys()

    # The downloaded-episode count comes from the local phase, so it is present
    # on first paint. The fixture pre-synced one After Life episode.
    al = next(
        it for it in payload["items"] if it["folder"] == "After Life (2019) {tvdb-2}"
    )
    assert al["synced_episodes"] == 1
    # mtime rides the same local phase: a synced file exists, so it is positive.
    assert al["mtime"] > 0

    # The background loop builds the enrichment phase.
    maint_cache.run_refresh_cycle(full=True)
    enriched = synced.get_synced()
    assert enriched["enriched"] is True


def test_enrichment_surfaces_unwatched_unsynced_episode(
    patch_paths, patch_watchstate, monkeypatch
):
    """A show with an episode that is neither synced nor watched shows up in the
    enrichment as a new_unwatched entry once the background phase builds."""
    monkeypatch.setattr("synclet.plex.section_index", lambda *a, **k: {})

    media = patch_paths["media"]
    sync = patch_paths["sync"]
    # After Life S01E01 is already synced + watched (fixture). Add S01E02 to the
    # library only (not synced) and leave it out of WatchState, so it is an
    # unwatched, unsynced episode of a synced show.
    al_media = media / "tv" / "After Life (2019) {tvdb-2}" / "Season 01"
    (al_media / "After Life - S01E02 - Episode 2.mkv").write_bytes(b"\0" * 1024)
    # Ensure the title is synced (it is in the fixture); confirm the dir exists.
    assert (sync / "tv" / "After Life (2019) {tvdb-2}").is_dir()

    enrichment = synced._build_enrichment()
    folder = "After Life (2019) {tvdb-2}"
    assert ("tv", folder) in enrichment
    eps = {(e["season"], e["episode"]) for e in enrichment["tv", folder]}
    assert (1, 2) in eps
    assert (1, 1) not in eps  # synced + watched → not "new unwatched"


def test_force_flags_both_phases_dirty(patch_paths, patch_watchstate, monkeypatch):
    monkeypatch.setattr("synclet.plex.section_index", lambda *a, **k: {})
    synced.get_synced()  # prime + register
    maint_cache.run_refresh_cycle(full=True)  # clear dirty flags

    synced.get_synced(force=True)
    assert "synced_local" in maint_cache._dirty
    assert "synced_enrichment" in maint_cache._dirty


def test_followed_show_remains_when_last_offline_episode_is_removed(
    patch_paths, patch_watchstate, monkeypatch
):
    """Following is title intent, so it survives an empty synced folder."""
    from synclet import followed

    monkeypatch.setattr("synclet.plex.section_index", lambda *a, **k: {})
    folder = "After Life (2019) {tvdb-2}"
    followed.set_following("tv", folder, True)
    synced_file = (
        patch_paths["sync"]
        / "tv"
        / folder
        / "Season 01"
        / "After Life - S01E01 - Episode 1.mkv"
    )
    synced_file.unlink()
    synced_file.parent.rmdir()
    synced_file.parent.parent.rmdir()
    (
        patch_paths["media"]
        / "tv"
        / folder
        / "Season 01"
        / "After Life - S01E02 - Episode 2.mkv"
    ).write_bytes(b"\0" * 1024)

    payload = synced.get_synced()
    entry = next(it for it in payload["items"] if it["folder"] == folder)
    assert entry["followed"] is True
    assert entry["synced_episodes"] == 0
    assert entry["size_bytes"] == 0

    maint_cache.run_refresh_cycle(full=True)
    entry = next(it for it in synced.get_synced()["items"] if it["folder"] == folder)
    assert entry["new_unwatched"] == [
        {"season": 1, "episode": 2, "title": "Episode 2", "size_bytes": 1024}
    ]


def test_follow_bootstrap_skips_ambiguous_tv_and_4k_source(patch_paths):
    from synclet import followed

    folder = "After Life (2019) {tvdb-2}"
    (patch_paths["media"] / "tv-4kUHD" / folder).mkdir(parents=True)

    assert ("tv", folder) not in followed.get_followed()
    assert ("tv-4kUHD", folder) not in followed.get_followed()
