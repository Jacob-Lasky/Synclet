"""Plex watchlist RSS fetch + library match.

The CLI does a fuzzy match against folder names. We do the same here, but
return both matched and unmatched items so the UI can show the user what's
available vs not.

Result is TTL-cached via maint_cache because:
  - RSS fetch is a single external HTTPS call (~1-2s, variable),
  - the fuzzy-match loop is O(watchlist_items * library_titles), which
    is ~50 * ~4000 = 200k score calls on Jake's library, observed at
    6-8s on warm hits even after the RSS round-trip,
  - the watchlist doesn't change between user clicks of the same tab.
"""

from __future__ import annotations

import urllib.request

# DO NOT switch xml.etree without considering defusedxml , see plex.py for
# the same trust-boundary discussion. The Plex watchlist RSS URL comes from
# env config (WATCHLIST_RSS); same scope.
import xml.etree.ElementTree as ET  # noqa: S405

from synclet import maint_cache
from synclet.config import WATCHLIST_RSS
from synclet.fuzzy import fuzzy_score
from synclet.state import get_state

_CACHE_KEY = "watchlist"


def fetch_rss() -> list[dict]:
    try:
        with urllib.request.urlopen(  # noqa: S310 (trusted WATCHLIST_RSS env)
            WATCHLIST_RSS, timeout=10
        ) as r:
            root = ET.fromstring(r.read())  # noqa: S314 (same trust boundary)
    except Exception as exc:
        return [{"_error": str(exc)}]
    items: list[dict] = []
    for item in root.findall(".//item"):
        title = item.findtext("title") or ""
        category = item.findtext("category") or ""
        guid = item.findtext("guid") or ""
        if title:
            items.append({"title": title, "category": category, "guid": guid})
    return items


def _build() -> list[dict]:
    """Compute the /api/watchlist payload. Caller-cached via maint_cache."""
    items = fetch_rss()
    if items and items[0].get("_error"):
        return items

    state = get_state()
    titles = [(t.base.name, t.base.lib, t.base.folder, t) for t in state]

    out: list[dict] = []
    for item in items:
        scored = sorted(
            (
                (fuzzy_score(item["title"], n), n, lib, folder, ts)
                for n, lib, folder, ts in titles
            ),
            key=lambda x: -x[0],
        )
        best = scored[0] if scored else None
        matched = best is not None and best[0] >= 0.8

        entry = {
            "title": item["title"],
            "category": item["category"],
            "guid": item["guid"],
            "matched": matched,
        }
        if matched and best is not None:
            _, _, lib, folder, ts = best
            entry.update(
                {
                    "lib": lib,
                    "folder": folder,
                    "name": ts.base.name,
                    "watched_pct": ts.watched_pct,
                    "synced_pct": ts.synced_pct,
                    "kind": ts.base.kind,
                }
            )
        out.append(entry)
    return out


def get_watchlist(*, force: bool = False) -> list[dict]:
    """Return the cached /api/watchlist payload, recomputing on miss/expire.

    `force=True` bypasses the cache for one call (and refreshes it).
    Errored RSS fetches are intentionally cached too — the next request
    within TTL gets the error rather than retrying the upstream RSS,
    which prevents thundering-herd retries during a Plex.tv outage.
    """
    if force:
        maint_cache.invalidate()
    return maint_cache.get_cached(_CACHE_KEY, _build)
