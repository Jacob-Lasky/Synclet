"""Source-library title listing, kept current per title instead of by re-listing.

WHY. Each library root under MEDIA_ROOT is a merged shfs directory across every
array disk. Listing one costs 50 to 330 ms warm and 10 to 60 s or more cold, and
every other client in that directory (Plex, Jellyfin, smbd) queues behind it.
Synclet used to list every root twice per cache pass (the grid scan and the
followed-title presence check), about 10 passes an hour, and on a cold inode
cache the passes ran back to back (Synclet#66, homelab#103).

DO NOT go back to listing a library root on a timer or once per cache pass. The
listing here is:
  - persisted to LIBRARY_LISTING_FILE, so a container restart does not re-list,
  - updated one title at a time from events: arr Connect webhooks and Synclet's
    own source deletes call note_title(), which costs one os.stat,
  - re-listed in full only as a rare backstop (LIBRARY_RELIST_S) or when the
    user asks (/api/refresh), on ONE background worker thread. A slow share is
    never answered with more listing: a library whose listing is still running
    is not resubmitted, and readers keep serving the last-good copy meanwhile.

Share reads run on worker threads with a wait timeout. A stuck FUSE call cannot
be interrupted, but no caller waits on one past its timeout.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import TYPE_CHECKING

from common.log_utils import get_logger
from synclet import config

if TYPE_CHECKING:
    from synclet.scan import TitleDetail

logger = get_logger(__name__)

_SCHEMA = 1

_lock = threading.RLock()
# lib -> folder names, as of the last listing plus per-title updates since.
_folders: dict[str, set[str]] = {}
# lib -> wall-clock time of the last completed full listing.
_listed_at: dict[str, float] = {}
_loaded = False
# Libraries the user asked to re-list (/api/refresh), served by start_due_relists.
_relist_requested: set[str] = set()
_inflight: dict[str, Future] = {}
# lib -> earliest time a failed listing may be retried by the backstop.
_retry_at: dict[str, float] = {}
# lib -> {folder: present} for title events that arrived while that library's
# listing was running. The listing may have read the directory before the
# event, so these are replayed on top of its result instead of being lost.
_events_during_listing: dict[str, dict[str, bool]] = {}
# Bumped when a title's source content may have changed. title_detail() memos
# key on (library generation, title generation), so a full re-list or a single
# title event each invalidate exactly what they cover.
_lib_gen: dict[str, int] = {}
_title_gen: dict[tuple[str, str], int] = {}
_detail_memo: dict[
    tuple[str, str], tuple[tuple[int, int], float, TitleDetail | None]
] = {}
_listeners: list[Callable[[], None]] = []

# One listing worker, so listings never overlap on the share.
_list_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="library-list")
# Single-title stats are separate so one hung listing cannot block an event.
_stat_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="library-stat")


def valid_folder(lib: str, folder: str) -> bool:
    """A title folder name that can only name one direct child of a library."""
    return (
        lib in config.LIBRARIES
        and folder not in ("", ".", "..")
        and not folder.startswith(".")
        and folder not in config.EXCLUDED_DIRS
        and "/" not in folder
        and "\\" not in folder
    )


def subscribe(callback: Callable[[], None]) -> None:
    """Run `callback` whenever any library's title set or a title changes.

    The grid and synced caches register their invalidate() here so a listing or
    title event flags them for a background rebuild. Idempotent per callback.
    """
    with _lock:
        if callback not in _listeners:
            _listeners.append(callback)


def _notify() -> None:
    with _lock:
        listeners = list(_listeners)
    for callback in listeners:
        try:
            callback()
        except Exception:
            logger.exception("library change listener failed")


def _load() -> None:
    """Load the persisted listing once. A missing or bad file means cold."""
    global _loaded
    with _lock:
        if _loaded:
            return
        _loaded = True
        try:
            raw = json.loads(config.LIBRARY_LISTING_FILE.read_text())
        except OSError, ValueError:
            return
        if not isinstance(raw, dict) or raw.get("v") != _SCHEMA:
            return
        libs = raw.get("libs")
        if not isinstance(libs, dict):
            return
        for lib, entry in libs.items():
            if lib not in config.LIBRARIES or not isinstance(entry, dict):
                continue
            names = entry.get("folders")
            listed_at = entry.get("listed_at")
            if not isinstance(names, list) or not isinstance(listed_at, (int, float)):
                continue
            _folders[lib] = {n for n in names if isinstance(n, str)}
            _listed_at[lib] = float(listed_at)


def _save() -> None:
    """Persist the listing atomically. Best-effort: memory stays authoritative."""
    with _lock:
        payload = {
            "v": _SCHEMA,
            "libs": {
                lib: {"listed_at": _listed_at.get(lib, 0.0), "folders": sorted(names)}
                for lib, names in _folders.items()
            },
        }
    path = config.LIBRARY_LISTING_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
    except OSError:
        logger.warning("could not persist library listing to %s", path)


def _list_library(lib: str) -> set[str] | None:
    """The one full listing of a library root. None when the root is missing."""
    root = config.MEDIA_ROOT / lib
    names: set[str] = set()
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False) and valid_folder(
                        lib, entry.name
                    ):
                        names.add(entry.name)
                except OSError:
                    # One vanishing entry must not drop the whole library.
                    continue
    except FileNotFoundError:
        return None
    return names


def _run_listing(lib: str) -> set[str] | None:
    """Run one listing on the listing worker and commit it.

    A failed listing keeps the last-good copy and is not retried before
    LIBRARY_RETRY_S, so a broken share is not hammered every loop wake.
    """
    started = time.monotonic()
    try:
        names = _list_library(lib)
    except OSError as exc:
        with _lock:
            _retry_at[lib] = time.time() + config.LIBRARY_RETRY_S
            _inflight.pop(lib, None)
            _events_during_listing.pop(lib, None)
            last_good = set(_folders[lib]) if lib in _folders else None
        logger.warning("library listing for %s failed: %r; keeping last-good", lib, exc)
        return last_good
    elapsed = time.monotonic() - started
    with _lock:
        events = _events_during_listing.pop(lib, {})
        if names is None and any(events.values()):
            names = set()  # the root appeared after the listing looked
        if names is None:
            _folders.pop(lib, None)
        else:
            for folder, present in events.items():
                if present:
                    names.add(folder)
                else:
                    names.discard(folder)
            _folders[lib] = names
        _listed_at[lib] = time.time()
        _retry_at.pop(lib, None)
        _lib_gen[lib] = _lib_gen.get(lib, 0) + 1
        _inflight.pop(lib, None)
    logger.info(
        "library listing: %s, %s titles in %.1fs",
        lib,
        "no root" if names is None else len(names),
        elapsed,
    )
    _save()
    _notify()
    return names


def _submit_listing(lib: str) -> Future:
    with _lock:
        future = _inflight.get(lib)
        if future is None:
            future = _list_pool.submit(_run_listing, lib)
            _inflight[lib] = future
        _relist_requested.discard(lib)
        return future


def folders(lib: str) -> list[str] | None:
    """Sorted title folders for `lib`, or None when it has no listing.

    Never lists when any copy exists, even an old one: the backstop re-list in
    start_due_relists refreshes it off the request path. Only the very first
    read of a library with no persisted copy waits for a listing, and only up
    to LIBRARY_LIST_TIMEOUT_S; past that it returns None and the listing lands
    in the background, notifying listeners when it does.
    """
    _load()
    with _lock:
        if lib in _folders:
            return sorted(_folders[lib])
        if lib in _listed_at:
            return None  # listed, and the root does not exist
    try:
        names = _submit_listing(lib).result(timeout=config.LIBRARY_LIST_TIMEOUT_S)
    except FutureTimeout:
        logger.warning(
            "library listing for %s still running after %ss; serving it as empty",
            lib,
            config.LIBRARY_LIST_TIMEOUT_S,
        )
        return None
    return sorted(names) if names is not None else None


def peek(lib: str, folder: str) -> bool | None:
    """True/False from the listing, or None when `lib` has no listing. Never lists."""
    _load()
    with _lock:
        names = _folders.get(lib)
        if names is None:
            return False if lib in _listed_at else None
        return folder in names


def source_lib(folder: str) -> str | None:
    """The first library (config order) whose listing holds `folder`.

    A set lookup per library; folders() is consulted only to prime a library
    that has no listing yet.
    """
    for lib in config.LIBRARIES:
        present = peek(lib, folder)
        if present is None:
            folders(lib)
            present = peek(lib, folder)
        if present:
            return lib
    return None


def _record(lib: str, folder: str, present: bool) -> None:
    with _lock:
        key = (lib, folder)
        _title_gen[key] = _title_gen.get(key, 0) + 1
        if lib in _inflight:
            _events_during_listing.setdefault(lib, {})[folder] = present
        # DO NOT start a listing from one event. A library with no listing yet
        # would then read as complete with a single title; its cold listing
        # captures this title anyway.
        if lib not in _folders and lib not in _listed_at:
            return
        names = _folders.setdefault(lib, set())
        if present:
            names.add(folder)
        else:
            names.discard(folder)


def note_title(lib: str, folder: str) -> bool | None:
    """Re-check ONE title on the share and update the listing for it.

    Called on an arr webhook and after Synclet's own source delete. Costs one
    os.stat under LIBRARY_STAT_TIMEOUT_S. Returns whether the folder exists, or
    None when the stat timed out or failed (the listing is left as it was and
    the backstop re-list reconciles it).
    """
    if not valid_folder(lib, folder):
        raise ValueError("invalid library title")
    _load()
    path = config.MEDIA_ROOT / lib / folder
    future = _stat_pool.submit(path.is_dir)
    try:
        present = future.result(timeout=config.LIBRARY_STAT_TIMEOUT_S)
    except FutureTimeout:
        logger.warning("stat of %s/%s timed out; listing unchanged", lib, folder)
        return None
    except OSError:
        return None
    _record(lib, folder, present)
    _save()
    _notify()
    return present


def record_present(lib: str, folder: str) -> None:
    """Record a title the caller has just confirmed exists (no extra stat)."""
    if not valid_folder(lib, folder):
        raise ValueError("invalid library title")
    _load()
    _record(lib, folder, True)
    _save()
    _notify()


def request_relist(lib: str | None = None) -> None:
    """Ask for a full re-list of one or every library on the next loop wake."""
    with _lock:
        _relist_requested.update(config.LIBRARIES if lib is None else (lib,))


def start_due_relists(now: float | None = None) -> list[str]:
    """Submit the backstop re-list for libraries that are due. Never blocks.

    Due means: asked for via request_relist, or older than LIBRARY_RELIST_S and
    past any failure backoff.
    Libraries with a listing already running are skipped, so a slow share does
    not collect a queue of listings. Returns the libraries submitted.
    """
    _load()
    now = time.time() if now is None else now
    submitted: list[str] = []
    with _lock:
        due = [
            lib
            for lib in config.LIBRARIES
            if lib not in _inflight
            and (
                lib in _relist_requested
                or (
                    now - _listed_at.get(lib, 0.0) >= config.LIBRARY_RELIST_S
                    and now >= _retry_at.get(lib, 0.0)
                )
            )
        ]
    for lib in due:
        _submit_listing(lib)
        submitted.append(lib)
    return submitted


def title_version(lib: str, folder: str) -> tuple[int, int]:
    with _lock:
        return _lib_gen.get(lib, 0), _title_gen.get((lib, folder), 0)


def title_detail(lib: str, folder: str) -> TitleDetail | None:
    """scan_title_detail for background builders, memoized per title.

    Rescans when that title had an event, its library was re-listed, or the
    memo is older than TITLE_RESCAN_S (the backstop for sources with no arr
    webhook, such as YouTube channels). Request handlers that show or delete a
    title call scan_title_detail directly so they always see the live tree.
    """
    from synclet.scan import scan_title_detail  # local to avoid cycle

    key = (lib, folder)
    version = title_version(lib, folder)
    now = time.monotonic()
    with _lock:
        hit = _detail_memo.get(key)
    if hit is not None and hit[0] == version and now - hit[1] < config.TITLE_RESCAN_S:
        return hit[2]
    detail = scan_title_detail(lib, folder)
    with _lock:
        _detail_memo[key] = (version, now, detail)
    return detail


def retain_details(keep: set[tuple[str, str]]) -> None:
    """Drop memoized episode trees for titles the background builders no longer use."""
    with _lock:
        for key in [k for k in _detail_memo if k not in keep]:
            del _detail_memo[key]


def status() -> dict:
    """Listing ages and sizes, for /api/library and the deploy check."""
    _load()
    now = time.time()
    with _lock:
        return {
            "relist_interval_s": config.LIBRARY_RELIST_S,
            "libraries": {
                lib: {
                    "titles": len(_folders[lib]) if lib in _folders else None,
                    "age_s": round(now - _listed_at[lib])
                    if lib in _listed_at
                    else None,
                    "listing": lib in _inflight,
                }
                for lib in config.LIBRARIES
            },
        }


def reset() -> None:
    """Drop all in-memory state. Tests only; the persisted file is untouched."""
    global _loaded
    with _lock:
        _folders.clear()
        _listed_at.clear()
        _relist_requested.clear()
        _inflight.clear()
        _retry_at.clear()
        _events_during_listing.clear()
        _lib_gen.clear()
        _title_gen.clear()
        _detail_memo.clear()
        _listeners.clear()
        _loaded = False
