"""Runtime config. Paths and constants the rest of the package reads.

DO NOT scatter Plex token / paths across modules — keep all environment-dependent
values here so changing a path or rotating a token is one edit.
"""

import os
from pathlib import Path

# Mounted from the host. /data/media is read-only source, /data/others/synced-media
# is what Syncthing watches — read-write, owned by the user that runs Syncthing.
MEDIA_ROOT = Path(os.environ.get("SYNCLET_MEDIA_ROOT", "/data/media"))
SYNC_ROOT = Path(os.environ.get("SYNCLET_SYNC_ROOT", "/data/others/synced-media"))

# Watch history. /appdata/watchstate is read-only mount from the host.
# The filename is watchstate_v02.db, NOT user.db — the CLI's hardcoded path is stale.
WATCHSTATE_DB = Path(
    os.environ.get(
        "SYNCLET_WATCHSTATE_DB",
        "/appdata/watchstate/users/jakelasky/watchstate_v02.db",
    )
)

# Plex server on the LAN. The token is intentionally embedded for the local-only
# deployment posture; rotate by setting SYNCLET_PLEX_TOKEN at runtime.
PLEX_URL = os.environ.get("SYNCLET_PLEX_URL", "http://192.168.86.183:32400")
PLEX_TOKEN = os.environ.get("SYNCLET_PLEX_TOKEN", "7p79GK4xzWp6A_pyNJkw")

# Jake's personal Plex watchlist RSS.
WATCHLIST_RSS = os.environ.get(
    "SYNCLET_WATCHLIST_RSS",
    "https://rss.plex.tv/f7c0b144-fc95-4a26-bbfe-1d29c2c78f15",
)

# library folder under MEDIA_ROOT -> (sync-folder under SYNC_ROOT, content kind, plex section id, display label)
LIBRARIES: dict[str, dict] = {
    "tv": {"sync_sub": "tv", "kind": "show", "plex_section": 2, "label": "TV"},
    "tv-4kUHD": {
        "sync_sub": "tv",
        "kind": "show",
        "plex_section": 12,
        "label": "TV 4K",
    },
    "movies": {
        "sync_sub": "movies",
        "kind": "movie",
        "plex_section": 1,
        "label": "Movies",
    },
    "movies-4kUHD": {
        "sync_sub": "movies",
        "kind": "movie",
        "plex_section": 7,
        "label": "Movies 4K",
    },
    "YouTube": {
        "sync_sub": "youtube",
        "kind": "youtube",
        "plex_section": 6,
        "label": "YouTube",
    },
}

EXCLUDED_DIRS = {".Recycle.Bin", ".recycle", ".trash", ".stfolder", ".stversions"}
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".webm"}
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".vtt"}
ENGLISH_CODES = {"en", "eng"}
SUBTITLE_QUALIFIERS = {"forced", "sdh", "hi", "cc", "default", "full"}

# State cache TTL (seconds). Filesystem scans are cheap enough to refresh often,
# but holding the result for ~30s avoids re-scanning on every API call during
# rapid UI navigation.
STATE_CACHE_TTL = int(os.environ.get("SYNCLET_STATE_TTL", "30"))

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

# Syncthing REST API integration (read-only). See backend/synclet/syncthing.py.
# DO NOT supply committed defaults. The integration is opt-in; unset env vars
# degrade the Syncthing UI surface to "not configured" without affecting the
# rest of the app. The Plex defaults above are grandfathered (legacy homelab
# config baked into the repo before the no-committed-secrets rule landed);
# new secrets do not get fallbacks.
SYNCTHING_URL = os.environ.get("SYNCTHING_URL")
SYNCTHING_API_KEY = os.environ.get("SYNCTHING_API_KEY")
