"""Per-user mute list for Maintenance entries.

Some maintenance items the user has seen and explicitly does not want to
act on (a sidecar that's kept on purpose, a pending deletion that's
expected, a "watched in synced" item they want to keep around). Today
the only way to clear those is to act on them (resolve / remove /
confirm), which mutates Plex or the filesystem. This module adds a
third gesture: **ignore**, which mutes the entry without acting on it.

Ignored entries persist across restarts in `/app/data/ignored.json`
alongside `snapshot.json`. Three independent identifier spaces:

- **pending**  : SnapshotKey shape `(sync_sub, folder, season?, episode?)`
- **watched**  : `(lib, folder)` per title group
- **hanging**  : the absolute file path inside SYNC_ROOT

Filtering hooks live alongside the producers (pending.grouped_pending,
sync_ops.find_watched_synced_files, sync_ops.find_hanging_files) and
intersect the producer's output with the matching ignored set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from synclet.config import IGNORED_FILE


# ── Per-kind references (frozen so they're hashable, set-safe) ───────────────


@dataclass(frozen=True)
class PendingRef:
    """Identifies a pending-deletion item; matches SnapshotKey shape."""

    sync_sub: str
    folder: str
    season: int | None = None
    episode: int | None = None

    def to_dict(self) -> dict:
        """JSON-serialisable dict."""
        return {
            "sync_sub": self.sync_sub,
            "folder": self.folder,
            "season": self.season,
            "episode": self.episode,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PendingRef:
        """Inverse of to_dict; tolerant of missing season/episode."""
        return cls(
            sync_sub=d["sync_sub"],
            folder=d["folder"],
            season=d.get("season"),
            episode=d.get("episode"),
        )


@dataclass(frozen=True)
class WatchedRef:
    """Identifies a watched-in-synced title group."""

    lib: str
    folder: str

    def to_dict(self) -> dict:
        """JSON-serialisable dict."""
        return {"lib": self.lib, "folder": self.folder}

    @classmethod
    def from_dict(cls, d: dict) -> WatchedRef:
        """Inverse of to_dict."""
        return cls(lib=d["lib"], folder=d["folder"])


@dataclass(frozen=True)
class HangingRef:
    """Identifies a hanging file by absolute path."""

    path: str

    def to_dict(self) -> dict:
        """JSON-serialisable dict."""
        return {"path": self.path}

    @classmethod
    def from_dict(cls, d: dict) -> HangingRef:
        """Inverse of to_dict."""
        return cls(path=d["path"])


# ── Store I/O ────────────────────────────────────────────────────────────────


@dataclass
class IgnoredState:
    """The three per-kind ignored sets, loaded together."""

    pending: set[PendingRef]
    watched: set[WatchedRef]
    hanging: set[HangingRef]

    def to_dict(self) -> dict:
        """JSON-serialisable shape: lists per kind, sorted for stable diffs."""
        return {
            "version": 1,
            "pending": sorted(
                (p.to_dict() for p in self.pending),
                key=lambda d: (d["sync_sub"], d["folder"], d.get("season") or -1, d.get("episode") or -1),
            ),
            "watched": sorted(
                (w.to_dict() for w in self.watched),
                key=lambda d: (d["lib"], d["folder"]),
            ),
            "hanging": sorted(
                (h.to_dict() for h in self.hanging),
                key=lambda d: d["path"],
            ),
        }


def load() -> IgnoredState:
    """Return the persisted state, or an empty state if the file is missing.

    A missing or corrupt file is treated as empty; this is a UI hint
    surface, not a load-bearing data store, so silent-clear is the right
    failure mode (better than blocking maintenance actions on a parse
    error).
    """
    if not IGNORED_FILE.exists():
        return IgnoredState(pending=set(), watched=set(), hanging=set())
    try:
        raw = json.loads(IGNORED_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return IgnoredState(pending=set(), watched=set(), hanging=set())
    return IgnoredState(
        pending={
            PendingRef.from_dict(d) for d in raw.get("pending", []) if isinstance(d, dict)
        },
        watched={
            WatchedRef.from_dict(d) for d in raw.get("watched", []) if isinstance(d, dict)
        },
        hanging={
            HangingRef.from_dict(d) for d in raw.get("hanging", []) if isinstance(d, dict)
        },
    )


def save(state: IgnoredState) -> None:
    """Persist atomically (tempfile + rename) so a crash can't corrupt."""
    IGNORED_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = IGNORED_FILE.with_suffix(IGNORED_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2))
    tmp.replace(IGNORED_FILE)


# ── Mutation API ─────────────────────────────────────────────────────────────


def ignore_pending(ref: PendingRef) -> None:
    """Add a pending key to the ignored set; idempotent."""
    state = load()
    state.pending.add(ref)
    save(state)


def unignore_pending(ref: PendingRef) -> None:
    """Remove a pending key from the ignored set; idempotent."""
    state = load()
    state.pending.discard(ref)
    save(state)


def ignore_watched(ref: WatchedRef) -> None:
    """Add a watched (lib, folder) to the ignored set; idempotent."""
    state = load()
    state.watched.add(ref)
    save(state)


def unignore_watched(ref: WatchedRef) -> None:
    """Remove a watched (lib, folder) from the ignored set; idempotent."""
    state = load()
    state.watched.discard(ref)
    save(state)


def ignore_hanging(ref: HangingRef) -> None:
    """Add a hanging file path to the ignored set; idempotent."""
    state = load()
    state.hanging.add(ref)
    save(state)


def unignore_hanging(ref: HangingRef) -> None:
    """Remove a hanging file path from the ignored set; idempotent."""
    state = load()
    state.hanging.discard(ref)
    save(state)


# ── Filter helpers (used by producers) ───────────────────────────────────────


def ignored_pending_set() -> set[PendingRef]:
    """Snapshot of the ignored-pending set for the current request."""
    return load().pending


def ignored_watched_set() -> set[WatchedRef]:
    """Snapshot of the ignored-watched set for the current request."""
    return load().watched


def ignored_hanging_set() -> set[HangingRef]:
    """Snapshot of the ignored-hanging set for the current request."""
    return load().hanging


# ── List for the UI ──────────────────────────────────────────────────────────


def list_grouped() -> dict:
    """Return all ignored entries grouped by kind for the Ignored UI section."""
    state = load()
    return state.to_dict()


def total_ignored() -> int:
    """Total count across all three kinds; surfaced for UX summaries."""
    state = load()
    return len(state.pending) + len(state.watched) + len(state.hanging)


# ── Bulk ignore by ref dict (called by the route layer) ──────────────────────


def ignore_ref(kind: str, ref: dict) -> bool:
    """Dispatch an ignore call by kind discriminator; returns True on success.

    Validates the kind and the ref shape per kind. Returns False if either is
    malformed so the caller can surface a friendly error.
    """
    try:
        if kind == "pending":
            ignore_pending(PendingRef.from_dict(ref))
        elif kind == "watched":
            ignore_watched(WatchedRef.from_dict(ref))
        elif kind == "hanging":
            ignore_hanging(HangingRef.from_dict(ref))
        else:
            return False
    except (KeyError, TypeError):
        return False
    return True


def unignore_ref(kind: str, ref: dict) -> bool:
    """Inverse of ignore_ref."""
    try:
        if kind == "pending":
            unignore_pending(PendingRef.from_dict(ref))
        elif kind == "watched":
            unignore_watched(WatchedRef.from_dict(ref))
        elif kind == "hanging":
            unignore_hanging(HangingRef.from_dict(ref))
        else:
            return False
    except (KeyError, TypeError):
        return False
    return True
