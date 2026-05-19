"""Plex watchlist RSS fetch + library match.

The CLI does a fuzzy match against folder names. We do the same here, but
return both matched and unmatched items so the UI can show the user what's
available vs not.
"""

from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET

from synclet.config import WATCHLIST_RSS
from synclet.fuzzy import fuzzy_score
from synclet.state import get_state


def fetch_rss() -> list[dict]:
    try:
        with urllib.request.urlopen(WATCHLIST_RSS, timeout=10) as r:
            root = ET.fromstring(r.read())
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


def get_watchlist() -> list[dict]:
    items = fetch_rss()
    if items and items[0].get("_error"):
        return items

    state = get_state()
    titles = [(t.base.name, t.base.lib, t.base.folder, t) for t in state]

    out: list[dict] = []
    for item in items:
        scored = sorted(
            ((fuzzy_score(item["title"], n), n, lib, folder, ts) for n, lib, folder, ts in titles),
            key=lambda x: -x[0],
        )
        best = scored[0] if scored else None
        matched = best and best[0] >= 0.8

        entry = {
            "title": item["title"],
            "category": item["category"],
            "guid": item["guid"],
            "matched": bool(matched),
        }
        if matched:
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
