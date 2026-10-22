"""Copy and remove operations + async job tracking.

The CLI does shutil.copy2 inline. We copy in a background asyncio task so the
HTTP handler returns immediately with a job_id and the client polls
/api/jobs/{id} for progress, and through fs_helpers.copy_into_place so a failed
copy never leaves a truncated file under its final name. Matches the CLI's file
selection (subtitles filter, mkdir -p parents) exactly.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

from synclet import pending as pending_mod
from synclet import state as state_mod
from synclet.config import LIBRARIES, MEDIA_ROOT, SYNC_ROOT, VIDEO_EXTS
from synclet.episodes import EPISODE_PAT
from synclet.fs_helpers import copy_into_place, iter_sync_subs, iter_synced_titles
from synclet.maint_cache import invalidate as _invalidate_maint_cache
from synclet.scan import is_wanted_file, scan_title_detail


@dataclass
class Job:
    id: str
    op: str  # "sync" | "unsync" | "refresh"
    status: str  # "queued" | "running" | "done" | "error"
    total_files: int = 0  # every file (videos + subs + chapters)
    total_media_files: int = 0  # video files only , what humans care about
    processed_files: int = 0
    processed_media_files: int = 0
    processed_bytes: int = 0
    total_bytes: int = 0
    current_file: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    error: str = ""
    title: str = ""  # display name for the job
    items: list[dict] = field(default_factory=list)  # [{src, dst, size, is_video}]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "op": self.op,
            "status": self.status,
            "total_files": self.total_files,
            "total_media_files": self.total_media_files,
            "processed_files": self.processed_files,
            "processed_media_files": self.processed_media_files,
            "processed_bytes": self.processed_bytes,
            "total_bytes": self.total_bytes,
            "current_file": self.current_file,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "error": self.error,
            "title": self.title,
        }


def _is_video(path_str: str) -> bool:
    return PurePath(path_str).suffix.lower() in VIDEO_EXTS


_JOBS: dict[str, Job] = {}

# DO NOT use `asyncio.create_task(coro)` directly without retaining the task.
# Python only holds a weakref to the running task, so a bare create_task call
# can be garbage-collected mid-execution, silently aborting the job. Tasks
# stored here are discarded by the done-callback once they finish.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def _spawn_background(coro: Coroutine[Any, Any, None]) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


def job_title(lib: str, folder: str) -> str:
    """A job's title: `<lib>/<folder>`. Folder names never contain "/"."""
    return f"{lib}/{folder}"


def _job_folder(title: str) -> str:
    return title.rpartition("/")[2]


def set_items(job: Job, items: list[dict]) -> None:
    """Give `job` its items ({dst, size, is_video, ...}) and their totals."""
    job.items = items
    job.total_files = len(items)
    job.total_media_files = sum(1 for i in items if i["is_video"])
    job.total_bytes = sum(i["size"] for i in items)


def new_job(op: str, title: str, items: list[dict]) -> Job:
    """A queued job over `items`, totals filled in."""
    job = Job(id=uuid.uuid4().hex[:12], op=op, status="queued", title=title)
    set_items(job, items)
    return job


# folder -> lock held by the one job working on that synced folder. Sync,
# unsync and refresh jobs on the same folder run one after another: a refresh
# removes files an unsync is about to target and copies files back that an
# unsync just removed, so letting them overlap loses either the refresh or the
# unsync. Keyed by folder, not library, because tv and tv-4kUHD (movies and
# movies-4kUHD) share one synced folder per title.
_FOLDER_LOCKS: dict[str, asyncio.Lock] = {}


async def _run_locked(
    job: Job, run: Callable[[Job], Coroutine[Any, Any, None]]
) -> None:
    lock = _FOLDER_LOCKS.setdefault(_job_folder(job.title), asyncio.Lock())
    async with lock:
        await run(job)


def launch(job: Job, run: Callable[[Job], Coroutine[Any, Any, None]]) -> Job:
    """Register `job` and run it in the background once its folder is free.

    Call on the event loop. The job stays "queued" while another job holds its
    folder (see _FOLDER_LOCKS).
    """
    _JOBS[job.id] = job
    _spawn_background(_run_locked(job, run))
    return job


