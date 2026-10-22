"""Runtime config. Paths and constants the rest of the package reads.

DO NOT scatter Plex token / paths across modules , keep all environment-dependent
values here so changing a path or rotating a token is one edit.
"""

import os
from pathlib import Path

# Mounted from the host. Production grants /data/media write access for the
# explicit, preview-confirmed source delete route. The dev mount stays read-only.
# /data/others/synced-media is what Syncthing watches, read-write.
MEDIA_ROOT = Path(os.environ.get("SYNCLET_MEDIA_ROOT", "/data/media"))
SYNC_ROOT = Path(os.environ.get("SYNCLET_SYNC_ROOT", "/data/others/synced-media"))

# Watch history. /appdata/watchstate is read-only mount from the host.
# The filename is watchstate_v02.db, NOT user.db , the CLI's hardcoded path is stale.
WATCHSTATE_DB = Path(
    os.environ.get(
        "SYNCLET_WATCHSTATE_DB",
        "/appdata/watchstate/users/jakelasky/watchstate_v02.db",
    )
)

# Plex server on the LAN. The token is intentionally embedded for the local-only
# deployment posture; rotate by setting SYNCLET_PLEX_TOKEN at runtime.
PLEX_URL = os.environ.get("SYNCLET_PLEX_URL", "https://plex.lan.jacoblasky.com")
PLEX_TOKEN = os.environ.get("SYNCLET_PLEX_TOKEN", "7p79GK4xzWp6A_pyNJkw")

# Jake's personal Plex watchlist RSS.
WATCHLIST_RSS = os.environ.get(
    "SYNCLET_WATCHLIST_RSS",
    "https://rss.plex.tv/f7c0b144-fc95-4a26-bbfe-1d29c2c78f15",
)

# library folder under MEDIA_ROOT -> (sync-folder under SYNC_ROOT, content kind,
# plex section id, display label, arr env prefix or None; see arr_instance)
LIBRARIES: dict[str, dict] = {
    "tv": {
        "sync_sub": "tv",
        "kind": "show",
        "plex_section": 2,
        "label": "TV",
        "arr": "SONARR",
    },
    "tv-4kUHD": {
        "sync_sub": "tv",
        "kind": "show",
        "plex_section": 12,
        "label": "TV 4K",
        "arr": "SONARR_4K",
    },
    "movies": {
        "sync_sub": "movies",
        "kind": "movie",
        "plex_section": 1,
        "label": "Movies",
        "arr": "RADARR",
    },
    "movies-4kUHD": {
        "sync_sub": "movies",
        "kind": "movie",
        "plex_section": 7,
        "label": "Movies 4K",
        "arr": "RADARR_4K",
    },
    "YouTube": {
        "sync_sub": "youtube",
        "kind": "youtube",
        "plex_section": 6,
        "label": "YouTube",
        "arr": None,
    },
}


def libs_for_sync_sub(sync_sub: str) -> list[str]:
    """Libraries (config order) whose titles sync into SYNC_ROOT/<sync_sub>."""
    return [lib for lib, info in LIBRARIES.items() if info["sync_sub"] == sync_sub]


# External metadata-agent ID schemes Synclet can join a synced folder to its
# Plex entry on. Single source of truth: scan.py parses them from a folder's
# {tvdb-...} cruft and plex.py parses them from a Plex <Guid id="tvdb://..."/>.
# Keep both parsers driven off this so adding a scheme (e.g. tvmaze) is one edit.
EXTERNAL_ID_SCHEMES = ("imdb", "tmdb", "tvdb")

EXCLUDED_DIRS = {".Recycle.Bin", ".recycle", ".trash", ".stfolder", ".stversions"}
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".webm"}
# Every subtitle format a library holds, text and image (.sup is Blu-ray PGS,
# .idx pairs with VobSub .sub). scan.is_wanted_file applies the English filter
# only to these, so a format missing here is synced in every language, and
# synclet.refresh treats a change to one as a media change.
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".sup", ".vtt"}
ENGLISH_CODES = {"en", "eng"}
SUBTITLE_QUALIFIERS = {"forced", "sdh", "hi", "cc", "default", "full"}

# Background cache refresh cadence (seconds). Reads never build (see
# synclet.maint_cache); the background loop in main.cache_refresh_loop owns all
# rebuilding. It wakes every CACHE_DIRTY_REFRESH to rebuild keys a mutation
# flagged dirty (prompt reflection of sync/unsync/remove), and does a full
# all-keys refresh every CACHE_FULL_REFRESH to pick up external changes
# (WatchState's own import poll, Syncthing propagation). These intervals trade
# staleness for background load, NOT user-facing latency, because no read ever
# blocks on a build.
CACHE_DIRTY_REFRESH = int(os.environ.get("SYNCLET_CACHE_DIRTY_REFRESH", "3"))
CACHE_FULL_REFRESH = int(os.environ.get("SYNCLET_CACHE_FULL_REFRESH", "300"))
# A full pass slower than this is logged as a warning, naming the share as slow.
CACHE_SLOW_PASS = int(os.environ.get("SYNCLET_CACHE_SLOW_PASS", "30"))

