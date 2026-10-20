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
    user asks (/api/refresh), one library at a time. A slow share is never
    answered with more listing: nothing new starts while a listing is running,
    a library is never listed twice at once, and readers keep serving the
    last-good copy meanwhile.

Share reads run on daemon threads with a wait timeout. A stuck FUSE call cannot
be interrupted, but no caller waits on one past its timeout and a stuck thread
holds up nothing else.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import TYPE_CHECKING

from common.log_utils import get_logger
from synclet import config

if TYPE_CHECKING:
    from synclet.scan import TitleDetail

logger = get_logger(__name__)

_SCHEMA = 1

_lock = threading.RLock()
# Serializes persisting. The snapshot is taken INSIDE this lock, so a later
# writer always writes a later snapshot and two saves never share a tmp file.
_save_lock = threading.Lock()
# lib -> folder names, as of the last listing plus per-title updates since.
_folders: dict[str, set[str]] = {}
# lib -> wall-clock time of the last completed full listing.
_listed_at: dict[str, float] = {}
# lib -> wall-clock time of the last failed listing (kept last-good instead).
_failed_at: dict[str, float] = {}
_loaded = False
# Libraries the user asked to re-list (/api/refresh), served by start_due_relists.
_relist_requested: set[str] = set()
# lib -> (future, monotonic start) of its running listing. At most one per lib.
_inflight: dict[str, tuple[Future, float]] = {}
# lib -> earliest time a failed listing may be retried by the backstop.
_retry_at: dict[str, float] = {}
# lib -> {folder: present} for title events that arrived while that library's
# listing was running. The listing may have read the directory before the
# event, so these are replayed on top of its result instead of being lost.
_events_during_listing: dict[str, dict[str, bool]] = {}
# Bumped when a title's source content may have changed (an event, or a re-list
# that found it added or removed). title_detail() memos key on it.
_title_gen: dict[tuple[str, str], int] = {}
# Per-title stat ordering: a stat result is applied only if no later-started
# stat of the same title has already been applied.
_stat_seq = 0
_applied_seq: dict[tuple[str, str], int] = {}
_detail_memo: dict[tuple[str, str], tuple[int, float, TitleDetail | None]] = {}
_listeners: list[Callable[[], None]] = []
# Listing threads that have not yet finished saving and notifying. Lets
# wait_for_listings() see a listing through its listeners, not just its commit.
_active_listings = 0
_listings_idle = threading.Condition(_lock)
# Libraries whose listing thread has not finished saving and notifying. status()
# reports these as listing, so a client never sees a fresh listed_at before the
# grid has been flagged for its rebuild.
_busy_libs: set[str] = set()

# Bounds threads parked on a hung share by single-title stats. When all are
# stuck, further events return "unknown" at once instead of piling up threads.
_MAX_STATS_IN_FLIGHT = 4
_stat_slots = threading.BoundedSemaphore(_MAX_STATS_IN_FLIGHT)


def _in_daemon_thread[T](name: str, fn: Callable[[], T]) -> Future[T]:
    """Run fn on a fresh daemon thread and return its Future.

    Daemon threads, not a pool: a listing or stat stuck in FUSE must neither
    hold a pool worker that later work queues behind, nor block process exit.
    """
    future: Future[T] = Future()

    def run() -> None:
        try:
            future.set_result(fn())
        except BaseException as exc:  # handed to the waiter via the Future
            future.set_exception(exc)

    threading.Thread(target=run, name=name, daemon=True).start()
    return future


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
    path = config.LIBRARY_LISTING_FILE
    with _save_lock:
        with _lock:
            payload = {
                "v": _SCHEMA,
                "libs": {
                    lib: {
                        "listed_at": _listed_at.get(lib, 0.0),
                        "folders": sorted(names),
                    }
                    for lib, names in _folders.items()
                },
            }
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


def _listing_failed(lib: str, reason: str) -> set[str] | None:
    """Keep the last-good copy and back off before the backstop retries."""
    now = time.time()
    with _lock:
        _retry_at[lib] = now + config.LIBRARY_RETRY_S
        _failed_at[lib] = now
        _inflight.pop(lib, None)
        _events_during_listing.pop(lib, None)
        last_good = set(_folders[lib]) if lib in _folders else None
    logger.warning("library listing for %s failed: %s; keeping last-good", lib, reason)
    return last_good


