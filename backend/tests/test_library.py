"""The library listing must not list a library root on a timer (Synclet#66)."""

from __future__ import annotations

import pathlib
import threading

import pytest

from synclet import config, library, maint_cache, state
from synclet.scan import scan_titles

BCS = "Better Call Saul (2015) {tvdb-1}"
AFTER_LIFE = "After Life (2019) {tvdb-2}"


@pytest.fixture
def count_scandir(monkeypatch):
    """Count os.scandir calls on library roots, keeping their behavior."""
    calls: list[str] = []
    original = library.os.scandir

    def counting(path):
        calls.append(str(path))
        return original(path)

    monkeypatch.setattr(library.os, "scandir", counting)
    return calls


def test_cold_read_lists_once_then_serves_memory(patch_paths, count_scandir):
    assert library.folders("tv") == sorted([AFTER_LIFE, BCS])
    assert library.folders("tv") == sorted([AFTER_LIFE, BCS])
    assert count_scandir == [str(patch_paths["media"] / "tv")]


def test_missing_root_is_remembered_not_relisted(patch_paths, count_scandir):
    assert library.folders("YouTube") is None
    assert library.folders("YouTube") is None
    assert library.peek("YouTube", "anything") is False
    assert len(count_scandir) == 1


def test_restart_reads_persisted_listing_without_listing(patch_paths, monkeypatch):
    library.folders("tv")
    library.reset()

    def no_listing(path):
        raise AssertionError(f"listed {path} after restart")

    monkeypatch.setattr(library.os, "scandir", no_listing)
    assert library.folders("tv") == sorted([AFTER_LIFE, BCS])


def test_corrupt_persisted_listing_is_a_cold_start(patch_paths):
    config.LIBRARY_LISTING_FILE.write_text("{not json")
    assert library.peek("tv", BCS) is None
    assert library.folders("tv") == sorted([AFTER_LIFE, BCS])


def test_full_cache_passes_never_list_library_roots(
    patch_paths, patch_watchstate, count_scandir
):
    """The regression this module exists for: grid rebuilds reuse the listing."""
    state.register_builders()
    for _ in range(3):
        maint_cache.run_refresh_cycle(full=True)
    listed = [p for p in count_scandir if p.startswith(str(patch_paths["media"]))]
    # Each library root was tried once, on the cold first build, and never again.
    assert sorted(listed) == sorted(
        str(patch_paths["media"] / lib) for lib in config.LIBRARIES
    )


def test_note_title_adds_and_removes_one_title(patch_paths, count_scandir):
    library.folders("tv")
    new = patch_paths["media"] / "tv" / "Severance (2022) {tvdb-9}"
    new.mkdir()
    assert library.note_title("tv", new.name) is True
    assert library.peek("tv", new.name) is True
    new.rmdir()
    assert library.note_title("tv", new.name) is False
    assert library.peek("tv", new.name) is False
    assert len(count_scandir) == 1  # one cold listing, then stats only


def test_note_title_rejects_paths_outside_a_library(patch_paths):
    for lib, folder in (("tv", "../movies"), ("tv", ".hidden"), ("nope", "x")):
        with pytest.raises(ValueError):
            library.note_title(lib, folder)


def test_event_before_any_listing_does_not_fake_a_listing(patch_paths):
    library.note_title("tv", BCS)
    assert library.peek("tv", BCS) is None
    assert library.folders("tv") == sorted([AFTER_LIFE, BCS])


def test_note_title_notifies_listeners(patch_paths):
    seen: list[int] = []
    library.subscribe(lambda: seen.append(1))
    library.note_title("tv", BCS)
    assert seen == [1]


def test_failing_listener_does_not_break_the_event(patch_paths):
    def boom():
        raise RuntimeError("listener blew up")

    library.subscribe(boom)
    assert library.note_title("tv", BCS) is True


