"""Keep synced copies current when the library file behind them changes.

WHY. Synclet COPIES library files into the Syncthing folder. DO NOT swap the
copy for a hardlink or symlink to keep it current: Syncthing does not support
hard links, so a replaced library file is never picked up that way (Jake,
2026-10-22). A copy is a snapshot, though. When an arr upgrades a title (new
release, new file name) or Tdarr rewrites a file in place, the synced copy kept
serving the old file. In the case that prompted this, a synced movie stayed on a
broken video-less file for days after the library got a good replacement.

WHAT. A synced title is refreshed item by item. An item is a movie, or one
episode that has a synced video (a multi-episode file belongs to every episode
it holds). For each item:
  - the library it comes from is the one library sharing its sync folder that
    has a video for it; when two do (tv and tv-4kUHD), the one holding a file
    by a synced video's name; otherwise the item is left alone,
  - when a video or subtitle differs (its synced copy is missing, a different
    size, or older than the library file, or a synced one is superseded), every
    wanted library file for the item that differs is copied (scan.is_wanted_file,
    the filter a sync copies with), sidecars included; a sidecar alone never
    starts a refresh,
  - a synced video or subtitle the library no longer has is superseded and is
    removed, but only after every copy for each item it holds has landed.
A refresh never adds an episode the user did not sync, and never touches an
item whose library video is gone (the offline copy is all that is left of it).
Files in a show's folder with no episode in their name belong to no item and
are never touched.

WHEN. One planner, three triggers:
  - an arr Connect Download/Upgrade, Rename or ImportComplete event for the
    title (main.api_arr_webhook), right away;
  - a background check each full cache pass, waiting REFRESH_SETTLE_S after a
    library file last changed, and starting one refresh at a time;
  - POST /api/synced/refresh for one title, now.
Each job re-plans once it holds the title's folder (sync_ops.launch), so a
plan made while another job was copying or unsyncing is never acted on.

COST. A refresh writes the new file into the synced folder on the array, and
Syncthing then sends it to every device (a device reuses blocks it already has
from the old file, so a rename or a small header edit moves little over the
network). Every refresh logs what it copies.
"""

from __future__ import annotations

import asyncio
import re
import stat
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
from synclet.episodes import episode_numbers
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
# Change detection compares size, then mtime in nanoseconds: copies keep the
# library mtime (shutil.copy2), measured ns-exact on every same-name pair on
# Tower (275 of 275, 2026-10-22), so a library file with a NEWER mtime may have
# new bytes under the same name and size. DO NOT compare for plain inequality:
# a copy made by something that did not keep the mtime would be re-copied for
# nothing. A rewrite that keeps both size and mtime is invisible without
# hashing every file, which a background pass over the share cannot afford.
# Matches a 4K release name. Arr naming puts the resolution in every file name.
_UHD = re.compile(r"2160p|\b4k\b|\buhd\b", re.IGNORECASE)
# synced folder -> when its last refresh failed. The background pass leaves such
# a title alone for REFRESH_RETRY_S: a copy that keeps failing (a full disk)
# must not be retried as a multi-gigabyte copy on every pass. Webhooks and the
# on-demand route mean something changed or someone asked, so they retry.
_failed_at: dict[str, float] = {}


class _StaleMemo(Exception):  # noqa: N818 (control flow, not an error report)
    """A memoized library tree names a file that is gone; re-plan live."""


@dataclass
class RefreshPlan:
    """What a refresh of one synced title would copy and remove.

    Each copy and removal names the `items` it serves ("movie", "S01E02"), and
    `skipped` says why a synced item is left as it is.
    """

    sync_sub: str
    folder: str
    libs: list[str] = field(default_factory=list)
    copies: list[dict] = field(default_factory=list)
    removals: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    @property
    def needed(self) -> bool:
        return bool(self.copies or self.removals)

    @property
    def lib(self) -> str:
        """The library to name jobs after: the first owner, else any sharing."""
        return (self.libs or config.libs_for_sync_sub(self.sync_sub))[0]

    @property
    def job_title(self) -> str:
        return sync_ops.job_title(self.lib, self.folder)

    def to_dict(self) -> dict:
        return {
            "sync_sub": self.sync_sub,
            "folder": self.folder,
            "libs": self.libs,
            "needed": self.needed,
            "copy_bytes": sum(c["size"] for c in self.copies),
            "copies": self.copies,
            "removals": self.removals,
            "skipped": self.skipped,
        }


def _label(season: int, episode: int) -> str:
    return f"S{season:02d}E{episode:02d}"


def _items_of(name: str, *, movie: bool) -> list[str]:
    if movie:
        return [_MOVIE]
    return [_label(*se) for se in sorted(episode_numbers(name))]


