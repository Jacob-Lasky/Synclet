"""Fuzzy title matching shared between resolve.py (link → title) and
watchlist.py (Plex RSS → library).

Mirror of frontend store.ts:fuzzyScore — keep the two in sync if either side
changes, since user-visible search ranking depends on identical behavior.
"""

from __future__ import annotations

from difflib import SequenceMatcher


def fuzzy_score(query: str, target: str) -> float:
    """Score how well `query` matches `target`. Higher is better.

    Returns:
      2.0: exact case-insensitive match
      1.0+: substring match, longer-query/shorter-target = higher
      0.9: all query words appear in target as substrings
      0.5 to 0.85: query word prefixes match target words
      <=0.4: SequenceMatcher ratio as last-resort
    """
    q = query.lower()
    t = target.lower()
    if not q:
        return 0.0
    if q == t:
        return 2.0
    if q in t:
        return 1.0 + len(q) / max(len(t), 1)
    words = q.split()
    if words and all(w in t for w in words):
        return 0.9
    t_words = t.split()
    prefix_hits = sum(any(tw.startswith(w) for tw in t_words) for w in words)
    if prefix_hits > 0:
        return 0.5 + (prefix_hits / len(words)) * 0.35
    return SequenceMatcher(None, q, t).ratio() * 0.4
