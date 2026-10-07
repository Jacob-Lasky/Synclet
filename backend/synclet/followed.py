"""Persist the user's intent to follow episodic titles across empty sync folders."""

from __future__ import annotations

import json
import threading
import time

from synclet import config, maint_cache
from synclet.fs_helpers import iter_synced_titles

_lock = threading.RLock()


def _valid_title(lib: str, folder: str) -> bool:
    return (
        lib in config.LIBRARIES
        and config.LIBRARIES[lib]["kind"] in ("show", "youtube")
        and folder not in ("", ".", "..")
        and not folder.startswith(".")
        and "/" not in folder
        and "\\" not in folder
        and (config.MEDIA_ROOT / lib / folder).is_dir()
    )


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
    """Return followed titles still present in the source library."""
    with _lock:
        return {key: at for key, at in _read_all().items() if _valid_title(*key)}


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
    return following
