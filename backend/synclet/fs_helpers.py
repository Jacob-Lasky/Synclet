"""Filesystem helpers shared across modules.

Centralises iteration patterns that were previously duplicated in
sync_ops, state, and the api_synced route.
"""

from __future__ import annotations

import contextlib
import os
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


def synced_title_sizes() -> dict[str, int]:
    """Return {title_dir_name: total_bytes} across every synced title.

    Single os.walk per sync-sub instead of one rglob+stat per title — same
    big-O but ~half the wall time on shfs FUSE because scandir-backed walks
    avoid repeated Path allocations and reuse stat buffers within a
    directory. Used by /api/synced; result is owned by maint_cache's TTL
    so the walk only fires once per cache window.

    Key is the title's directory name (matches `iter_synced_titles()`'s
    second tuple element so callers can join by name without an extra
    Path comparison). Returns 0 / missing for any title where every stat
    failed; OSErrors are swallowed per-file to match the prior behavior.
    """
    out: dict[str, int] = {}
    for sub_path in iter_sync_subs():
        try:
            top_level_entries = list(os.scandir(sub_path))
        except OSError:
            continue
        for top_entry in top_level_entries:
            if not top_entry.is_dir(follow_symlinks=False):
                continue
            if top_entry.name.startswith("."):
                continue
            total = 0
            # os.walk uses scandir internally and emits (dirpath, dirnames,
            # filenames). One walk per title beats rglob+stat per file
            # because scandir reuses inode buffers across siblings on the
            # same directory — material on shfs FUSE where the inode lookup
            # is the dominant cost.
            for dirpath, _dirnames, filenames in os.walk(top_entry.path):
                dir_path_obj = Path(dirpath)
                for name in filenames:
                    with contextlib.suppress(OSError):
                        total += (dir_path_obj / name).stat().st_size
            out[top_entry.name] = total
    return out
