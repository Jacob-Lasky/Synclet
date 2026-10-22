"""Keep synced copies current when the library file behind them changes.

WHY. Synclet COPIES library files into the Syncthing folder. DO NOT swap the
copy for a hardlink or symlink to keep it current: Syncthing does not support
hard links, so a replaced library file is never picked up that way (Jake,
2026-10-22). A copy is a snapshot, though. When an arr upgrades a title (new
release, new file name) or Tdarr rewrites a file in place (same name, new
size), the synced copy kept serving the old file. In the case that prompted
this, a synced movie stayed on a broken video-less file for days after the
library got a good replacement.

WHAT. For each synced item (a movie, or one SxxExx episode that is on the
devices), compare the synced files with the library's wanted files for the same
item (scan.is_wanted_file, the same filter a sync copies with):
  - a library file whose synced copy is missing or a different size is copied,
  - a synced video or subtitle the library no longer has is superseded and is
    removed, but only once every copy for that item has landed at full size.
Only items already on the devices are refreshed: a refresh never adds an
episode the user did not sync, and never touches an item whose library video
is gone (the offline copy is all that is left of it).

WHEN. One planner, three triggers:
  - an arr Connect Download/Upgrade, Rename or ImportComplete event for the
    title (main.api_arr_webhook), right away;
  - a background check each full cache pass, from the memoized episode trees
    (library.title_detail, rescanned on every arr event), waiting
    REFRESH_SETTLE_S after a library file last changed;
  - POST /api/synced/refresh for one title, now.

COST. A refresh re-sends the new file to every Syncthing device. That is the
point, but it can be gigabytes, so every refresh logs what it will copy.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from common.log_utils import get_logger
from synclet import config, library, maint_cache, sync_ops
from synclet import pending as pending_mod
from synclet import state as state_mod
from synclet.config import LIBRARIES, SUBTITLE_EXTS, VIDEO_EXTS
from synclet.fs_helpers import copy_into_place, iter_synced_titles
from synclet.scan import scan_title_detail

if TYPE_CHECKING:
    from synclet.scan import TitleDetail

logger = get_logger(__name__)

# Arr Connect events after which a title's library files may differ from its
# synced copy. Download covers upgrades (isUpgrade); Rename follows Tdarr's
# re-encodes; ImportComplete is Sonarr's once-per-import summary. A file-delete
# event alone is NOT one: the synced copy is the user's offline copy, and the
# Download that follows an upgrade's delete carries the refresh.
REFRESH_EVENTS = frozenset({"Download", "Rename", "ImportComplete"})

_MEDIA_EXTS = VIDEO_EXTS | SUBTITLE_EXTS
_MOVIE = "movie"
_STALE_KEY = "stale"
# job title -> when its last refresh failed. The background pass leaves such a
# title alone for REFRESH_RETRY_S: a copy that keeps failing (a full disk) must
# not be retried as a multi-gigabyte copy on every pass. Webhooks and the
# on-demand route still retry at once.
_failed_at: dict[str, float] = {}


@dataclass(frozen=True)
class _SourceFile:
    path: Path
    size: int
    mtime: float


@dataclass
class RefreshPlan:
    """What a refresh of one synced title would copy and remove.

    `lib` is the library the synced copy comes from (None when that cannot be
    told). Each copy and removal names its `item` ("movie" or "S01E02"), and
    `skipped` says why an item that is synced is left as it is.
    """

    sync_sub: str
    folder: str
    lib: str | None = None
    copies: list[dict] = field(default_factory=list)
    removals: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    @property
    def needed(self) -> bool:
        return bool(self.copies or self.removals)

    @property
    def job_title(self) -> str:
        # Same "<lib>/<folder>" shape as sync and unsync jobs, so the source
        # delete guard (sync_ops.has_active_job_for_title) sees a refresh too.
        return f"{self.lib}/{self.folder}"

    def to_dict(self) -> dict:
        return {
            "sync_sub": self.sync_sub,
            "folder": self.folder,
            "lib": self.lib,
            "needed": self.needed,
            "copy_bytes": sum(c["size"] for c in self.copies),
            "copies": self.copies,
            "removals": self.removals,
            "skipped": self.skipped,
        }


def _label(season: int, episode: int) -> str:
    return f"S{season:02d}E{episode:02d}"


def _synced_items(lib: str, folder: str) -> dict[str, list[Path]]:
    """{item: synced video and subtitle files} for items with a synced video."""
    items: dict[str, list[Path]] = {}
    movie = LIBRARIES[lib]["kind"] == "movie"
    for f in sync_ops.synced_title_files(lib, folder):
        if f.suffix.lower() not in _MEDIA_EXTS:
            continue
        se = None if movie else sync_ops.file_se(f)
        if not movie and se is None:
            continue
        items.setdefault(_MOVIE if se is None else _label(*se), []).append(f)
    return {
        item: files
        for item, files in items.items()
        if any(f.suffix.lower() in VIDEO_EXTS for f in files)
    }


def _source_items(detail: TitleDetail) -> dict[str, list[_SourceFile]]:
    """{item: the library's wanted files for it}, from a title scan."""
    if detail.kind == "movie":
        return {
            _MOVIE: [
                _SourceFile(Path(f["path"]), f["size_bytes"], f["mtime"])
                for f in detail.files
            ]
        }
    return {
        _label(e.season, e.episode): [
            _SourceFile(Path(p), *e.file_stats[p]) for p in e.files
        ]
        for s in detail.seasons
        for e in s.episodes
    }


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return None


def _owner(
    sync_sub: str,
    folder: str,
    synced_names: set[str],
    detail_of: Callable[[str, str], TitleDetail | None],
) -> tuple[str | None, TitleDetail | None, str]:
    """(library the synced copy came from, its scan, why not when None).

    Two libraries share a sync folder (tv and tv-4kUHD, movies and
    movies-4kUHD), and a title can be in both. DO NOT just take the first
    library with the folder: refreshing a 4K copy from the HD library would
    swap the quality the user chose and re-send the whole title. With more than
    one candidate, the owner is the one library holding a file by a synced
    copy's name; if none or several do (an upgrade renamed the file), the
    title is left alone and reported.
    """
    candidates = [
        lib
        for lib in library.source_libs(folder)
        if LIBRARIES[lib]["sync_sub"] == sync_sub
    ]
    if not candidates:
        return None, None, "title is not in the library"
    details = {lib: detail_of(lib, folder) for lib in candidates}
    if len(candidates) == 1:
        lib = candidates[0]
        if details[lib] is None:
            return None, None, "title is not in the library"
        return lib, details[lib], ""
    matching = [
        lib
        for lib, detail in details.items()
        if detail is not None
        and any(
            sf.path.name in synced_names
            for files in _source_items(detail).values()
            for sf in files
        )
    ]
    if len(matching) == 1:
        return matching[0], details[matching[0]], ""
    return (
        None,
        None,
        f"title is in {' and '.join(candidates)}; cannot tell which one "
        "the synced copy came from",
    )


def plan_refresh(
    sync_sub: str,
    folder: str,
    *,
    live: bool = True,
    settle_s: float = 0,
    now: float | None = None,
) -> RefreshPlan:
    """What refreshing the synced title `sync_sub/folder` would do. Reads only.

    `live` scans the library title now (request and webhook paths); otherwise
    the memoized tree from library.title_detail is used (background pass). An
    item whose library files changed less than `settle_s` ago is skipped.
    """
    plan = RefreshPlan(sync_sub=sync_sub, folder=folder)
    any_lib = next(
        (lib for lib, info in LIBRARIES.items() if info["sync_sub"] == sync_sub),
        None,
    )
    if any_lib is None or not library.valid_folder(any_lib, folder):
        return plan
    synced = _synced_items(any_lib, folder)
    if not synced:
        return plan
    synced_names = {f.name for files in synced.values() for f in files}
    detail_of = scan_title_detail if live else library.title_detail
    lib, detail, why = _owner(sync_sub, folder, synced_names, detail_of)
    if lib is None or detail is None:
        plan.skipped = [{"item": item, "reason": why} for item in sorted(synced)]
        return plan
    plan.lib = lib
    sources = _source_items(detail)
    now = time.time() if now is None else now
    for item, synced_files in sorted(synced.items()):
        src = sources.get(item, [])
        if not any(sf.path.suffix.lower() in VIDEO_EXTS for sf in src):
            plan.skipped.append(
                {"item": item, "reason": "no library video; offline copy kept"}
            )
            continue
        newest = max(sf.mtime for sf in src)
        if now - newest < settle_s:
            plan.skipped.append(
                {
                    "item": item,
                    "reason": f"library file changed {int(now - newest)}s ago; "
                    "waiting for it to settle",
                }
            )
            continue
        wanted = {sync_ops.sync_dest(sf.path, lib): sf for sf in src}
        for dst, sf in wanted.items():
            if _size(dst) != sf.size:
                plan.copies.append(
                    {
                        "item": item,
                        "src": str(sf.path),
                        "dst": str(dst),
                        "size": sf.size,
                        "is_video": sf.path.suffix.lower() in VIDEO_EXTS,
                    }
                )
        for f in synced_files:
            if f not in wanted:
                plan.removals.append(
                    {"item": item, "path": str(f), "size": _size(f) or 0}
                )
    return plan


async def _run_refresh(job: sync_ops.Job, plan: RefreshPlan) -> None:
    job.status = "running"
    job.started_at = time.time()
    failed: dict[str, str] = {}
    removed = 0
    try:
        for c in plan.copies:
            src, dst = Path(c["src"]), Path(c["dst"])
            job.current_file = src.name
            try:
                await asyncio.to_thread(copy_into_place, src, dst)
                # The library file can change under the copy (an import or
                # Tdarr still writing). A short copy must not stand in for the
                # file it replaces, so its superseded files stay.
                landed = await asyncio.to_thread(_size, dst)
                current = await asyncio.to_thread(_size, src)
                if landed != current:
                    failed[c["item"]] = f"{src.name} changed during the copy"
            except OSError as exc:
                failed[c["item"]] = f"{src.name}: {exc}"
            job.processed_files += 1
            if c["is_video"]:
                job.processed_media_files += 1
            job.processed_bytes += c["size"]
        for r in plan.removals:
            if r["item"] in failed:
                continue
            with contextlib.suppress(FileNotFoundError):
                await asyncio.to_thread(Path(r["path"]).unlink)
                removed += 1
        # Same item identity before and after (a movie, or SxxExx), so the
        # snapshot already holds it; adding is for an item the snapshot missed.
        await asyncio.to_thread(
            pending_mod.add_keys,
            {
                key
                for c in plan.copies
                if c["is_video"] and c["item"] not in failed
                if (key := pending_mod.path_to_snapshot_key(Path(c["dst"])))
            },
        )
        if failed:
            job.status = "error"
            job.error = "; ".join(f"{item}: {why}" for item, why in failed.items())
        else:
            job.status = "done"
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
    finally:
        job.ended_at = time.time()
        logger.info(
            "refresh %s: %s; copied %d of %d files, removed %d superseded",
            plan.job_title,
            job.status,
            job.processed_files,
            len(plan.copies),
            removed,
        )
        if job.error:
            _failed_at[plan.job_title] = job.ended_at
            logger.warning("refresh %s failed: %s", plan.job_title, job.error)
        else:
            _failed_at.pop(plan.job_title, None)
        state_mod.invalidate()
        maint_cache.invalidate()


def start_refresh(plan: RefreshPlan) -> sync_ops.Job | None:
    """Run `plan` as a background job. Call on the event loop.

    None when there is nothing to do, or when another job is already working
    on the title (the next trigger re-plans against whatever it left).
    """
    if not plan.needed or sync_ops.has_active_job_for_title(plan.job_title):
        return None
    copy_bytes = sum(c["size"] for c in plan.copies)
    logger.info(
        "refresh %s: copying %d files (%.1f MB, re-sent to every Syncthing "
        "device), removing %d superseded: %s",
        plan.job_title,
        len(plan.copies),
        copy_bytes / 1e6,
        len(plan.removals),
        ", ".join(
            sorted(
                {c["item"] for c in plan.copies} | {r["item"] for r in plan.removals}
            )
        ),
    )
    job = sync_ops.new_job("refresh", plan.job_title, plan.copies)
    return sync_ops.launch(job, lambda j: _run_refresh(j, plan))


async def refresh_title(
    sync_sub: str, folder: str, *, live: bool = True, settle_s: float = 0
) -> dict:
    """Plan (off the loop) and start a refresh of one synced title.

    The one entry point the webhook, the on-demand route and the background
    pass share.
    """
    plan = await asyncio.to_thread(
        plan_refresh, sync_sub, folder, live=live, settle_s=settle_s
    )
    job = start_refresh(plan)
    return {**plan.to_dict(), "job_id": job.id if job else None}


def _find_stale_uncached() -> list[dict]:
    """Plans for every synced title that differs from the library, or is skipped.

    Uses the memoized episode trees (library.title_detail), so a pass costs a
    walk of the synced folder plus a library scan only of titles that had an
    event or aged out of the memo.
    """
    out = []
    for sub_path, item in iter_synced_titles():
        plan = plan_refresh(
            sub_path.name, item.name, live=False, settle_s=config.REFRESH_SETTLE_S
        )
        if plan.needed or plan.skipped:
            out.append(plan.to_dict())
    return out


def find_stale() -> list[dict]:
    """Cached wrapper: see _find_stale_uncached."""
    return maint_cache.get_cached(_STALE_KEY, _find_stale_uncached)


def register_builders() -> None:
    maint_cache.register(_STALE_KEY, _find_stale_uncached)


async def start_due_refreshes() -> list[str]:
    """Start a refresh for each synced title the background check found stale.

    Called by the cache loop after a full pass. Each title is re-planned live
    before anything is copied, so a memo that is out of date never drives a
    copy or a removal by itself. Returns the titles a job was started for.
    """
    present, stale = maint_cache.peek(_STALE_KEY, _find_stale_uncached)
    started = []
    now = time.time()
    for entry in stale if present else []:
        if not entry["needed"]:
            continue
        failed_at = _failed_at.get(f"{entry['lib']}/{entry['folder']}")
        if failed_at is not None and now - failed_at < config.REFRESH_RETRY_S:
            continue
        result = await refresh_title(
            entry["sync_sub"], entry["folder"], settle_s=config.REFRESH_SETTLE_S
        )
        if result["job_id"]:
            started.append(f"{result['lib']}/{result['folder']}")
    return started