# Source-library listing (see synclet.library). The full pass above does NOT
# list MEDIA_ROOT: library roots are merged shfs directories that cost seconds
# to minutes cold and stall Plex, Jellyfin and SMB while they run (Synclet#66).
# Titles are updated one stat at a time from arr webhooks and Synclet's own
# deletes; LIBRARY_RELIST is only the backstop for changes nothing reported.
LIBRARY_LISTING_FILE = Path(
    os.environ.get("SYNCLET_LIBRARY_LISTING_FILE", "/app/data/.library-listing.json"),
)
LIBRARY_RELIST_S = int(os.environ.get("SYNCLET_LIBRARY_RELIST", "21600"))
LIBRARY_RETRY_S = int(os.environ.get("SYNCLET_LIBRARY_RETRY", "900"))
LIBRARY_LIST_TIMEOUT_S = int(os.environ.get("SYNCLET_LIBRARY_LIST_TIMEOUT", "120"))
LIBRARY_STAT_TIMEOUT_S = int(os.environ.get("SYNCLET_LIBRARY_STAT_TIMEOUT", "10"))
# Per-title episode tree reuse by background builders (library.title_detail).
# Arr webhooks invalidate a title at once; this bounds staleness for sources
# without one (YouTube).
TITLE_RESCAN_S = int(os.environ.get("SYNCLET_TITLE_RESCAN", "3600"))
# How long a library file must sit unchanged (seconds since its mtime) before
# the background pass refreshes a synced copy from it (synclet.refresh). Imports
# and Tdarr write library files in place; copying one mid-write would send a
# partial file to the devices and then a second full copy. Arr webhooks skip
# the wait because the arr sends them once its import has finished.
REFRESH_SETTLE_S = int(os.environ.get("SYNCLET_REFRESH_SETTLE", "600"))
# After a failed refresh, the background pass waits this long before trying
# that title again (see synclet.refresh._failed_at).
REFRESH_RETRY_S = int(os.environ.get("SYNCLET_REFRESH_RETRY", "3600"))

# Whether to run the background refresh loop + startup prewarm. On in
# production. Tests set SYNCLET_CACHE_BACKGROUND_REFRESH=0 so no live timer
# mutates the in-process cache mid-test; they drive run_refresh_cycle directly
# and rely on the cold-miss build for read-your-writes.
CACHE_BACKGROUND_REFRESH = (
    os.environ.get("SYNCLET_CACHE_BACKGROUND_REFRESH", "1") != "0"
)

# Where to cache Plex poster bytes. Lives under /app/data inside the backend
# container; persistent across restarts via docker-compose bind mount.
THUMB_CACHE = Path(os.environ.get("SYNCLET_THUMB_CACHE", "/app/data/.thumb-cache"))

# Snapshot of what Synclet last knew was synced. Source of truth for the
# deletion-driven watch-state write-back flow (see synclet.pending). Persists
# across restarts via the same /app/data bind mount as THUMB_CACHE.
SNAPSHOT_FILE = Path(
    os.environ.get("SYNCLET_SNAPSHOT_FILE", "/app/data/snapshot.json"),
)

# User-muted maintenance entries (see synclet.ignored). Survives restarts so
# muting is sticky; lives alongside snapshot.json.
IGNORED_FILE = Path(
    os.environ.get("SYNCLET_IGNORED_FILE", "/app/data/ignored.json"),
)

# Episodic titles the user wants to keep watching for new releases. This is
# separate from the synced file tree: a show stays followed after its last
# offline episode is removed. Stored with the other persistent app state.
FOLLOWED_FILE = Path(
    os.environ.get("SYNCLET_FOLLOWED_FILE", "/app/data/followed.json"),
)

# Permanent source deletion requires a separate secret. Keep it only in the
# gitignored deployment .env, never in the shared committed env contract.
DELETE_KEY = os.environ.get("SYNCLET_DELETE_KEY", "")

# Syncthing REST API integration (read-only). See backend/synclet/syncthing.py.
# DO NOT supply committed defaults. The integration is opt-in; unset env vars
# degrade the Syncthing UI surface to "not configured" without affecting the
# rest of the app. The Plex defaults above are grandfathered (legacy homelab
# config baked into the repo before the no-committed-secrets rule landed);
# new secrets do not get fallbacks.
SYNCTHING_URL = os.environ.get("SYNCTHING_URL")
SYNCTHING_API_KEY = os.environ.get("SYNCTHING_API_KEY")

# Sonarr/Radarr per source library, so a source delete can ask the owning arr
# to rescan just that series or movie (synclet.arr). Opt-in like Syncthing: no
# committed defaults, and a library with no URL+key skips the rescan. The env
# prefix per library lives in LIBRARIES[lib]["arr"].
ARR_TIMEOUT_S = int(os.environ.get("SYNCLET_ARR_TIMEOUT", "10"))


def arr_instance(lib: str) -> tuple[str, str, str] | None:
    """(app, base_url, api_key) for the arr that owns `lib`, or None."""
    info = LIBRARIES.get(lib) or {}
    prefix = info.get("arr")
    if not prefix:
        return None
    url = os.environ.get(f"SYNCLET_{prefix}_URL")
    key = os.environ.get(f"SYNCLET_{prefix}_API_KEY")
    if not url or not key:
        return None
    app = "sonarr" if info["kind"] == "show" else "radarr"
    return app, url.rstrip("/"), key


# Production: the Vite-built frontend (vite build -> dist/) ships inside the
# backend image and is served by the same Litestar process at /. Set via env
# in the prod Dockerfile only. Unset in dev so the dev backend doesn't try to
# serve a non-existent dir; dev frontend runs separately on :1313.
_STATIC_DIR_ENV = os.environ.get("SYNCLET_STATIC_DIR")
STATIC_DIR: Path | None = Path(_STATIC_DIR_ENV) if _STATIC_DIR_ENV else None
