"""Filesystem helpers shared across modules.

Centralises iteration patterns that were previously duplicated in
sync_ops, state, and the api_synced route.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from synclet.config import LIBRARIES, SYNC_ROOT


def iter_sync_subs() -> Iterator[Path]:
    """Yield each unique SYNC_ROOT/<sub>/ that backs at least one library.

    Multiple libraries can share a sync sub (e.g. tv and tv-4kUHD both go to
    SYNC_ROOT/tv/). This yields each sub once, in LIBRARIES iteration order.
    Only existing directories are yielded.
    """
    seen: set[str] = set()
    for info in LIBRARIES.values():
        sub = info["sync_sub"]
        if sub in seen:
            continue
        seen.add(sub)
        sub_path = SYNC_ROOT / sub
        if sub_path.exists():
            yield sub_path


def iter_synced_titles() -> Iterator[tuple[Path, Path]]:
    """Yield (sub_path, title_dir) for every synced title directory.

    Skips dotfiles and non-directories. Order is sub-major, then directory
    name within each sub (sorted).
    """
    for sub_path in iter_sync_subs():
        for item in sorted(sub_path.iterdir()):
            if not item.is_dir() or item.name.startswith("."):
                continue
            yield sub_path, item
