"""Resolve a pasted URL to a local title.

Supported shapes:
  - Plex web app:      https://app.plex.tv/desktop/#!/server/<uuid>/details?key=%2Flibrary%2Fmetadata%2F12345
  - Plex local web:    http://192.168.86.183:32400/web/index.html#!/server/<uuid>/details?key=%2Flibrary%2Fmetadata%2F12345
  - Plex deep metadata key: /library/metadata/12345  (bare)
  - IMDb:              https://www.imdb.com/title/tt1234567/  (matched by title text , best-effort)
  - Plain text:        treated as a fuzzy library search

Plex URLs are the most precise: we hit /library/metadata/{ratingKey} on our
local Plex server, get the title, then look it up by name in our scanned
state. For episode/season ratingKeys we walk up to the show via grandparentTitle.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from synclet.fuzzy import fuzzy_score
from synclet.plex import get_metadata
from synclet.state import get_state

_PLEX_KEY = re.compile(r"/library/metadata/(\d+)")
_IMDB = re.compile(r"imdb\.com/title/(tt\d+)")
_JELLYFIN_ID = re.compile(r"[?&#]id=([a-f0-9-]+)", re.IGNORECASE)

# Minimum score before we treat a fuzzy match as confident enough to surface.
# Tuned so single-word substring matches like "saul" → "Better Call Saul" pass
# but unrelated short tokens don't.
_FUZZY_MIN_SCORE = 0.6


def _by_name(query: str) -> dict | None:
    """Fuzzy lookup against current state. Returns the best match, or None
    if no candidate scored above _FUZZY_MIN_SCORE.
    """
    if not query:
        return None
    state = get_state()
    best_score = 0.0
    best_t = None
    for t in state:
        s = fuzzy_score(query, t.base.name)
        if s > best_score:
            best_score = s
            best_t = t
    if best_t is None or best_score < _FUZZY_MIN_SCORE:
        return None
    return {
        "lib": best_t.base.lib,
        "folder": best_t.base.folder,
        "name": best_t.base.name,
        "kind": best_t.base.kind,
        "match_score": best_score,
        "match_method": "fuzzy",
    }


def resolve_url(url: str) -> dict:
    """Return {found: bool, lib?, folder?, name?, ...} for a pasted URL or query string."""
    raw = (url or "").strip()
    if not raw:
        return {"found": False, "reason": "empty"}

    decoded = urllib.parse.unquote(raw)

    # 1. Plex metadata key in URL
    m = _PLEX_KEY.search(decoded)
    if m:
        rk = m.group(1)
        meta = get_metadata(rk)
        if meta:
            # If episode/season, walk up to the show
            kind = (meta.get("type") or "").lower()
            if kind in ("episode", "season"):
                show_title = meta.get("grandparentTitle") or meta.get("parentTitle")
                if show_title:
                    by = _by_name(show_title)
                    if by:
                        return {"found": True, **by, "via": "plex_metadata"}
            elif kind in ("movie", "show"):
                title = meta.get("title") or ""
                # Prefer folder path if Plex exposes it
                loc = meta.get("location")
                if loc:
                    folder = Path(loc).name
                    state = get_state()
                    for t in state:
                        if t.base.folder == folder:
                            return {
                                "found": True,
                                "lib": t.base.lib,
                                "folder": folder,
                                "name": t.base.name,
                                "kind": t.base.kind,
                                "match_method": "plex_path",
                                "via": "plex_metadata",
                            }
                by = _by_name(title)
                if by:
                    return {"found": True, **by, "via": "plex_metadata"}
        return {
            "found": False,
            "reason": "plex_metadata_lookup_failed",
            "ratingKey": rk,
        }

    # 2. IMDb URL , fall back to title parsing from URL path (best-effort)
    m = _IMDB.search(decoded)
    if m:
        # We can't hit IMDb without an API key; treat as unknown and prompt user
        return {"found": False, "reason": "imdb_url_unsupported", "imdb_id": m.group(1)}

    # 3. Jellyfin item URL , id is a server-local guid, can't resolve from outside
    m = _JELLYFIN_ID.search(decoded)
    if m:
        return {
            "found": False,
            "reason": "jellyfin_url_unsupported",
            "jellyfin_id": m.group(1),
        }

    # 4. Plain text , fuzzy search the library
    by = _by_name(decoded)
    if by:
        return {"found": True, **by, "via": "text_search"}
    return {"found": False, "reason": "no_match", "query": decoded}
