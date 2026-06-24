"""Plex API client: section listings, metadata lookup, poster proxy.

Designed for batch listing per section (one HTTP call per library) since the
section dump contains every title's poster key, ratingKey, year, and summary
in a single response. We cache the {folder_name: metadata} map across three
layers: in-process lru_cache (hottest), a disk JSON file under
SYNCLET_PLEX_CACHE_FILE (survives container restarts, 1h TTL by default),
and finally a fresh Plex round-trip on full miss.

The disk layer matters because the section_index calls dominate cold-load:
five libraries fan out to five serialized Plex HTTP calls inside
all_show_aggregates / all_movie_watched. With the disk cache, only the
first container in the past hour pays that cost; subsequent restarts read
the index in milliseconds.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import urllib.parse
import urllib.request

# DO NOT switch to `xml.etree` without defusedxml without tracking the
# trust boundary , Plex responses come from the user's own Plex Media Server
# at PLEX_URL (env-configured trusted source on the home LAN). Replacing
# with defusedxml is tracked in a follow-up; for now the bandit warnings
# are silenced per-import with this audit trail.
import xml.etree.ElementTree as ET  # noqa: S405
from functools import lru_cache
from pathlib import Path

from synclet.config import LIBRARIES, PLEX_TOKEN, PLEX_URL, THUMB_CACHE

# Disk-cache layer for section_index. Path is configurable for tests; default
# lives alongside snapshot.json / thumb cache under the persistent /app/data
# mount. TTL is intentionally short relative to "Plex never changes" because
# scrobbles invalidate the disk file as well as lru_cache (see
# invalidate_watch_caches below) — TTL is just the upper bound for staleness
# in the absence of mutation signals.
PLEX_CACHE_FILE = Path(
    os.environ.get("SYNCLET_PLEX_CACHE_FILE", "/app/data/.plex-section-cache.json")
)
PLEX_CACHE_TTL_S = int(os.environ.get("SYNCLET_PLEX_CACHE_TTL", "3600"))

# Serialize disk-cache writes. The startup-warm and watchstate paths now fan
# out section_index calls in parallel; without a lock, two threads can race
# inside _save_disk_cache_entry's read-modify-write (load JSON, merge their
# section, write back), and the later writer's "merge" would start from a
# pre-other-thread view and silently drop the earlier section. Per-section
# files would also work but cost a directory + N small files for one rare
# write path — a process-local Lock is the smaller diff.
_DISK_CACHE_LOCK = threading.Lock()


def _plex_url(path: str, params: dict | None = None) -> str:
    qp = {"X-Plex-Token": PLEX_TOKEN}
    if params:
        qp.update(params)
    return f"{PLEX_URL}{path}?{urllib.parse.urlencode(qp)}"


def _get_xml(
    path: str, params: dict | None = None, timeout: int = 8
) -> ET.Element | None:
    try:
        with urllib.request.urlopen(  # noqa: S310 (trusted PLEX_URL)
            _plex_url(path, params), timeout=timeout
        ) as r:
            return ET.fromstring(r.read())  # noqa: S314 (same trust boundary)
    except Exception:
        return None


def _parse_int(value: str | None, default: int = 0) -> int:
    """Parse a Plex XML attribute as int, defaulting on missing/malformed."""
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _load_disk_cache() -> dict[int, dict[str, dict]] | None:
    """Return {section_id: section_index_dict} from disk if fresh, else None.

    Returns None on any IO error, decode error, or stale TTL — callers treat
    that as a full miss and refetch from Plex. The disk cache is best-effort:
    if it's corrupt for any reason, we don't surface the error, we just bypass
    it and let the network fetch repopulate.
    """
    try:
        with PLEX_CACHE_FILE.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except OSError, json.JSONDecodeError, ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    ts = raw.get("ts")
    if not isinstance(ts, (int, float)) or time.time() - ts > PLEX_CACHE_TTL_S:
        return None
    sections = raw.get("sections")
    if not isinstance(sections, dict):
        return None
    out: dict[int, dict[str, dict]] = {}
    for k, v in sections.items():
        try:
            section_id = int(k)
        except TypeError, ValueError:
            continue
        if isinstance(v, dict):
            out[section_id] = v
    return out


def _save_disk_cache_entry(section_id: int, idx: dict[str, dict]) -> None:
    """Persist a single section's index, merging with whatever else is on disk.

    Atomic via tmp + rename so a crash partway through doesn't leave a half-
    written JSON the next reader chokes on. _DISK_CACHE_LOCK serializes the
    read-modify-write so parallel section_index callers (the threadpool in
    watchstate._fetch_indices_parallel) don't lose each other's updates.

    Best-effort: a swallowed OSError means the next request will refetch from
    Plex, no correctness impact.
    """
    with _DISK_CACHE_LOCK:
        try:
            existing: dict = {}
            if PLEX_CACHE_FILE.exists():
                try:
                    with PLEX_CACHE_FILE.open("r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        existing = loaded
                except OSError, json.JSONDecodeError, ValueError:
                    existing = {}
            sections_raw = existing.get("sections")
            sections: dict[str, dict] = (
                dict(sections_raw) if isinstance(sections_raw, dict) else {}
            )
            sections[str(section_id)] = idx
            payload = {"sections": sections, "ts": time.time()}
            PLEX_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = PLEX_CACHE_FILE.with_suffix(PLEX_CACHE_FILE.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(payload, f)
            tmp.replace(PLEX_CACHE_FILE)
        except OSError:
            # Disk cache is best-effort. The in-memory lru_cache and the next
            # Plex round-trip keep the system correct without it.
            return


def _fetch_section_index_from_plex(section_id: int) -> dict[str, dict]:
    """Pure Plex round-trip + parse, no caching. Split out for testability.

    The disk-cache and lru_cache wrappers around this live in section_index
    below. Tests that want to assert "the network was hit" monkeypatch this.
    """
    from synclet.scan import watchstate_key  # local to avoid cycle

    root = _get_xml(f"/library/sections/{section_id}/all", timeout=30)
    if root is None:
        return {}
    out: dict[str, dict] = {}
    for item in root:
        if item.tag not in ("Video", "Directory"):
            continue
        title = item.get("title")
        if not title:
            continue
        key = watchstate_key(title)
        out[key] = {
            "ratingKey": item.get("ratingKey"),
            "thumb": item.get("thumb"),
            "art": item.get("art"),
            "year": item.get("year"),
            "summary": item.get("summary") or "",
            "title": title,
            "tag": item.tag,
            "view_count": _parse_int(item.get("viewCount"), 0),
            "viewed_leaf_count": _parse_int(item.get("viewedLeafCount"), 0),
            "leaf_count": _parse_int(item.get("leafCount"), 0),
        }
    return out


@lru_cache(maxsize=8)
def section_index(section_id: int) -> dict[str, dict]:
    """Return {watchstate_key: {ratingKey, thumb, art, year, ...}} for a section.

    Plex's /library/sections/{id}/all does NOT include Location on Directory
    (show) entries, so we can't join by folder path. Instead we join by the
    same key WatchState uses: title with year/tvdb cruft stripped, lowercased.

    view_count / viewed_leaf_count / leaf_count are Plex's authoritative watch
    counters. They underpin the watchstate fallback for Plex sections that the
    user's WatchState daemon does not index (notably section 6 / YouTube and
    under-tracked 4K sections); see synclet.watchstate.

    Three-layer cache:
      1. @lru_cache (this decorator) — in-process, lifetime of the worker.
      2. PLEX_CACHE_FILE — disk JSON, survives container restart, 1h TTL.
      3. Plex network — only on full miss.
    """
    disk = _load_disk_cache()
    if disk is not None and section_id in disk:
        return disk[section_id]
    idx = _fetch_section_index_from_plex(section_id)
    if idx:
        _save_disk_cache_entry(section_id, idx)
    return idx


def find_in_library(lib: str, folder: str) -> dict | None:
    from synclet.scan import watchstate_key

    info = LIBRARIES.get(lib)
    if not info:
        return None
    idx = section_index(info["plex_section"])
    return idx.get(watchstate_key(folder))


def fetch_thumb_bytes(lib: str, folder: str) -> tuple[bytes, str] | None:
    """Return (bytes, content_type) for the Plex poster.

    Disk-cached under THUMB_CACHE so subsequent loads skip the network hit.
    """
    THUMB_CACHE.mkdir(parents=True, exist_ok=True)
    safe = f"{lib}__{folder}".replace("/", "_")[:200]
    cache_path = THUMB_CACHE / f"{safe}.jpg"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path.read_bytes(), "image/jpeg"

    meta = find_in_library(lib, folder)
    if not meta or not meta.get("thumb"):
        return None
    try:
        with urllib.request.urlopen(  # noqa: S310 (trusted PLEX_URL)
            _plex_url(meta["thumb"]), timeout=10
        ) as r:
            data = r.read()
            content_type = r.headers.get("Content-Type", "image/jpeg")
        cache_path.write_bytes(data)
        return data, content_type
    except Exception:
        return None


def fetch_art_bytes(lib: str, folder: str) -> tuple[bytes, str] | None:
    """Return (bytes, content_type) for the Plex background art (wider hero image)."""
    THUMB_CACHE.mkdir(parents=True, exist_ok=True)
    safe = f"{lib}__{folder}__art".replace("/", "_")[:200]
    cache_path = THUMB_CACHE / f"{safe}.jpg"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path.read_bytes(), "image/jpeg"

    meta = find_in_library(lib, folder)
    if not meta or not meta.get("art"):
        return None
    try:
        with urllib.request.urlopen(  # noqa: S310 (trusted PLEX_URL)
            _plex_url(meta["art"]), timeout=10
        ) as r:
            data = r.read()
            content_type = r.headers.get("Content-Type", "image/jpeg")
        cache_path.write_bytes(data)
        return data, content_type
    except Exception:
        return None


# ── Write-back: episode ratingKey lookup + scrobble ─────────────────────────


@lru_cache(maxsize=64)
def episode_rating_keys(show_rating_key: str) -> dict[tuple[int, int], str]:
    """Return {(season_index, episode_index): episode_rating_key} for a show.

    Scrobble takes the EPISODE's ratingKey, not the show's. Plex exposes
    `/library/metadata/{showRatingKey}/allLeaves` as a flat list of every
    episode in one round-trip. Cached for the process lifetime since Plex
    episode ratingKeys are stable.
    """
    root = _get_xml(f"/library/metadata/{show_rating_key}/allLeaves", timeout=15)
    if root is None:
        return {}
    out: dict[tuple[int, int], str] = {}
    for video in root:
        if video.tag != "Video":
            continue
        rk = video.get("ratingKey")
        s = video.get("parentIndex")
        e = video.get("index")
        if rk and s is not None and e is not None:
            try:
                out[int(s), int(e)] = rk
            except ValueError:
                continue
    return out


@lru_cache(maxsize=64)
def episode_watch_map(show_rating_key: str) -> dict[tuple[int, int], bool]:
    """Return {(season, episode): watched_bool} sourced directly from Plex.

    Used as a fallback when WatchState's SQLite has not indexed this show's
    library section (the canonical case: section 6 / YouTube). viewCount > 0
    on a Video means Plex recorded at least one play.

    The cache is invalidated by callers that mutate Plex view state (see
    invalidate_watch_caches) so an explicit Mark-watched is reflected on the
    next read instead of waiting for a process restart.
    """
    root = _get_xml(f"/library/metadata/{show_rating_key}/allLeaves", timeout=15)
    if root is None:
        return {}
    out: dict[tuple[int, int], bool] = {}
    for video in root:
        if video.tag != "Video":
            continue
        s = video.get("parentIndex")
        e = video.get("index")
        if s is None or e is None:
            continue
        try:
            key = (int(s), int(e))
        except ValueError:
            continue
        out[key] = _parse_int(video.get("viewCount"), 0) > 0
    return out


def invalidate_watch_caches() -> None:
    """Bust every cached Plex-side watch-state read, in-memory AND on disk.

    Call after a successful scrobble. Without this, the section_index dict and
    episode_watch_map still hold the pre-scrobble view counts and a follow-up
    read (e.g. after the frontend's session-only scrobbledOverlay clears) would
    appear to revert. section_index is also cleared because it stores the
    show-level view_count / viewed_leaf_count / leaf_count counters.

    The disk cache file is unlinked alongside the lru_cache so the post-
    scrobble state isn't resurrected by a container restart inside the TTL
    window — the two layers share invalidation authority.
    """
    section_index.cache_clear()
    episode_watch_map.cache_clear()
    with contextlib.suppress(FileNotFoundError, OSError):
        PLEX_CACHE_FILE.unlink()


def _set_watched(rating_key: str, *, watched: bool, timeout: int = 8) -> bool:
    """Set a Plex item's watch state. Returns True on success.

    `watched=True`  -> `PUT /:/scrobble`   (mark watched)
    `watched=False` -> `PUT /:/unscrobble` (mark unwatched)

    Both take `?identifier=com.plexapp.plugins.library&key=<ratingKey>` and are
    idempotent: Plex returns 200 even if the item was already in the target
    state, so callers don't need to pre-check. Network errors and non-2xx
    responses return False so callers can report per-item status without
    aborting a batch.
    """
    endpoint = "/:/scrobble" if watched else "/:/unscrobble"
    url = _plex_url(
        endpoint,
        {"identifier": "com.plexapp.plugins.library", "key": rating_key},
    )
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            return 200 <= r.status < 300
    except Exception:
        return False


def scrobble(rating_key: str, timeout: int = 8) -> bool:
    """Mark a Plex item watched. Thin wrapper over _set_watched."""
    return _set_watched(rating_key, watched=True, timeout=timeout)


def unscrobble(rating_key: str, timeout: int = 8) -> bool:
    """Mark a Plex item unwatched. Thin wrapper over _set_watched."""
    return _set_watched(rating_key, watched=False, timeout=timeout)


def get_metadata(rating_key: str) -> dict | None:
    """Look up Plex metadata by ratingKey , used by link resolver."""
    root = _get_xml(f"/library/metadata/{rating_key}")
    if root is None:
        return None
    el = next(iter(root), None)
    if el is None:
        return None
    out = {
        "ratingKey": el.get("ratingKey"),
        "title": el.get("title"),
        "type": el.get("type"),
        "year": el.get("year"),
        "thumb": el.get("thumb"),
        "summary": el.get("summary"),
    }
    # For episode -> parent show
    out["grandparentTitle"] = el.get("grandparentTitle")
    out["parentTitle"] = el.get("parentTitle")
    out["librarySectionID"] = el.get("librarySectionID")
    # Find on-disk folder for shows
    location = el.find("Location")
    if location is not None:
        out["location"] = location.get("path")
    return out