def _run_listing(lib: str) -> set[str] | None:
    """Run one listing and commit it.

    A failed listing keeps the last-good copy and is not retried before
    LIBRARY_RETRY_S, so a broken share is not hammered every loop wake. A root
    that vanishes after it was listed with titles counts as a failure too: an
    unmounted disk or a share hiccup must not erase the catalog for six hours.
    """
    started = time.monotonic()
    try:
        names = _list_library(lib)
    except OSError as exc:
        return _listing_failed(lib, repr(exc))
    with _lock:
        events = _events_during_listing.pop(lib, {})
        if names is None and any(events.values()):
            names = set()  # the root appeared after the listing looked
        changed = 0
        if names is None and _folders.get(lib):
            vanished = True
        else:
            vanished = False
            old = _folders.get(lib, set())
            if names is not None:
                for folder, present in events.items():
                    if present:
                        names.add(folder)
                    else:
                        names.discard(folder)
                _folders[lib] = names
            else:
                _folders.pop(lib, None)
            for folder in old ^ (names or set()):
                key = (lib, folder)
                _title_gen[key] = _title_gen.get(key, 0) + 1
            changed = len(old ^ (names or set()))
            _listed_at[lib] = time.time()
            _retry_at.pop(lib, None)
            _inflight.pop(lib, None)
    if vanished:
        return _listing_failed(lib, "library root is missing")
    logger.info(
        "library listing: %s, %s titles (%d changed) in %.1fs",
        lib,
        "no root" if names is None else len(names),
        changed,
        time.monotonic() - started,
    )
    _save()
    _notify()
    return names


def _tracked_listing(lib: str) -> set[str] | None:
    global _active_listings
    try:
        return _run_listing(lib)
    finally:
        with _lock:
            _active_listings -= 1
            _busy_libs.discard(lib)
            _listings_idle.notify_all()


def wait_for_listings(timeout: float) -> bool:
    """Block until no listing is running or committing. True if idle in time."""
    with _lock:
        return _listings_idle.wait_for(lambda: _active_listings == 0, timeout)


def _submit_listing(lib: str) -> Future:
    global _active_listings
    with _lock:
        running = _inflight.get(lib)
        if running is None:
            _active_listings += 1
            _busy_libs.add(lib)
            running = (
                _in_daemon_thread(f"library-list-{lib}", lambda: _tracked_listing(lib)),
                time.monotonic(),
            )
            _inflight[lib] = running
        _relist_requested.discard(lib)
        return running[0]


def folders(lib: str) -> list[str] | None:
    """Sorted title folders for `lib`, or None when it has no listing.

    Never lists when any copy exists, even an old one: the backstop re-list in
    start_due_relists refreshes it off the request path. Only a library with no
    persisted copy at all (the first boot ever) waits for a listing, and only
    up to LIBRARY_LIST_TIMEOUT_S. That first boot starts every unlisted
    library at once, so a hung root costs one timeout, not one per library.
    Past the timeout this returns None and the listing lands in the background,
    notifying listeners when it does.
    """
    _load()
    with _lock:
        if lib in _folders:
            return sorted(_folders[lib])
        if lib in _listed_at:
            return None  # listed, and the root does not exist
        cold = [
            other
            for other in config.LIBRARIES
            if other not in _folders and other not in _listed_at
        ]
    for other in cold:
        _submit_listing(other)
    with _lock:
        running = _inflight.get(lib)
    if running is None:  # finished between the two locks
        return folders(lib) if (lib in _folders or lib in _listed_at) else None
    future, started = running
    remaining = config.LIBRARY_LIST_TIMEOUT_S - (time.monotonic() - started)
    try:
        names = future.result(timeout=max(0.0, remaining))
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


def _record(lib: str, folder: str, present: bool, seq: int | None = None) -> bool:
    """Apply one title's presence. Returns False if a newer stat already won."""
    with _lock:
        key = (lib, folder)
        if seq is not None:
            if seq <= _applied_seq.get(key, -1):
                return False
            _applied_seq[key] = seq
        _title_gen[key] = _title_gen.get(key, 0) + 1
        if lib in _inflight:
            _events_during_listing.setdefault(lib, {})[folder] = present
        # DO NOT start a listing from one event. A library with no listing yet
        # would then read as complete with a single title; its cold listing
        # captures this title anyway.
        if lib not in _folders and lib not in _listed_at:
            return True
        names = _folders.setdefault(lib, set())
        if present:
            names.add(folder)
        else:
            names.discard(folder)
        return True


