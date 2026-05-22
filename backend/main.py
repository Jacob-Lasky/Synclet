"""Synclet HTTP API.

Routes are flat by design: no controllers, no DI containers. The file should
read like a contract: every endpoint the frontend touches is listed here.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from litestar import Litestar, MediaType, Response, get, post
from litestar.config.cors import CORSConfig
from litestar.exceptions import NotFoundException
from litestar.static_files import create_static_files_router
from pydantic import BaseModel

from common.log_utils import get_logger
from synclet import config, ignored, pending, sync_ops, syncthing
from synclet.plex import fetch_art_bytes, fetch_thumb_bytes
from synclet.resolve import resolve_url
from synclet.scan import scan_title_detail, title_detail_to_dict
from synclet.state import disk_usage, get_state, invalidate
from synclet.watchlist import get_watchlist
from synclet.watchstate import movie_watch_state, show_watch_map

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


class IgnoreRequest(BaseModel):
    """Mute or un-mute a maintenance entry.

    `kind` discriminates the ref shape:
      - "pending": ref = {sync_sub, folder, season?, episode?}
      - "watched": ref = {lib, folder}
      - "hanging": ref = {path}
    """

    kind: str
    ref: dict


class ScrobbleRequest(BaseModel):
    """Explicit mark-watched gesture.

    The file is still on disk; this is "Plex's watch state drifted, sync it
    up." Distinct from /api/maintenance/resolve which is post-deletion.
    """

    lib: str
    folder: str
    scope: str  # "movie" | "series" | "season" | "episode"
    season: int | None = None
    episode: int | None = None


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
    """Two-char badge code for a library: '4K' for 4K libs, 'YT' for YouTube,
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
                with contextlib.suppress(OSError):
                    total_bytes += f.stat().st_size

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


@get("/api/maintenance/ignored")
async def api_maint_ignored() -> dict:
    """Return the user's muted entries, grouped by kind, for the Ignored UI."""
    return ignored.list_grouped()


@post("/api/maintenance/ignore")
async def api_maint_ignore(data: IgnoreRequest) -> dict:
    """Mute a maintenance entry. Idempotent. Returns ok=False on malformed ref."""
    ok = ignored.ignore_ref(data.kind, data.ref)
    return {"ok": ok}


@post("/api/maintenance/unignore")
async def api_maint_unignore(data: IgnoreRequest) -> dict:
    """Un-mute a maintenance entry. Idempotent. Returns ok=False on malformed ref."""
    ok = ignored.unignore_ref(data.kind, data.ref)
    return {"ok": ok}


@get("/api/maintenance/counts")
async def api_maint_counts() -> dict:
    """Actionable-item counts for the Maintenance tab badge.

    Sum is what TabBar surfaces as a single number. Per-category counts are
    exposed too so the UI can target a specific section without re-fetching.
    Walks the filesystem (cheap at homelab scale, <1s for ~hundreds of files);
    callers should debounce or fetch on tab focus rather than every render.
    """
    watched = sync_ops.find_watched_synced_files()
    hanging = sync_ops.find_hanging_files()
    pending_items = pending.compute_pending()
    return {
        "watched_titles": len(watched),
        "hanging_files": len(hanging),
        "pending_items": len(pending_items),
        "total": len(watched) + len(hanging) + len(pending_items),
    }


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


@post("/api/scrobble")
async def api_scrobble(data: ScrobbleRequest) -> dict:
    """Mark Plex items watched explicitly (no file deletion required).

    Surfaced in DetailDrawer as Mark-watched buttons at the movie / series /
    season / episode levels. The file stays on disk; only Plex's watch state
    is updated. WatchState's daemon picks up the change on its next poll, so
    Synclet's grid view will reflect the new watched state shortly after.
    """
    return pending.mark_watched_scope(
        lib=data.lib,
        folder=data.folder,
        scope=data.scope,
        season=data.season,
        episode=data.episode,
    )


@post("/api/resolve-link")
async def api_resolve(data: ResolveLinkRequest) -> dict:
    return resolve_url(data.url)


@post("/api/refresh")
async def api_refresh() -> dict:
    invalidate()
    return {"ok": True}


@get("/api/syncthing/overview")
async def api_syncthing_overview() -> dict:
    """Read-only join of Syncthing's folder + device state.

    Returns {"configured": bool, "folders": [...]}. The configured flag
    lets the frontend distinguish "Syncthing not set up" from "Syncthing
    set up but currently has nothing to show" without leaking env state.
    Folders shape is documented in backend/synclet/syncthing.py.
    """
    if not syncthing.is_configured():
        return {"configured": False, "folders": []}
    return {"configured": True, "folders": await syncthing.overview()}


def build_route_handlers(static_dir: Path | None = None) -> list:
    """Assemble the route_handlers list for Litestar.

    The API routes are listed literally; the optional static-files router is
    appended LAST so Litestar's path specificity prefers the literal /api/...
    matches over the static catchall rooted at /. If you reorder this list
    so the static router comes first, /api/* requests will be poached and
    return HTML instead of JSON — the contract test in tests/test_api.py
    pins this ordering.

    static_dir: the built Vue dist. None (dev) means no static handler is
    added; the dev frontend runs separately on :1313. A non-None Path that
    doesn't exist on disk is also ignored — saves a startup crash if the
    env var points somewhere stale.
    """
    handlers: list = [
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
        api_maint_counts,
        api_maint_ignored,
        api_maint_ignore,
        api_maint_unignore,
        api_maint_pending,
        api_maint_resolve,
        api_scrobble,
        api_resolve,
        api_refresh,
        api_syncthing_overview,
    ]

    if static_dir is not None and static_dir.is_dir():
        logger.info("Serving static frontend from %s", static_dir)
        # html_mode=True serves index.html from / and 404.html on missing
        # files. The production Dockerfile copies index.html -> 404.html so
        # unknown paths still render the SPA shell (Vue mounts, default view
        # renders). Status code on the fallback is 404, not 200; acceptable
        # for Synclet which has no client-side URL routing today.
        handlers.append(
            create_static_files_router(
                path="/",
                directories=[static_dir],
                html_mode=True,
            ),
        )

    return handlers


app = Litestar(
    route_handlers=build_route_handlers(config.STATIC_DIR),
    cors_config=cors_config,
)
