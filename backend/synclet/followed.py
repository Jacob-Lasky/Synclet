"""Persist the user's intent to follow episodic titles across empty sync folders."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable

from synclet import config, library
from synclet.fs_helpers import iter_synced_titles

_lock = threading.RLock()
# Called after follow intent changes; synced registers its enrichment
# invalidation here so this module never names another module's cache key.
_listeners: list[Callable[[], None]] = []


def subscribe(callback: Callable[[], None]) -> None:
    """Run `callback` after every follow or unfollow. Idempotent per callback."""
    with _lock:
        if callback not in _listeners:
            _listeners.append(callback)


def _valid_key(lib: str, folder: str) -> bool:
    return library.valid_folder(lib, folder) and config.LIBRARIES[lib]["kind"] in (
        "show",
        "youtube",
    )


def _valid_title(lib: str, folder: str) -> bool:
    return _valid_key(lib, folder) and (config.MEDIA_ROOT / lib / folder).is_dir()


def _write(items: dict[tuple[str, str], float]) -> None:
    path = config.FOLLOWED_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "items": [
            {"lib": lib, "folder": folder, "followed_at": at}
            for (lib, folder), at in sorted(items.items())
        ],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def _bootstrap() -> dict[tuple[str, str], float]:
    """Seed existing episodic offline titles once when upgrading older installs."""
    items: dict[tuple[str, str], float] = {}
    for sub, title in iter_synced_titles():
        candidates = [
            lib
            for lib in config.libs_for_sync_sub(sub.name)
            if _valid_title(lib, title.name)
        ]
        # Shared sync folders cannot identify which source library was copied.
        # Leave ambiguous titles for an explicit Follow gesture.
        if len(candidates) == 1:
            items[candidates[0], title.name] = title.stat().st_mtime
    _write(items)
    return items


def _read_all() -> dict[tuple[str, str], float]:
    path = config.FOLLOWED_FILE
    if not path.exists():
        return _bootstrap()
    raw = json.loads(path.read_text())
    if raw.get("version") != 1 or not isinstance(raw.get("items"), list):
        raise ValueError("unsupported followed.json format")
    return {
        (item["lib"], item["folder"]): float(item["followed_at"])
        for item in raw["items"]
    }


def get_followed() -> dict[tuple[str, str], float]:
    """Return followed intent without statting every source folder.

    Source existence is checked when following starts. A slow source mount must
    not hold up every title-detail request because another followed show lives
    on that mount. An explicit source delete removes its follow entry.
    """
    with _lock:
        return {key: at for key, at in _read_all().items() if _valid_key(*key)}


def get_display_followed() -> dict[tuple[str, str], float]:
    """Hide titles the library listing says are gone, keeping the saved intent.

    Presence comes from synclet.library, which never lists on this path, so a
    slow source mount cannot hold up the Synced tab. A library with no listing
    yet shows every follow. A title that returns is shown again as soon as the
    listing records it (arr webhook, Follow gesture or the backstop re-list).
    """
    return {
        key: at for key, at in get_followed().items() if library.peek(*key) is not False
    }


def set_following(lib: str, folder: str, following: bool) -> bool:
    """Set follow intent and flag the expensive new-episode hints for refresh."""
    if following and not _valid_title(lib, folder):
        raise ValueError("title must be an existing show or YouTube channel")
    with _lock:
        items = _read_all()
        key = (lib, folder)
        if following:
            if key in items:
                return True
            items[key] = time.time()
        else:
            if key not in items:
                return False
            del items[key]
        _write(items)
    if following:
        # _valid_title just confirmed the folder, so the listing can record it
        # without another stat on the share.
        library.record_present(lib, folder)
    with _lock:
        listeners = list(_listeners)
    for callback in listeners:
        callback()
    return following
