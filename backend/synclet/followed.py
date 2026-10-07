"""Persist the user's intent to follow episodic titles across empty sync folders."""

from __future__ import annotations

import json
import os
import threading
import time

from synclet import config, maint_cache
from synclet.fs_helpers import iter_synced_titles

_lock = threading.RLock()
_PRESENCE_KEY = "followed_presence"


def _valid_key(lib: str, folder: str) -> bool:
    return (
        lib in config.LIBRARIES
        and config.LIBRARIES[lib]["kind"] in ("show", "youtube")
        and folder not in ("", ".", "..")
        and not folder.startswith(".")
        and "/" not in folder
        and "\\" not in folder
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
            for lib, info in config.LIBRARIES.items()
            if info["sync_sub"] == sub.name and _valid_title(lib, title.name)
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


def _build_source_presence() -> dict[tuple[str, str], tuple[float, bool]]:
    """Scan each source library once, away from title-detail request latency."""
    items = get_followed()
    names_by_lib: dict[str, set[str]] = {}
    for lib, _folder in items:
        if lib in names_by_lib:
            continue
        try:
            with os.scandir(config.MEDIA_ROOT / lib) as entries:
                names: set[str] = set()
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            names.add(entry.name)
                    except OSError:
                        # One vanishing entry must not hide every other title.
                        continue
                names_by_lib[lib] = names
        except OSError:
            # Hide unavailable titles, but keep the saved intent so a later
            # full refresh can show them again when the source mount returns.
            names_by_lib[lib] = set()
    return {key: (at, key[1] in names_by_lib[key[0]]) for key, at in items.items()}


def get_display_followed() -> dict[tuple[str, str], float]:
    """Hide externally moved titles after background validation, keeping intent.

    A new Follow gesture appears immediately even if validation is still
    rebuilding. The full refresh picks up a source folder that returns later.
    """
    items = get_followed()
    ready, presence = maint_cache.peek(_PRESENCE_KEY, _build_source_presence)
    if not ready:
        return items
    return {
        key: at
        for key, at in items.items()
        if key not in presence or presence[key][0] != at or presence[key][1]
    }


def register_presence_builder() -> None:
    maint_cache.register(_PRESENCE_KEY, _build_source_presence)


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
    maint_cache.invalidate("synced_enrichment")
    maint_cache.invalidate(_PRESENCE_KEY)
    return following
