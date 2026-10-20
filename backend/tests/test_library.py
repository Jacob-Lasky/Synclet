"""The library listing must not list a library root on a timer (Synclet#66)."""

from __future__ import annotations

import pathlib
import threading
from typing import cast

import pytest

from synclet import config, library, maint_cache, state
from synclet.scan import scan_titles

BCS = "Better Call Saul (2015) {tvdb-1}"
AFTER_LIFE = "After Life (2019) {tvdb-2}"


def _drain() -> None:
    """Wait for every running listing to finish, listeners included."""
    assert library.wait_for_listings(5)


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
    _drain()
    assert library.folders("tv") == sorted([AFTER_LIFE, BCS])
    assert library.folders("movies") == ["1917 (2019) {tmdb-3}"]
    # First boot lists every library once, together, and never again.
    assert sorted(count_scandir) == sorted(
        str(patch_paths["media"] / lib) for lib in config.LIBRARIES
    )


def test_missing_root_is_remembered_not_relisted(patch_paths, count_scandir):
    assert library.folders("YouTube") is None
    _drain()
    assert library.folders("YouTube") is None
    assert library.peek("YouTube", "anything") is False
    youtube = str(patch_paths["media"] / "YouTube")
    assert count_scandir.count(youtube) == 1


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
    _drain()
    listings = len(count_scandir)
    new = patch_paths["media"] / "tv" / "Severance (2022) {tvdb-9}"
    new.mkdir()
    assert library.note_title("tv", new.name) is True
    assert library.peek("tv", new.name) is True
    new.rmdir()
    assert library.note_title("tv", new.name) is False
    assert library.peek("tv", new.name) is False
    assert len(count_scandir) == listings  # stats only, no listing


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
    monkeypatch.setattr(library, "_stat_is_dir", lambda path: release.wait(5))
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
    _drain()
    assert library.folders("tv") == sorted([AFTER_LIFE, BCS])


def test_backstop_relist_only_when_due(patch_paths, count_scandir):
    library.folders("tv")
    _drain()
    now = library._listed_at["tv"]
    assert "tv" not in library.start_due_relists(now=now + 1)
    due = library.start_due_relists(now=now + config.LIBRARY_RELIST_S + 1)
    assert "tv" in due
    _drain()
    assert count_scandir.count(str(patch_paths["media"] / "tv")) == 2


def test_requested_relist_runs_on_next_wake(patch_paths, count_scandir):
    library.folders("tv")
    _drain()
    library.request_relist("tv")
    assert "tv" in library.start_due_relists(now=library._listed_at["tv"])
    _drain()
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
    _drain()
    assert calls.count("tv") == 1


def test_failed_listing_keeps_last_good_and_backs_off(patch_paths, monkeypatch):
    library.folders("tv")
    _drain()
    listed_at = library._listed_at["tv"]

    def broken(lib):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(library, "_list_library", broken)
    library.request_relist("tv")
    library.start_due_relists()
    _drain()
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
    # tv has an arr webhook, so only the re-list backstop ages its memo out.
    monkeypatch.setattr(config, "TITLE_RESCAN_S", 0)
    library.title_detail("tv", BCS)
    assert calls == [BCS, BCS]
    monkeypatch.setattr(config, "LIBRARY_RELIST_S", 0)
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
    _drain()
    assert library.peek("tv", new.name) is True


def test_retain_details_drops_unused_memos(patch_paths):
    library.title_detail("tv", BCS)
    library.title_detail("tv", AFTER_LIFE)
    library.retain_details({("tv", BCS)})
    assert set(library._detail_memo) == {("tv", BCS)}


def test_stuck_listing_does_not_hold_back_other_libraries(patch_paths, monkeypatch):
    library.folders("tv")
    _drain()
    release = threading.Event()
    original = library._list_library

    def tv_hangs(lib):
        if lib == "tv":
            release.wait(5)
        return original(lib)

    monkeypatch.setattr(library, "_list_library", tv_hangs)
    library.request_relist()
    assert library.start_due_relists() == ["tv"]
    # While tv's listing is young, nothing else starts on the share.
    assert library.start_due_relists() == []
    monkeypatch.setattr(config, "LIBRARY_LIST_TIMEOUT_S", 0)
    # Past the timeout it is treated as stuck: the next library may go.
    assert library.start_due_relists() == ["tv-4kUHD"]
    release.set()
    _drain()