def _stat_is_dir(path: Path) -> bool | None:
    """True/False for a directory that does or does not exist, None if unknown.

    DO NOT use Path.is_dir() here: it returns False on ANY OSError, so a
    transient share error would be recorded as the title being deleted.
    """
    try:
        return stat.S_ISDIR(path.stat().st_mode)
    except FileNotFoundError, NotADirectoryError:
        return False
    except OSError:
        return None


def note_title(lib: str, folder: str) -> bool | None:
    """Re-check ONE title on the share and update the listing for it.

    Called on an arr webhook and after Synclet's own source delete. Costs one
    os.stat under LIBRARY_STAT_TIMEOUT_S. Returns whether the folder exists, or
    None when the stat timed out or failed (the listing is left as it was and
    the backstop re-list reconciles it).
    """
    global _stat_seq
    if not valid_folder(lib, folder):
        raise ValueError("invalid library title")
    _load()
    if not _stat_slots.acquire(blocking=False):
        logger.warning("stats are stuck on the share; %s/%s left as is", lib, folder)
        return None
    with _lock:
        _stat_seq += 1
        seq = _stat_seq
    path = config.MEDIA_ROOT / lib / folder

    def stat_then_release() -> bool | None:
        try:
            return _stat_is_dir(path)
        finally:
            _stat_slots.release()

    future = _in_daemon_thread("library-stat", stat_then_release)
    try:
        present = future.result(timeout=config.LIBRARY_STAT_TIMEOUT_S)
    except FutureTimeout:
        logger.warning("stat of %s/%s timed out; listing unchanged", lib, folder)
        return None
    if present is None:
        return None
    if _record(lib, folder, present, seq):
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
    """Start the backstop re-list for at most ONE due library. Never blocks.

    Due means: asked for via request_relist, or older than LIBRARY_RELIST_S and
    past any failure backoff. Libraries are re-listed one after another, one
    per loop wake, and nothing new starts while a listing younger than
    LIBRARY_LIST_TIMEOUT_S is running, so a slow share is never answered with
    more listing. A listing stuck past that timeout no longer holds the others
    back (it keeps its own library from being resubmitted). Returns the library
    started, if any.
    """
    _load()
    now = time.time() if now is None else now
    with _lock:
        if any(
            time.monotonic() - started < config.LIBRARY_LIST_TIMEOUT_S
            for _future, started in _inflight.values()
        ):
            return []
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
    if not due:
        return []
    _submit_listing(due[0])
    return [due[0]]


def title_version(lib: str, folder: str) -> int:
    with _lock:
        return _title_gen.get((lib, folder), 0)


def _detail_max_age(lib: str) -> float:
    """How long a memoized episode tree may be reused with no event for it.

    Libraries with an arr get a Connect webhook on every import and delete, so
    their trees are reused until that event (bounded by the re-list backstop).
    Libraries without one (YouTube) have no event, so TITLE_RESCAN_S applies.
    """
    if config.LIBRARIES.get(lib, {}).get("arr"):
        return config.LIBRARY_RELIST_S
    return config.TITLE_RESCAN_S


def title_detail(lib: str, folder: str) -> TitleDetail | None:
    """scan_title_detail for background builders, memoized per title.

    Rescans when that title had an event or a re-list saw it change, or when
    the memo is older than _detail_max_age. Request handlers that show or
    delete a title call scan_title_detail directly so they always see the live
    tree.
    """
    from synclet.scan import scan_title_detail  # local to avoid cycle

    key = (lib, folder)
    version = title_version(lib, folder)
    now = time.monotonic()
    with _lock:
        hit = _detail_memo.get(key)
    if hit is not None and hit[0] == version and now - hit[1] < _detail_max_age(lib):
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
    """Listing times and sizes, for /api/library, the refresh button and deploy checks.

    `listed_at` / `failed_at` are epoch seconds on the server clock, so a
    client can tell whether a listing finished (or failed) after it asked.
    """
    _load()
    now = time.time()
    with _lock:
        return {
            "relist_interval_s": config.LIBRARY_RELIST_S,
            "libraries": {
                lib: {
                    "titles": len(_folders[lib]) if lib in _folders else None,
                    "listed_at": _listed_at.get(lib),
                    "failed_at": _failed_at.get(lib),
                    "age_s": round(now - _listed_at[lib])
                    if lib in _listed_at
                    else None,
                    "listing": lib in _busy_libs,
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
        _failed_at.clear()
        _busy_libs.clear()
        _retry_at.clear()
        _events_during_listing.clear()
        _title_gen.clear()
        _applied_seq.clear()
        _detail_memo.clear()
        _listeners.clear()
        _loaded = False
