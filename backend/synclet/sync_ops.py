"""Copy and remove operations + async job tracking.

The CLI does shutil.copy2 inline. We do the same, but in a background asyncio
task so the HTTP handler returns immediately with a job_id and the client
polls /api/jobs/{id} for progress. Matches the CLI's file selection (subtitles
filter, mkdir -p parents) exactly.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import time
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

from synclet import pending as pending_mod
from synclet import state as state_mod
from synclet.config import LIBRARIES, MEDIA_ROOT, SYNC_ROOT, VIDEO_EXTS
from synclet.fs_helpers import iter_sync_subs, iter_synced_titles
from synclet.maint_cache import invalidate as _invalidate_maint_cache
from synclet.scan import is_wanted_file, scan_title_detail


@dataclass
class Job:
    id: str
    op: str  # "sync" | "unsync"
    status: str  # "queued" | "running" | "done" | "error"
    total_files: int = 0  # every file (videos + subs + chapters)
    total_media_files: int = 0  # video files only — what humans care about
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


def get_job(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


def recent_jobs(limit: int = 20) -> list[Job]:
    return sorted(_JOBS.values(), key=lambda j: j.started_at, reverse=True)[:limit]


# ── Selector → file pairs ─────────────────────────────────────────────────────


def _sync_dest(src: Path, lib: str) -> Path:
    sync_sub = LIBRARIES[lib]["sync_sub"]
    rel = src.relative_to(MEDIA_ROOT / lib)
    return SYNC_ROOT / sync_sub / rel


def _filter_episodes(
    detail: Any,  # synclet.scan.TitleDetail — kept loose to avoid circular import
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
        return [(f, _sync_dest(f, lib)) for f in files]

    watched_map: dict[tuple[int, int], bool] | None = None
    if unwatched_only:
        from synclet.watchstate import show_watch_map

        watched_map = show_watch_map(detail.name)

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
    return [(f, _sync_dest(f, lib)) for f in selected_files]


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
            await asyncio.to_thread(dst.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, str(src), str(dst))
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
        pending_mod.add_keys(k for k in added_keys if k is not None)
    except Exception as exc:
        job.error = str(exc)
        job.status = "error"
    finally:
        job.ended_at = time.time()
        state_mod.invalidate()
        _invalidate_maint_cache()


async def _run_unsync(job: Job) -> None:
    job.status = "running"
    job.started_at = time.time()
    try:
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
        pending_mod.remove_keys(k for k in unsync_keys if k is not None)
    except Exception as exc:
        job.error = str(exc)
        job.status = "error"
    finally:
        job.ended_at = time.time()
        state_mod.invalidate()
        _invalidate_maint_cache()


def start_sync(pairs: list[tuple[Path, Path]], title: str = "") -> Job:
    job = Job(id=uuid.uuid4().hex[:12], op="sync", status="queued", title=title)
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
    job.items = items
    job.total_files = len(items)
    job.total_media_files = sum(1 for i in items if i["is_video"])
    job.total_bytes = sum(i["size"] for i in items)
    _JOBS[job.id] = job
    _spawn_background(_run_sync(job))
    return job


def start_unsync(pairs: list[tuple[Path, Path]], title: str = "") -> Job:
    job = Job(id=uuid.uuid4().hex[:12], op="unsync", status="queued", title=title)
    items = []
    for src, dst in pairs:
        if dst.exists():
            try:
                size = dst.stat().st_size
            except OSError:
                size = 0
            items.append(
                {
                    "src": str(src),
                    "dst": str(dst),
                    "size": size,
                    "is_video": _is_video(str(dst)),
                }
            )
    job.items = items
    job.total_files = len(items)
    job.total_media_files = sum(1 for i in items if i["is_video"])
    job.total_bytes = sum(i["size"] for i in items)
    _JOBS[job.id] = job
    _spawn_background(_run_unsync(job))
    return job


# ── Maintenance: remove watched, hanging files ────────────────────────────────


def find_source_lib(folder_name: str) -> str | None:
    """Return the library name whose media folder contains `folder_name`."""
    for lib in LIBRARIES:
        if (MEDIA_ROOT / lib / folder_name).exists():
            return lib
    return None


def _find_watched_synced_files_uncached() -> list[dict]:
    """The expensive watched-files walk; callers should use the cached wrapper."""
    from synclet.ignored import WatchedRef, ignored_watched_set
    from synclet.scan import _EP_PAT, _season_num, clean_name
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
            if movie_watch_state(display):
                for f in item.iterdir():
                    if not f.is_dir():
                        files_to_remove.append(f)
        else:
            ws_map = show_watch_map(display)
            if not ws_map:
                continue
            for season_dir in item.iterdir():
                if not season_dir.is_dir():
                    continue
                s_num = _season_num(season_dir)
                for f in season_dir.iterdir():
                    if f.is_dir():
                        continue
                    m = _EP_PAT.search(f.name)
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


def find_watched_synced_files() -> list[dict]:
    """Cached wrapper: see _find_watched_synced_files_uncached."""
    from synclet.maint_cache import get_cached

    return get_cached("watched", _find_watched_synced_files_uncached)


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

    return get_cached("hanging", _find_hanging_files_uncached)


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