def test_vanished_root_keeps_last_good_and_backs_off(patch_paths, monkeypatch):
    library.folders("tv")
    _drain()
    monkeypatch.setattr(library, "_list_library", lambda lib: None)
    library.request_relist("tv")
    library.start_due_relists()
    _drain()
    assert library.folders("tv") == sorted([AFTER_LIFE, BCS])
    status = library.status()["libraries"]["tv"]
    assert status["failed_at"] is not None
    assert status["failed_at"] >= status["listed_at"]
    assert library._retry_at["tv"] > status["failed_at"]


def test_share_error_on_stat_is_not_a_delete(patch_paths, monkeypatch):
    class BrokenShare:
        def stat(self):
            raise OSError(5, "Input/output error")

    assert library._stat_is_dir(cast(pathlib.Path, BrokenShare())) is None
    library.folders("tv")
    _drain()
    monkeypatch.setattr(library, "_stat_is_dir", lambda path: None)
    assert library.note_title("tv", BCS) is None
    assert library.peek("tv", BCS) is True


def test_missing_title_stat_is_a_delete(patch_paths):
    library.folders("tv")
    _drain()
    assert library._stat_is_dir(patch_paths["media"] / "tv" / "Nope") is False
    assert library._stat_is_dir(patch_paths["media"] / "tv" / BCS) is True


def test_an_older_stat_never_overwrites_a_newer_one(patch_paths, monkeypatch):
    """Stat A (present) starts first, stat B (absent) finishes first: B wins."""
    library.folders("tv")
    _drain()
    a_started, a_release = threading.Event(), threading.Event()
    answers = iter([True, False])

    def ordered(path):
        answer = next(answers)
        if answer:
            a_started.set()
            a_release.wait(5)
        return answer

    monkeypatch.setattr(library, "_stat_is_dir", ordered)
    results: list[bool | None] = []
    first = threading.Thread(
        target=lambda: results.append(library.note_title("tv", BCS))
    )
    first.start()
    assert a_started.wait(5)
    assert library.note_title("tv", BCS) is False
    a_release.set()
    first.join(5)
    assert results == [True]
    assert library.peek("tv", BCS) is False


def test_stuck_stats_fail_fast_instead_of_piling_up(patch_paths, monkeypatch):
    library.folders("tv")
    _drain()
    release = threading.Event()
    monkeypatch.setattr(config, "LIBRARY_STAT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(library, "_stat_is_dir", lambda path: release.wait(5))
    try:
        for _ in range(library._MAX_STATS_IN_FLIGHT):
            assert library.note_title("tv", BCS) is None
        started = threading.active_count()
        assert library.note_title("tv", BCS) is None
        assert threading.active_count() == started  # no new thread parked
    finally:
        release.set()


def test_relist_with_no_change_keeps_episode_memos(patch_paths, monkeypatch):
    from synclet import scan

    library.folders("tv")
    _drain()
    calls: list[str] = []
    original = scan.scan_title_detail
    monkeypatch.setattr(
        scan, "scan_title_detail", lambda lib, f: calls.append(f) or original(lib, f)
    )
    library.title_detail("tv", BCS)
    library.request_relist("tv")
    library.start_due_relists()
    _drain()
    library.title_detail("tv", BCS)
    assert calls == [BCS]


def test_saves_never_write_an_older_snapshot_last(patch_paths, monkeypatch):
    """Two concurrent saves: whichever writes last carries the newest state."""
    import json

    library.folders("tv")
    _drain()
    in_write, release = threading.Event(), threading.Event()
    original_write = library.Path.write_text

    def slow_first_write(self, data, *a, **k):
        if not in_write.is_set():
            in_write.set()
            release.wait(5)
        return original_write(self, data, *a, **k)

    monkeypatch.setattr(library.Path, "write_text", slow_first_write)
    old_save = threading.Thread(target=library._save)
    old_save.start()
    assert in_write.wait(5)
    new = patch_paths["media"] / "tv" / "Severance (2022) {tvdb-9}"
    new.mkdir()
    saver = threading.Thread(target=library.note_title, args=("tv", new.name))
    saver.start()
    release.set()
    old_save.join(5)
    saver.join(5)
    saved = json.loads(config.LIBRARY_LISTING_FILE.read_text())
    assert new.name in saved["libs"]["tv"]["folders"]
