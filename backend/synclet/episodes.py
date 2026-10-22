"""Episode numbers in media file names. The one parser every module uses.

Sonarr names a multi-episode file `S01E01E02` or `S01E01-E02`. EPISODE_PAT
matches the FIRST episode only; it is what a module wants when one file maps to
one key (the pending snapshot, the grid's per-season episode list).
episode_numbers returns EVERY episode a file holds; use it wherever acting on
a file affects all of them (deleting source media, replacing a synced copy).
"""

from __future__ import annotations

import re

EPISODE_PAT = re.compile(r"[Ss](\d+)[Ee](\d+)")
_MULTI_EP = re.compile(r"[Ss](\d+)((?:-?[Ee]\d+)+)")
_EP_NUM = re.compile(r"[Ee](\d+)")


def episode_numbers(name: str) -> set[tuple[int, int]]:
    """{(season, episode)} for every episode in a file name; empty if none."""
    match = _MULTI_EP.search(name)
    if not match:
        return set()
    season = int(match.group(1))
    return {(season, int(e)) for e in _EP_NUM.findall(match.group(2))}