def get_job(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


def recent_jobs(limit: int = 20) -> list[Job]:
    return sorted(_JOBS.values(), key=lambda j: j.started_at, reverse=True)[:limit]


def active_jobs() -> list[Job]:
    """Jobs that are queued or running."""
    return [job for job in _JOBS.values() if job.status in ("queued", "running")]


def active_jobs_for_folder(folder: str) -> list[Job]:
    """Active jobs on `folder`, in any library sharing its sync folder.

    Source deletion refuses while one runs, and a refresh joins a queued one
    instead of queueing another.
    """
    return [job for job in active_jobs() if _job_folder(job.title) == folder]


# ── Selector → file pairs ─────────────────────────────────────────────────────


def sync_dest(src: Path, lib: str) -> Path:
    sync_sub = LIBRARIES[lib]["sync_sub"]
    rel = src.relative_to(MEDIA_ROOT / lib)
    return SYNC_ROOT / sync_sub / rel


def _filter_episodes(
    detail: Any,  # synclet.scan.TitleDetail , kept loose to avoid circular import
    season_filter: int | None,
    episode_keys: list[tuple[int, int]] | None,
    unwatched_only: bool,
    watched_map: dict[tuple[int, int], bool] | None,
) -> list[Path]:
    files: list[Path] = []
    for s in detail.seasons:
        if season_filter is not None and s.season != season_filter:
            continue
        for e in s.episodes:
            key = (e.season, e.episode)
            if episode_keys is not None and key not in episode_keys:
                continue
            if unwatched_only and watched_map and watched_map.get(key):
                continue
            for fp in e.files:
                files.append(Path(fp))
    return files


def resolve_selection(
    lib: str,
    folder: str,
    *,
    selection_type: str,  # "all" | "season" | "episodes" | "movie"
    season: int | None = None,
    episodes: list[list[int]]
    | None = None,  # [[s,e], ...] for selection_type="episodes"
    unwatched_only: bool = False,
) -> list[tuple[Path, Path]]:
    """Return [(src, dst)] pairs given the user's selection.

    Pulls a fresh title-detail scan so file paths reflect current disk state.
    """
    detail = scan_title_detail(lib, folder)
    if detail is None:
        return []

    if detail.kind == "movie":
        files = [
            Path(f["path"]) for f in detail.files if is_wanted_file(Path(f["path"]))
        ]
        return [(f, sync_dest(f, lib)) for f in files]

    watched_map: dict[tuple[int, int], bool] | None = None
    if unwatched_only:
        from synclet.watchstate import show_watch_map

        watched_map = show_watch_map(detail.name, lib=lib, folder=folder)

    ep_keys: list[tuple[int, int]] | None = None
    if selection_type == "episodes" and episodes:
        ep_keys = [(int(s), int(e)) for s, e in episodes]
    season_filter = season if selection_type == "season" else None

    selected_files = _filter_episodes(
        detail,
        season_filter=season_filter,
        episode_keys=ep_keys,
        unwatched_only=unwatched_only,
        watched_map=watched_map,
    )
    return [(f, sync_dest(f, lib)) for f in selected_files]


# ── Unsync selector (identity-based, NOT source→dest name mapping) ──────────


def synced_title_files(lib: str, folder: str) -> list[Path]:
    """Every real synced file under a title's synced dir.

    Excludes dot-prefixed paths so Syncthing internals (`.stfolder`,
    `.stversions`) and dotfiles are NEVER candidates for deletion , this is a
    load-bearing blast-radius guard, do not drop it.

    Symlinks are never returned and a symlinked title dir yields nothing: unsync
    and refresh unlink what this returns, and a link could point them at files
    outside SYNC_ROOT. Synclet itself only ever writes regular files here.
    rglob does not descend into symlinked subdirectories.
    """
    synced_dir = SYNC_ROOT / LIBRARIES[lib]["sync_sub"] / folder
    if synced_dir.is_symlink() or not synced_dir.is_dir():
        return []
    out: list[Path] = []
    for f in synced_dir.rglob("*"):
        rel = f.relative_to(synced_dir)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if f.is_file() and not f.is_symlink():
            out.append(f)
    return out


def file_se(f: Path) -> tuple[int, int] | None:
    m = EPISODE_PAT.search(f.name)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _file_season(f: Path) -> int | None:
    se = file_se(f)
    if se is not None:
        return se[0]
    m = re.search(r"\d+", f.parent.name)
    return int(m.group()) if m else None


def resolve_unsync_selection(
    lib: str,
    folder: str,
    *,
    selection_type: str,  # "all" | "season" | "episodes" | "movie"
    season: int | None = None,
    episodes: list[list[int]] | None = None,
) -> list[Path]:
    """Synced files to remove for an unsync, matched by IDENTITY not filename.

    DO NOT resolve unsync through `resolve_selection` (source→dest name
    mapping). That is correct for COPY but wrong for REMOVE: a source-side
    re-encode renames the file (e.g. x264 -> h265), so the source's computed
    dest no longer exists in the synced folder and the real synced file is
    orphaned and never removed , the "Unsynced … 0 items" bug. Instead, scan
    the actual synced title dir and select what is THERE, by SxxExx
    (episodes/season) or the whole title (movie/all).
    """
    if lib not in LIBRARIES:
        return []
    files = synced_title_files(lib, folder)
    if not files:
        return []

    if LIBRARIES[lib]["kind"] == "movie" or selection_type in ("all", "movie"):
        return files

    if selection_type == "season" and season is not None:
        return [f for f in files if _file_season(f) == season]

    if selection_type == "episodes" and episodes:
        keys = {(int(s), int(e)) for s, e in episodes}
        return [f for f in files if file_se(f) in keys]

    # Unscoped show selection (e.g. "all" episodes): the whole title.
    return files


# ── Job execution ─────────────────────────────────────────────────────────────


async def _run_sync(job: Job) -> None:
    job.status = "running"
    job.started_at = time.time()
    try:
        for item in job.items:
            src = Path(item["src"])
            dst = Path(item["dst"])
            job.current_file = src.name
            if await asyncio.to_thread(dst.exists):
                job.processed_files += 1
                if item["is_video"]:
                    job.processed_media_files += 1
                continue
            await asyncio.to_thread(copy_into_place, src, dst)
            job.processed_files += 1
            if item["is_video"]:
                job.processed_media_files += 1
            job.processed_bytes += item["size"]
        job.status = "done"
        # Snapshot tracks what Synclet thinks is on disk. A completed sync
        # adds every newly-present media file. Run after the loop, not per
        # item, so a mid-job crash doesn't half-update the snapshot.
        added_keys = {
            pending_mod.path_to_snapshot_key(Path(item["dst"]))
            for item in job.items
            if item["is_video"]
        }
        # add_keys loads+saves snapshot.json (blocking FS); offload it so a
        # stalled shfs write can't freeze the event loop (see main.py note).
        await asyncio.to_thread(
            pending_mod.add_keys, (k for k in added_keys if k is not None)
        )
    except Exception as exc:
        job.error = str(exc)
        job.status = "error"
    finally:
        job.ended_at = time.time()
        state_mod.invalidate()
        _invalidate_maint_cache()


async def _run_unsync(job: Job, resolve: Callable[[], list[Path]] | None) -> None:
    job.status = "running"
    job.started_at = time.time()
    try:
        if resolve is not None:
            # Re-select under the folder lock: a refresh that ran while this
            # job was queued renamed the files it was queued with.
            set_items(job, await asyncio.to_thread(lambda: unsync_items(resolve())))
        parents: set[Path] = set()
        for item in job.items:
            dst = Path(item["dst"])
            job.current_file = dst.name
            if await asyncio.to_thread(dst.exists):
                size = (await asyncio.to_thread(dst.stat)).st_size
                await asyncio.to_thread(dst.unlink)
                job.processed_bytes += size
                parents.add(dst.parent)
                parents.add(dst.parent.parent)
            job.processed_files += 1
            if item["is_video"]:
                job.processed_media_files += 1
        # Prune empty dirs
        for p in sorted(parents, key=lambda x: -len(x.parts)):
            with contextlib.suppress(OSError):
                await asyncio.to_thread(p.rmdir)
        job.status = "done"
        # Explicit unsync is "I no longer want this here", not "I watched it".
        # Same shape as a reject: drop from snapshot, do not scrobble.
        unsync_keys = {
            pending_mod.path_to_snapshot_key(Path(item["dst"]))
            for item in job.items
            if item["is_video"]
        }
        # remove_keys loads+saves snapshot.json (blocking FS); offload it so a
        # stalled shfs write can't freeze the event loop (see main.py note).
        await asyncio.to_thread(
            pending_mod.remove_keys, (k for k in unsync_keys if k is not None)
        )
    except Exception as exc:
        job.error = str(exc)
        job.status = "error"
    finally:
        job.ended_at = time.time()
        state_mod.invalidate()
        _invalidate_maint_cache()


def sync_items(pairs: list[tuple[Path, Path]]) -> list[dict]:
    """Job items for the (src, dst) pairs not yet synced. Reads the share, so
    callers on the event loop run it in a thread."""
    items = []
    for src, dst in pairs:
        if dst.exists():
            continue
        try:
            size = src.stat().st_size
        except OSError:
            continue
        items.append(
            {
                "src": str(src),
                "dst": str(dst),
                "size": size,
                "is_video": _is_video(str(src)),
            }
        )
    return items


def start_sync(items: list[dict], title: str = "") -> Job:
    """Copy `items` (from sync_items) in the background. Call on the event loop."""
    return launch(new_job("sync", title, items), _run_sync)


def unsync_items(targets: list[Path]) -> list[dict]:
    """Job items for the synced files in `targets` that exist. Reads the share,
    so callers on the event loop run it in a thread. `targets` are actual
    synced paths (from resolve_unsync_selection), not source→dest pairs , see
    that function for why unsync must not key off source filenames."""
    items = []
    for dst in targets:
        if dst.exists():
            try:
                size = dst.stat().st_size
            except OSError:
                size = 0
            items.append(
                {
                    "dst": str(dst),
                    "size": size,
                    "is_video": _is_video(str(dst)),
                }
            )
    return items


def start_unsync(
    items: list[dict],
    title: str = "",
    resolve: Callable[[], list[Path]] | None = None,
) -> Job:
    """Remove `items` (from unsync_items) in the background. Call on the loop.

    `resolve` re-selects the targets once the job holds its folder; without it
    the job removes `items` as given.
    """
    return launch(new_job("unsync", title, items), lambda j: _run_unsync(j, resolve))


# ── Maintenance: remove watched, hanging files ────────────────────────────────


def find_source_lib(folder_name: str) -> str | None:
    """Return the library name whose media folder contains `folder_name`.

    Answered from the library listing, not a stat per library: the background
    builders call this once per synced title on every pass (see
    synclet.library for why passes must not touch the share).
    """
    from synclet import library  # local to avoid cycle

    return library.source_lib(folder_name)


def _find_watched_synced_files_uncached() -> list[dict]:
    """The expensive watched-files walk; callers should use the cached wrapper."""
    from synclet.ignored import WatchedRef, ignored_watched_set
    from synclet.scan import _season_num, clean_name
    from synclet.watchstate import movie_watch_state, show_watch_map

    ignored = ignored_watched_set()

    out: list[dict] = []
    for _sub_path, item in iter_synced_titles():
        source_lib = find_source_lib(item.name)
        if not source_lib:
            continue
        if WatchedRef(lib=source_lib, folder=item.name) in ignored:
            continue

        display = clean_name(item.name)
        kind = LIBRARIES[source_lib]["kind"]

        files_to_remove: list[Path] = []

        if kind == "movie":
            if movie_watch_state(display, lib=source_lib, folder=item.name):
                for f in item.iterdir():
                    if not f.is_dir():
                        files_to_remove.append(f)
        else:
            ws_map = show_watch_map(display, lib=source_lib, folder=item.name)
            if not ws_map:
                continue
            for season_dir in item.iterdir():
                if not season_dir.is_dir():
                    continue
                s_num = _season_num(season_dir)
                for f in season_dir.iterdir():
                    if f.is_dir():
                        continue
                    m = EPISODE_PAT.search(f.name)
                    if not m:
                        continue
                    e_num = int(m.group(2))
                    if ws_map.get((s_num, e_num)):
                        files_to_remove.append(f)

        if files_to_remove:
            size = sum(f.stat().st_size for f in files_to_remove)
            out.append(
                {
                    "title": display,
                    "lib": source_lib,
                    "folder": item.name,
                    "files": [str(f) for f in files_to_remove],
                    "size_bytes": size,
                    "file_count": len(files_to_remove),
                }
            )
    return out


# Cache keys for the two maintenance walks. Single source of truth so the
# get_cached read and the register-for-background-refresh use the exact same
# key (a drifted literal would silently fork the cache into two entries).
_WATCHED_KEY = "watched"
_HANGING_KEY = "hanging"


def find_watched_synced_files() -> list[dict]:
    """Cached wrapper: see _find_watched_synced_files_uncached."""
    from synclet.maint_cache import get_cached

    return get_cached(_WATCHED_KEY, _find_watched_synced_files_uncached)


def _find_hanging_files_uncached() -> list[dict]:
    """The expensive hanging-files walk; callers should use the cached wrapper."""
    from synclet.ignored import ignored_hanging_set

    ignored_paths = {h.path for h in ignored_hanging_set()}

    hanging: list[dict] = []
    for sub_path in iter_sync_subs():
        for dirpath in sub_path.rglob("*"):
            if not dirpath.is_dir() or dirpath.name.startswith("."):
                continue
            files = [
                f
                for f in dirpath.iterdir()
                if f.is_file() and not f.name.startswith(".")
            ]
            if not files:
                continue
            if not any(f.suffix.lower() in VIDEO_EXTS for f in files):
                for f in files:
                    if str(f) in ignored_paths:
                        continue
                    hanging.append(
                        {
                            "path": str(f),
                            "rel": str(f.relative_to(SYNC_ROOT)),
                            "size_bytes": f.stat().st_size,
                        }
                    )
    return hanging


def find_hanging_files() -> list[dict]:
    """Cached wrapper: see _find_hanging_files_uncached."""
    from synclet.maint_cache import get_cached

    return get_cached(_HANGING_KEY, _find_hanging_files_uncached)


def register_builders() -> None:
    """Register the watched/hanging builders with the background-refresh engine.

    Called once at startup so the loop's full-refresh pass rebuilds these walks
    even before any Maintenance request has touched them.
    """
    from synclet.maint_cache import register

    register(_WATCHED_KEY, _find_watched_synced_files_uncached)
    register(_HANGING_KEY, _find_hanging_files_uncached)


def remove_files(paths: list[str]) -> dict:
    # Translate to snapshot keys BEFORE deleting; path_to_snapshot_key reads
    # the file extension, not the inode, so post-delete translation still
    # works, but doing it first means the resolution is unambiguous even if
    # a sibling path collision happens.
    keys_removing = pending_mod.keys_for_paths(paths)

    removed = 0
    bytes_freed = 0
    for p in paths:
        path = Path(p)
        if path.exists() and path.is_file():
            try:
                size = path.stat().st_size
                path.unlink()
                removed += 1
                bytes_freed += size
            except OSError:
                continue
    state_mod.invalidate()
    _invalidate_maint_cache()
    # Implicit confirm: the maintenance "remove watched" path means the user
    # already watched these in Plex. Drop them from the snapshot so they do
    # NOT later appear as pending deletions.
    pending_mod.remove_keys(keys_removing)
    # Sweep orphan sidecars + prune empty parent dirs via the shared cleanup
    # helper (same logic that runs after explicit resolve). This subsumes
    # the prior parent.rmdir() loop and additionally clears subtitle/.nfo
    # leftovers the user did not explicitly select.
    cleanup = pending_mod.aggregate_cleanup(keys_removing)
    return {
        "removed": removed,
        "bytes_freed": bytes_freed,
        "cleanup": cleanup,
    }
