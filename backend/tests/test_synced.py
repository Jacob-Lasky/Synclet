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
        assert {"title", "folder", "lib", "kind", "size_bytes", "new_unwatched"} <= (
            it.keys()
        )

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
    assert folder in enrichment
    eps = {(e["season"], e["episode"]) for e in enrichment[folder]}
    assert (1, 2) in eps
    assert (1, 1) not in eps  # synced + watched → not "new unwatched"


def test_force_flags_both_phases_dirty(patch_paths, patch_watchstate, monkeypatch):
    monkeypatch.setattr("synclet.plex.section_index", lambda *a, **k: {})
    synced.get_synced()  # prime + register
    maint_cache.run_refresh_cycle(full=True)  # clear dirty flags

    synced.get_synced(force=True)
    assert "synced_local" in maint_cache._dirty
    assert "synced_enrichment" in maint_cache._dirty