def _is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def _source_items(detail: TitleDetail) -> dict[str, list[Path]]:
    """{item: the library's wanted files for it}, from a title scan."""
    if detail.kind == "movie":
        return {_MOVIE: [Path(f["path"]) for f in detail.files]}
    out: dict[str, list[Path]] = {}
    for season in detail.seasons:
        for ep in season.episodes:
            for p in map(Path, ep.files):
                for item in _items_of(p.name, movie=False):
                    out.setdefault(item, []).append(p)
    return out


def _file_stat(path: Path) -> tuple[int, int] | None:
    """(size, mtime_ns) of a regular file; None if missing or not a regular file."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_size, st.st_mtime_ns) if stat.S_ISREG(st.st_mode) else None


def _owner(
    item: str,
    synced_videos: set[str],
    sources: dict[str, dict[str, list[Path]]],
    *,
    shared: bool,
) -> tuple[str | None, str]:
    """(library this synced item came from, why not when None).

    Two libraries share each sync folder (tv and tv-4kUHD, movies and
    movies-4kUHD), and an item can be in both. DO NOT take the first library
    that has the title: refreshing a 4K copy from the HD library swaps the
    quality the user chose and re-sends the whole file. Only libraries with a
    video for this item count. With two, the owner is the one holding a file by
    a synced VIDEO's name (subtitles are named alike across releases, so they
    prove nothing); if an upgrade renamed it, the item is left alone. With one,
    it is the owner, since an upgrade renames the file and a name match cannot
    be required, unless the folder is `shared` and the synced video and the
    library's differ in being 4K: then the synced copy came from the other
    library, whose file is gone, and it is kept as the offline copy.
    """
    candidates = [
        lib
        for lib, items in sources.items()
        if any(_is_video(p) for p in items.get(item, []))
    ]
    if not candidates:
        return None, "no library video; offline copy kept"
    if len(candidates) == 1:
        lib = candidates[0]
        source_videos = [p.name for p in sources[lib][item] if _is_video(p)]
        if (
            shared
            and not synced_videos & set(source_videos)
            and {bool(_UHD.search(n)) for n in synced_videos}
            != {bool(_UHD.search(n)) for n in source_videos}
        ):
            return None, (
                f"synced copy and {lib} differ in 4K; the library it came "
                "from no longer has it, offline copy kept"
            )
        return lib, ""
    by_name = [
        lib
        for lib in candidates
        if any(p.name in synced_videos for p in sources[lib][item] if _is_video(p))
    ]
    if len(by_name) == 1:
        return by_name[0], ""
    return None, (
        f"in {' and '.join(candidates)}; cannot tell which one the synced copy "
        "came from"
    )


def _synced_items(lib: str, folder: str, *, movie: bool) -> dict[Path, list[str]]:
    """{synced video or subtitle: the items it holds}, for items with a video."""
    synced = {
        f: items
        for f in sync_ops.synced_title_files(lib, folder)
        if f.suffix.lower() in _MEDIA_EXTS
        if (items := _items_of(f.name, movie=movie))
    }
    with_video = {i for f, items in synced.items() if _is_video(f) for i in items}
    return {
        f: items for f, items in synced.items() if any(i in with_video for i in items)
    }


def _plan(
    sync_sub: str, folder: str, *, live: bool, settle_s: float, now: float
) -> RefreshPlan:
    plan = RefreshPlan(sync_sub=sync_sub, folder=folder)
    libs = config.libs_for_sync_sub(sync_sub)
    if not libs or not library.valid_folder(libs[0], folder):
        return plan
    movie = LIBRARIES[libs[0]]["kind"] == "movie"
    synced = _synced_items(libs[0], folder, movie=movie)
    if not synced:
        return plan
    items = sorted({i for its in synced.values() for i in its})

    detail_of: Callable[[str, str], TitleDetail | None] = (
        scan_title_detail if live else library.title_detail
    )
    sources: dict[str, dict[str, list[Path]]] = {}
    for lib in libs:
        detail = detail_of(lib, folder)
        if detail is not None:
            sources[lib] = _source_items(detail)

    copies: dict[Path, dict] = {}
    wanted: set[Path] = set()
    skipped: dict[str, str] = {}
    for item in items:
        videos = {f.name for f, its in synced.items() if item in its and _is_video(f)}
        lib, why = _owner(item, videos, sources, shared=len(libs) > 1)
        if lib is None:
            skipped[item] = why
            continue
        extra = {
            other
            for p in sources[lib][item]
            for other in _items_of(p.name, movie=movie)
            if other not in items
        }
        if extra:
            # The library merged this episode into a file with others the user
            # did not sync (S01E02 is now S01E01E02); copying it would add them.
            skipped[item] = (
                f"library file also holds {', '.join(sorted(extra))}, "
                "which is not synced"
            )
            continue
        src_stats: dict[Path, tuple[int, int]] = {}
        for src in sources[lib][item]:
            st = _file_stat(src)
            if st is None:
                if not live:
                    raise _StaleMemo(str(src))
                skipped[item] = "library file vanished while planning; try again"
                break
            src_stats[src] = st
        if item in skipped:
            continue
        age = now - max(mtime for _size, mtime in src_stats.values()) / 1e9
        if 0 <= age < settle_s:
            skipped[item] = (
                f"library file changed {int(age)}s ago; waiting for it to settle"
            )
            continue
        if lib not in plan.libs:
            plan.libs.append(lib)
        for src, (size, mtime) in src_stats.items():
            dst = sync_ops.sync_dest(src, lib)
            wanted.add(dst)
            have = _file_stat(dst)
            if have is None or have[0] != size or mtime > have[1]:
                copies.setdefault(
                    dst,
                    {
                        "items": [],
                        "lib": lib,
                        "src": str(src),
                        "dst": str(dst),
                        "size": size,
                        "is_video": _is_video(src),
                    },
                )["items"].append(item)

    plan.skipped = [{"item": i, "reason": r} for i, r in sorted(skipped.items())]
    for f, its in sorted(synced.items()):
        # A file holding any skipped item stays: removing it would remove that
        # item's offline copy (e.g. S01E01E02 when only E01 can be refreshed).
        if f in wanted or any(i in skipped for i in its):
            continue
        plan.removals.append(
            {"items": its, "path": str(f), "size": (_file_stat(f) or (0, 0))[0]}
        )
    # Sidecars (.nfo, -thumb.jpg, _chapters.xml) ride along with an item's
    # refresh but never start one. DO NOT let a sidecar alone mark an item
    # changed: Jellyfin and Tdarr rewrite them routinely, so every metadata
    # pass would re-send them and keep the background check busy for nothing.
    changed = {i for r in plan.removals for i in r["items"]}
    changed |= {
        i
        for c in copies.values()
        if Path(c["dst"]).suffix.lower() in _MEDIA_EXTS
        for i in c["items"]
    }
    plan.copies = [c for c in copies.values() if any(i in changed for i in c["items"])]
    return plan


def plan_refresh(
    sync_sub: str,
    folder: str,
    *,
    live: bool = True,
    settle_s: float = 0,
    now: float | None = None,
) -> RefreshPlan:
    """What refreshing the synced title `sync_sub/folder` would do. Reads only.

    `live` scans the library title now (request, webhook and job paths);
    otherwise the memoized tree from library.title_detail lists the files
    (background check). Either way the library files of synced items are
    stat'ed now, so an in-place rewrite is seen even when no event bumped the
    memo, and a memo naming a file that is gone falls back to a live scan. An
    item whose library files changed less than `settle_s` ago is skipped.
    """
    now = time.time() if now is None else now
    try:
        return _plan(sync_sub, folder, live=live, settle_s=settle_s, now=now)
    except _StaleMemo:
        return _plan(sync_sub, folder, live=True, settle_s=settle_s, now=now)


async def _execute(job: sync_ops.Job, plan: RefreshPlan) -> dict[str, str]:
    """Copy, then remove what the copies made obsolete. Returns {item: why} failed.

    A removal waits on every item it holds: if any copy for one of them failed,
    the superseded file stays, so a failed refresh never leaves an item with no
    offline copy. A removal that fails is reported and the others still run.
    """
    failed: dict[str, str] = {}
    for c in plan.copies:
        src, dst = Path(c["src"]), Path(c["dst"])
        job.current_file = src.name
        try:
            await asyncio.to_thread(copy_into_place, src, dst)
        except OSError as exc:
            for item in c["items"]:
                failed[item] = f"{src.name}: {exc}"
        job.processed_files += 1
        if c["is_video"]:
            job.processed_media_files += 1
        job.processed_bytes += c["size"]
    not_copied = set(failed)
    for r in plan.removals:
        if any(item in not_copied for item in r["items"]):
            continue
        path = Path(r["path"])
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError:
            pass
        except OSError as exc:
            for item in r["items"]:
                failed[item] = f"removing {path.name}: {exc}"
    return failed


def _log_start(job: sync_ops.Job, plan: RefreshPlan) -> None:
    items = {i for c in plan.copies for i in c["items"]}
    items |= {i for r in plan.removals for i in r["items"]}
    logger.info(
        "refresh %s: copying %d files (%.1f MB written to the synced folder and "
        "sent to every Syncthing device), removing %d superseded, for %s",
        job.title,
        len(plan.copies),
        sum(c["size"] for c in plan.copies) / 1e6,
        len(plan.removals),
        ", ".join(sorted(items)),
    )


async def _run_refresh(
    job: sync_ops.Job, sync_sub: str, folder: str, settle_s: float
) -> None:
    job.status = "running"
    job.started_at = time.time()
    plan = RefreshPlan(sync_sub=sync_sub, folder=folder)
    try:
        plan = await asyncio.to_thread(
            plan_refresh, sync_sub, folder, settle_s=settle_s
        )
        sync_ops.set_items(job, plan.copies)
        if plan.needed:
            _log_start(job, plan)
        failed = await _execute(job, plan)
        # Items keep their identity (a movie, or SxxExx), so the snapshot holds
        # them already; adding covers an item the snapshot missed.
        await asyncio.to_thread(
            pending_mod.add_keys,
            {
                key
                for c in plan.copies
                if c["is_video"] and not any(i in failed for i in c["items"])
                if (key := pending_mod.path_to_snapshot_key(Path(c["dst"])))
            },
        )
        job.status = "error" if failed else "done"
        job.error = "; ".join(f"{i}: {why}" for i, why in sorted(failed.items()))
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
    finally:
        job.ended_at = time.time()
        if job.status == "error":
            _failed_at[folder] = job.ended_at
            logger.warning("refresh %s failed: %s", job.title, job.error)
        else:
            _failed_at.pop(folder, None)
            if plan.needed:
                logger.info(
                    "refresh %s done: %d files copied, %d superseded removed",
                    job.title,
                    len(plan.copies),
                    len(plan.removals),
                )
        state_mod.invalidate()
        maint_cache.invalidate()


def start_refresh(plan: RefreshPlan, *, settle_s: float = 0) -> sync_ops.Job | None:
    """Queue a refresh job for the title of `plan`. Call on the event loop.

    `plan` only decides whether to start: the job re-plans once it holds the
    folder. None when there is nothing to do. A refresh already queued for the
    folder is returned instead of queueing a second one, since it will see
    whatever has changed by the time it runs.
    """
    if not plan.needed:
        return None
    for job in sync_ops.active_jobs_on_folder(plan.lib, plan.folder):
        if job.op == "refresh" and job.status == "queued":
            return job
    job = sync_ops.new_job("refresh", plan.job_title, plan.copies)
    return sync_ops.launch(
        job, lambda j: _run_refresh(j, plan.sync_sub, plan.folder, settle_s)
    )


async def refresh_title(sync_sub: str, folder: str, *, settle_s: float = 0) -> dict:
    """Plan (off the loop) and queue a refresh of one synced title.

    The one entry point the webhook, the on-demand route and the background
    pass share. Returns the plan as of now plus the job id, if one was queued.
    """
    plan = await asyncio.to_thread(plan_refresh, sync_sub, folder, settle_s=settle_s)
    job = start_refresh(plan, settle_s=settle_s)
    return {**plan.to_dict(), "job_id": job.id if job else None}


def _find_stale_uncached() -> list[dict]:
    """Plans for every synced title that differs from the library or is skipped.

    Lists library files from the memoized trees (library.title_detail) and
    stats only the files of synced items, so a pass costs a walk of the synced
    folder plus a few dozen stats, and a library scan only for titles that had
    an event or aged out of the memo. One title failing to plan (a share error)
    is logged and left out; the others are still checked.
    """
    out = []
    for sub_path, item in iter_synced_titles():
        try:
            plan = plan_refresh(
                sub_path.name, item.name, live=False, settle_s=config.REFRESH_SETTLE_S
            )
        except OSError as exc:
            logger.warning("refresh check of %s failed: %s", item.name, exc)
            continue
        if plan.needed or plan.skipped:
            out.append(plan.to_dict())
    return out


def find_stale() -> list[dict]:
    """Cached wrapper: see _find_stale_uncached."""
    return maint_cache.get_cached(_STALE_KEY, _find_stale_uncached)


def register_builders() -> None:
    maint_cache.register(_STALE_KEY, _find_stale_uncached)


async def start_due_refreshes() -> list[str]:
    """Start a refresh for the first title the background check found stale.

    Called by the cache loop after a full pass. One at a time: a refresh can be
    gigabytes, and several at once would contend for the array Plex streams
    from. Nothing starts while any refresh is queued or running. The job
    re-plans live before it copies or removes anything, so an out-of-date check
    never drives a change by itself. Returns the job title started, if any.
    """
    if any(job.op == "refresh" for job in sync_ops.active_jobs()):
        return []
    present, stale = maint_cache.peek(_STALE_KEY, _find_stale_uncached)
    now = time.time()
    for entry in stale if present else []:
        if not entry["needed"]:
            continue
        failed_at = _failed_at.get(entry["folder"])
        if failed_at is not None and now - failed_at < config.REFRESH_RETRY_S:
            continue
        result = await refresh_title(
            entry["sync_sub"], entry["folder"], settle_s=config.REFRESH_SETTLE_S
        )
        job = sync_ops.get_job(result["job_id"]) if result["job_id"] else None
        if job is not None:
            return [job.title]
    return []
