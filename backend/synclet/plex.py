"""Plex API client: section listings, metadata lookup, poster proxy.

Designed for batch listing per section (one HTTP call per library) since the
section dump contains every title's poster key, ratingKey, year, and summary
in a single response. We cache the {folder_name: metadata} map for the
process lifetime — it's a few hundred KB and Plex updates rarely.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from synclet.config import LIBRARIES, PLEX_TOKEN, PLEX_URL, THUMB_CACHE


def _plex_url(path: str, params: dict | None = None) -> str:
    qp = {"X-Plex-Token": PLEX_TOKEN}
    if params:
        qp.update(params)
    return f"{PLEX_URL}{path}?{urllib.parse.urlencode(qp)}"


def _get_xml(path: str, params: dict | None = None, timeout: int = 8) -> ET.Element | None:
    try:
        with urllib.request.urlopen(_plex_url(path, params), timeout=timeout) as r:
            return ET.fromstring(r.read())
    except Exception:
        return None


@lru_cache(maxsize=8)
def section_index(section_id: int) -> dict[str, dict]:
    """Return {watchstate_key: {ratingKey, thumb, art, year, ...}} for a section.

    Plex's /library/sections/{id}/all does NOT include Location on Directory
    (show) entries, so we can't join by folder path. Instead we join by the
    same key WatchState uses: title with year/tvdb cruft stripped, lowercased.
    """
    from synclet.scan import watchstate_key   # local to avoid cycle

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
        }
    return out


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
        with urllib.request.urlopen(_plex_url(meta["thumb"]), timeout=10) as r:
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
        with urllib.request.urlopen(_plex_url(meta["art"]), timeout=10) as r:
            data = r.read()
            content_type = r.headers.get("Content-Type", "image/jpeg")
        cache_path.write_bytes(data)
        return data, content_type
    except Exception:
        return None


def get_metadata(rating_key: str) -> dict | None:
    """Look up Plex metadata by ratingKey — used by link resolver."""
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