def test_stat_timeout_leaves_listing_unchanged(patch_paths, monkeypatch):
    library.folders("tv")
    release = threading.Event()
    monkeypatch.setattr(config, "LIBRARY_STAT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(pathlib.Path, "is_dir", lambda self: release.wait(5))
    try:
        assert library.note_title("tv", "Gone (2020)") is None
        assert library.peek("tv", "Gone (2020)") is False
    finally:
        release.set()


def test_cold_listing_timeout_serves_none_then_lands(patch_paths, monkeypatch):
    release = threading.Event()
    original = library._list_library

    def slow(lib):
        release.wait(5)
        return original(lib)

    monkeypatch.setattr(library, "_list_library", slow)
    monkeypatch.setattr(config, "LIBRARY_LIST_TIMEOUT_S", 0.05)
    assert library.folders("tv") is None
    release.set()
    # The same in-flight listing finishes; nothing new is submitted.
    inflight = library._inflight.get("tv")
    if inflight is not None:
        inflight.result(5)
    assert library.folders("tv") == sorted([AFTER_LIFE, BCS])


def test_backstop_relist_only_when_due(patch_paths, count_scandir):
    library.folders("tv")
    now = library._listed_at["tv"]
    assert "tv" not in library.start_due_relists(now=now + 1)
    due = library.start_due_relists(now=now + config.LIBRARY_RELIST_S + 1)
    assert "tv" in due
    library._list_pool.submit(lambda: None).result(5)  # drain the worker
    assert count_scandir.count(str(patch_paths["media"] / "tv")) == 2


def test_requested_relist_runs_on_next_wake(patch_paths, count_scandir):
    library.folders("tv")
    library.request_relist("tv")
    assert "tv" in library.start_due_relists(now=library._listed_at["tv"])
    library._list_pool.submit(lambda: None).result(5)
    assert count_scandir.count(str(patch_paths["media"] / "tv")) == 2


def test_inflight_listing_is_never_stacked(patch_paths, monkeypatch):
    release = threading.Event()
    calls: list[str] = []

    def slow(lib):
        calls.append(lib)
        release.wait(5)
        return set()

    monkeypatch.setattr(library, "_list_library", slow)
    library.request_relist("tv")
    library.start_due_relists()
    library.request_relist("tv")
    library.start_due_relists()
    release.set()
    library._list_pool.submit(lambda: None).result(5)
    assert calls.count("tv") == 1


def test_failed_listing_keeps_last_good_and_backs_off(patch_paths, monkeypatch):
    library.folders("tv")
    listed_at = library._listed_at["tv"]

    def broken(lib):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(library, "_list_library", broken)
    library.request_relist("tv")
    library.start_due_relists()
    library._list_pool.submit(lambda: None).result(5)
    assert library.peek("tv", BCS) is True
    later = listed_at + config.LIBRARY_RELIST_S + 1
    assert library._retry_at["tv"] > 0
    assert "tv" not in library.start_due_relists(
        now=min(later, library._retry_at["tv"] - 1)
    )


def test_source_lib_answers_from_listing(patch_paths, monkeypatch):
    assert library.source_lib(BCS) == "tv"
    assert library.source_lib("1917 (2019) {tmdb-3}") == "movies"
    assert library.source_lib("Nope") is None


def test_title_detail_is_reused_until_the_title_changes(patch_paths, monkeypatch):
    from synclet import scan

    calls: list[str] = []
    original = scan.scan_title_detail

    def counting(lib, folder):
        calls.append(folder)
        return original(lib, folder)

    monkeypatch.setattr(scan, "scan_title_detail", counting)
    library.title_detail("tv", BCS)
    library.title_detail("tv", BCS)
    assert calls == [BCS]
    library.note_title("tv", BCS)
    library.title_detail("tv", BCS)
    assert calls == [BCS, BCS]
    monkeypatch.setattr(config, "TITLE_RESCAN_S", 0)
    library.title_detail("tv", BCS)
    assert calls == [BCS, BCS, BCS]


def test_scan_titles_uses_listing(patch_paths, patch_watchstate):
    titles = scan_titles()
    assert {t.folder for t in titles} == {BCS, AFTER_LIFE, "1917 (2019) {tmdb-3}"}


def test_status_reports_listing_age(patch_paths):
    library.folders("tv")
    out = library.status()
    assert out["libraries"]["tv"]["titles"] == 2
    assert out["libraries"]["YouTube"]["titles"] is None
    assert out["relist_interval_s"] == config.LIBRARY_RELIST_S


def test_unwritable_listing_file_is_best_effort(patch_paths, monkeypatch):
    monkeypatch.setattr(
        config,
        "LIBRARY_LISTING_FILE",
        patch_paths["media"] / "tv" / BCS / "Season 01" / "x" / "y",
    )
    (patch_paths["media"] / "tv" / BCS / "Season 01" / "x").write_text("a file")
    assert library.folders("tv") == sorted([AFTER_LIFE, BCS])


def test_event_during_a_listing_is_not_lost(patch_paths, monkeypatch):
    """A title added while the (slow) listing runs survives the listing's commit."""
    started = threading.Event()
    release = threading.Event()

    def slow(lib):
        names = {AFTER_LIFE, BCS}  # read the directory before the new title
        started.set()
        release.wait(5)
        return names

    monkeypatch.setattr(library, "_list_library", slow)
    library.request_relist("tv")
    library.start_due_relists()
    assert started.wait(5)
    new = patch_paths["media"] / "tv" / "Severance (2022) {tvdb-9}"
    new.mkdir()
    library.note_title("tv", new.name)
    release.set()
    library._list_pool.submit(lambda: None).result(5)
    assert library.peek("tv", new.name) is True


def test_retain_details_drops_unused_memos(patch_paths):
    library.title_detail("tv", BCS)
    library.title_detail("tv", AFTER_LIFE)
    library.retain_details({("tv", BCS)})
    assert set(library._detail_memo) == {("tv", BCS)}
