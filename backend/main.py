"""Synclet HTTP API.

Routes are flat by design — no controllers, no DI containers. The file should
read like a contract: every endpoint the frontend touches is listed here.
"""

from __future__ import annotations

from litestar import Litestar, MediaType, Response, get, post
from litestar.config.cors import CORSConfig
from litestar.exceptions import NotFoundException
from pydantic import BaseModel

from common.log_utils import get_logger
from synclet import pending, sync_ops
from synclet.plex import fetch_art_bytes, fetch_thumb_bytes
from synclet.resolve import resolve_url
from synclet.scan import scan_title_detail, title_detail_to_dict
from synclet.state import disk_usage, get_state, invalidate
from synclet.watchstate import movie_watch_state, show_watch_map
from synclet.watchlist import get_watchlist

logger = get_logger(__name__)

cors_config = CORSConfig(
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


# ── Models ────────────────────────────────────────────────────────────────────


class SyncRequest(BaseModel):
    lib: str
    folder: str
    selection_type: str = "all"
    season: int | None = None
    episodes: list[list[int]] | None = None
    unwatched_only: bool = False


class RemoveFilesRequest(BaseModel):
    paths: list[str]


class ResolveLinkRequest(BaseModel):
    url: str


class PendingItemRef(BaseModel):
    """One pending-deletion item the user is acting on.

    season and episode are None for movies. The (sync_sub, folder) pair is
    unique within SYNC_ROOT, so no library disambiguation is needed.
    """

    sync_sub: str
    folder: str
    season: int | None = None
    episode: int | None = None


class ResolvePendingRequest(BaseModel):
    items: list[PendingItemRef]
    action: str  # "confirm" | "reject"


# ── Routes ────────────────────────────────────────────────────────────────────


@get("/api/health", media_type=MediaType.TEXT)
async def health() -> str:
    return "OK"


@get("/api/state")
async def api_state(refresh: bool = False) -> dict:
    from synclet.config import LIBRARIES

    state = get_state(force=refresh)
    return {
        "titles": [t.to_dict() for t in state],
        "disk": disk_usage(),
        # Surface library metadata so the frontend doesn't hardcode labels or
        # short tags. `short` is the 2-char badge used on cards.
        "libraries": [
            {
                "id": lib,
                "label": info["label"],
                "short": _short_label(lib, info["label"]),
                "kind": info["kind"],
                "sync_sub": info["sync_sub"],
            }
            for lib, info in LIBRARIES.items()
        ],
    }


def _short_label(lib_id: str, label: str) -> str:
    """Two-char badge code for a library — '4K' for 4K libs, 'YT' for YouTube,
    else the first letter of the first two words, or the first two letters of
    a single-word label."""
    if "4K" in label or "4kUHD" in lib_id:
        return "4K"
    if "youtube" in lib_id.lower():
        return "YT"
    words = [w for w in label.split() if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    if len(words) == 1:
        return words[0][:2].upper()
    return "??"


@get("/api/title/{lib:str}/{folder:path}")
async def api_title(lib: str, folder: str) -> dict:
    folder = folder.lstrip("/")
    detail = scan_title_detail(lib, folder)
    if detail is None:
        raise NotFoundException(f"{lib}/{folder} not found")

    out = title_detail_to_dict(detail)

    # Join watch state into episodes (TV/YouTube) or top-level (movie)
    if detail.kind == "movie":
        out["watched"] = bool(movie_watch_state(detail.name))
    else:
        ws_map = show_watch_map(detail.name)
        watched_total = 0
        for s in out["seasons"]:
            sw = 0
            for e in s["episodes"]:
                w = ws_map.get((e["season"], e["episode"]))
                if w:
                    e["watch_state"] = "watched"
                    e["watch_pct"] = 100
                    sw += 1
            s["watched_episodes"] = sw
            watched_total += sw
        out["watched_episodes"] = watched_total

    return out


@get("/api/plex/thumb/{lib:str}/{folder:path}")
async def api_thumb(lib: str, folder: str) -> Response:
    folder = folder.lstrip("/")
    result = fetch_thumb_bytes(lib, folder)
    if result is None:
        return Response(content=b"", status_code=404, media_type="image/jpeg")
    data, content_type = result
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@get("/api/plex/art/{lib:str}/{folder:path}")
async def api_art(lib: str, folder: str) -> Response:
    folder = folder.lstrip("/")
    result = fetch_art_bytes(lib, folder)
    if result is None:
        return Response(content=b"", status_code=404, media_type="image/jpeg")
    data, content_type = result
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@post("/api/sync")
async def api_sync(data: SyncRequest) -> dict:
    pairs = sync_ops.resolve_selection(
        data.lib,
        data.folder,
        selection_type=data.selection_type,
        season=data.season,
        episodes=data.episodes,
        unwatched_only=data.unwatched_only,
    )
    if not pairs:
        return {"error": "no files matched", "job_id": None}
    job = sync_ops.start_sync(pairs, title=f"{data.lib}/{data.folder}")
    return {
        "job_id": job.id,
        "total_files": job.total_files,
        "total_media_files": job.total_media_files,
        "total_bytes": job.total_bytes,
    }


@post("/api/unsync")
async def api_unsync(data: SyncRequest) -> dict:
    pairs = sync_ops.resolve_selection(
        data.lib,
        data.folder,
        selection_type=data.selection_type,
        season=data.season,
        episodes=data.episodes,
        unwatched_only=False,
    )
    if not pairs:
        return {"error": "no files matched", "job_id": None}
    job = sync_ops.start_unsync(pairs, title=f"{data.lib}/{data.folder}")
    return {
        "job_id": job.id,
        "total_files": job.total_files,
        "total_media_files": job.total_media_files,
        "total_bytes": job.total_bytes,
    }


@get("/api/jobs/{job_id:str}")
async def api_job(job_id: str) -> dict:
    job = sync_ops.get_job(job_id)
    if not job:
        raise NotFoundException(f"job {job_id} not found")
    return job.to_dict()


@get("/api/jobs")
async def api_jobs() -> dict:
    return {"jobs": [j.to_dict() for j in sync_ops.recent_jobs()]}


@get("/api/synced")
async def api_synced() -> dict:
    """Return synced titles with new-unwatched-episode hints."""
    from synclet.config import LIBRARIES
    from synclet.fs_helpers import iter_synced_titles
    from synclet.scan import clean_name, scan_title_detail
    from synclet.sync_ops import find_source_lib
    from synclet.watchstate import show_watch_map

    items: list[dict] = []
    for _sub_path, item in iter_synced_titles():
        source_lib = find_source_lib(item.name)

        display = clean_name(item.name)
        total_bytes = 0
        for f in item.rglob("*"):
            if f.is_file():
                try:
                    total_bytes += f.stat().st_size
                except OSError:
                    pass

        entry = {
            "title": display,
            "folder": item.name,
            "lib": source_lib,
            "kind": LIBRARIES[source_lib]["kind"] if source_lib else "unknown",
            "size_bytes": total_bytes,
            "new_unwatched": [],
        }

        if source_lib and LIBRARIES[source_lib]["kind"] in ("show", "youtube"):
            detail = scan_title_detail(source_lib, item.name)
            if detail:
                ws_map = show_watch_map(display)
                new_eps = []
                for s in detail.seasons:
                    for e in s.episodes:
                        watched = ws_map.get((e.season, e.episode), False)
                        if not watched and not e.is_synced:
                            new_eps.append(
                                {
                                    "season": e.season,
                                    "episode": e.episode,
                                    "title": e.title,
                                    "size_bytes": e.size_bytes,
                                }
                            )
                entry["new_unwatched"] = new_eps

        items.append(entry)
    return {"items": items}


@get("/api/watchlist")
async def api_watchlist() -> dict:
    return {"items": get_watchlist()}


@get("/api/maintenance/watched")
async def api_maint_watched() -> dict:
    return {"items": sync_ops.find_watched_synced_files()}


@get("/api/maintenance/hanging")
async def api_maint_hanging() -> dict:
    return {"items": sync_ops.find_hanging_files()}


@post("/api/maintenance/remove")
async def api_maint_remove(data: RemoveFilesRequest) -> dict:
    return sync_ops.remove_files(data.paths)


@get("/api/maintenance/pending")
async def api_maint_pending() -> dict:
    """Synced items whose files were deleted since the last snapshot.

    Returned as a grouped tree (one entry per show/movie, episodes nested
    under seasons for shows). The frontend (MaintenanceView pending pane)
    renders this directly.
    """
    return {"items": pending.grouped_pending()}


@post("/api/maintenance/resolve")
async def api_maint_resolve(data: ResolvePendingRequest) -> dict:
    """Confirm or reject a batch of pending deletions.

    Confirm scrobbles each item to Plex (best-effort, per-item) and drops it
    from the snapshot. Reject just drops from the snapshot. Per-item results
    let the UI flag scrobble failures without aborting the rest of the batch.

    After resolve, orphan sidecars (subtitles, .nfo, art) and now-empty
    parent dirs are swept by pending.cleanup_after_resolve. Counts surface
    in the response's `cleanup` field for toast UX.
    """
    if data.action not in {"confirm", "reject"}:
        return {
            "error": f"unknown action: {data.action}",
            "results": [],
            "cleanup": {"removed_files": 0, "removed_dirs": 0},
        }
    keys = [
        pending.SnapshotKey(
            sync_sub=item.sync_sub,
            folder=item.folder,
            season=item.season,
            episode=item.episode,
        )
        for item in data.items
    ]
    results, cleanup = pending.resolve(keys, confirm=(data.action == "confirm"))
    return {"results": [r.to_dict() for r in results], "cleanup": cleanup}


@post("/api/resolve-link")
async def api_resolve(data: ResolveLinkRequest) -> dict:
    return resolve_url(data.url)


@post("/api/refresh")
async def api_refresh() -> dict:
    invalidate()
    return {"ok": True}


app = Litestar(
    route_handlers=[
        health,
        api_state,
        api_title,
        api_thumb,
        api_art,
        api_sync,
        api_unsync,
        api_job,
        api_jobs,
        api_synced,
        api_watchlist,
        api_maint_watched,
        api_maint_hanging,
        api_maint_remove,
        api_maint_pending,
        api_maint_resolve,
        api_resolve,
        api_refresh,
    ],
    cors_config=cors_config,
)
