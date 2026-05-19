"""Deletion-driven watch-state write-back.

When a file disappears from SYNC_ROOT, we infer the user finished watching it
(or chose not to) and surface a confirm/reject decision. The single source of
truth is `snapshot.json`: the set of media items Synclet last knew were synced.

Pending = snapshot - on-disk. Resolve (confirm or reject) removes the item from
the snapshot; confirm additionally scrobbles the item to Plex via
`synclet.plex.scrobble`.

Bootstrap: if the snapshot file does not yet exist, it is initialised from the
current on-disk state, so the first run produces zero pending items.

The snapshot key is `(sync_sub, folder, season, episode)` for shows and
YouTube, and `(sync_sub, folder)` for movies (season and episode are None).
`sync_sub` plus `folder` is unique inside SYNC_ROOT. The source library is
resolved lazily via sync_ops.find_source_lib when metadata is needed.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from operator import itemgetter
from pathlib import Path
from typing import TYPE_CHECKING

from synclet.config import LIBRARIES, SNAPSHOT_FILE, SYNC_ROOT, VIDEO_EXTS
from synclet.fs_helpers import iter_sync_subs

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Literal

    # Wire contract with the frontend (see ResolveStatus in types.ts). A typo
    # in any of these strings is a Python type error AND a TS type error,
    # which is the layered guarantee we want.
    ResolveStatusType = Literal["ok", "scrobble_failed", "no_rating_key", "rejected"]

_EP_PAT = re.compile(r"[Ss](\d+)[Ee](\d+)", re.IGNORECASE)


# ── Sync-sub kind map (derived from LIBRARIES, stable per process) ──────────


def _sync_sub_kinds() -> dict[str, str]:
    """{sync_sub: kind} derived from LIBRARIES.

    DO NOT call from import time. LIBRARIES can be monkeypatched in tests, and
    a module-level cache would freeze the test fixture's view. Cheap to rebuild
    (5 entries today).
    """
    out: dict[str, str] = {}
    for info in LIBRARIES.values():
        out[info["sync_sub"]] = info["kind"]
    return out


# ── Snapshot key ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SnapshotKey:
    """Identifies a synced media item.

    For shows and youtube, season and episode are populated. For movies, both
    are None and the (sync_sub, folder) pair fully identifies the item.
    """

    sync_sub: str
    folder: str
    season: int | None = None
    episode: int | None = None

    def to_dict(self) -> dict:
        """JSON-serialisable dict form, matches the snapshot file schema."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SnapshotKey:
        """Inverse of to_dict; tolerant of missing season/episode (movies)."""
        return cls(
            sync_sub=d["sync_sub"],
            folder=d["folder"],
            season=d.get("season"),
            episode=d.get("episode"),
        )


# ── Path -> SnapshotKey ─────────────────────────────────────────────────────


# Minimum SYNC_ROOT-relative path depth that can identify a media item: at
# least <sync_sub>/<folder>/... — anything shallower is a stray top-level file.
_MIN_REL_PARTS = 2


def path_to_snapshot_key(path: Path) -> SnapshotKey | None:  # noqa: PLR0911 — guards
    """Reverse a synced file path to its SnapshotKey.

    Returns None for paths outside SYNC_ROOT, non-video files, or paths whose
    sync_sub is not in the current LIBRARIES config (stale leftover sync sub
    that we no longer manage).
    """
    if path.suffix.lower() not in VIDEO_EXTS:
        return None
    try:
        rel = path.relative_to(SYNC_ROOT)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < _MIN_REL_PARTS:
        return None
    sync_sub, folder = parts[0], parts[1]
    kind = _sync_sub_kinds().get(sync_sub)
    if kind is None:
        return None
    if kind == "movie":
        return SnapshotKey(sync_sub=sync_sub, folder=folder)
    # show / youtube: extract season+episode from the filename
    match = _EP_PAT.search(path.name)
    if not match:
        return None
    return SnapshotKey(
        sync_sub=sync_sub,
        folder=folder,
        season=int(match.group(1)),
        episode=int(match.group(2)),
    )


# ── On-disk scan ────────────────────────────────────────────────────────────


def scan_on_disk() -> set[SnapshotKey]:
    """Walk SYNC_ROOT and return the set of currently-present SnapshotKeys.

    Non-video files (subtitles, sidecars) are ignored: the snapshot tracks
    media items, not individual files. A show episode is "on disk" if any of
    its video files exists.
    """
    out: set[SnapshotKey] = set()
    for sub_path in iter_sync_subs():
        for video in _iter_videos(sub_path):
            key = path_to_snapshot_key(video)
            if key is not None:
                out.add(key)
    return out


def _iter_videos(root: Path) -> Iterable[Path]:
    """Yield every video file under root, skipping dotfiles and hidden dirs.

    Uses os.scandir for efficiency: SYNC_ROOT can have thousands of files
    across a few hundred folders, and Path.rglob is roughly an order of
    magnitude slower on shfs FUSE.
    """
    for entry in os.scandir(root):
        name = entry.name
        if name.startswith("."):
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                yield from _iter_videos(Path(entry.path))
            elif entry.is_file(follow_symlinks=False) and (
                Path(entry.path).suffix.lower() in VIDEO_EXTS
            ):
                yield Path(entry.path)
        except OSError:
            continue


# ── Snapshot I/O ────────────────────────────────────────────────────────────


def load_snapshot() -> set[SnapshotKey]:
    """Return the persisted snapshot, or an empty set if the file is missing.

    A missing file means "not bootstrapped yet" — callers should run
    bootstrap_if_missing() before computing pending, otherwise every existing
    on-disk file would erroneously appear as a deletion candidate the first
    time the feature is used.
    """
    if not SNAPSHOT_FILE.exists():
        return set()
    try:
        raw = json.loads(SNAPSHOT_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    items = raw.get("items", [])
    return {SnapshotKey.from_dict(item) for item in items if isinstance(item, dict)}


def save_snapshot(keys: set[SnapshotKey]) -> None:
    """Persist the snapshot atomically.

    Writes to a sibling tempfile then renames, so a crashed write cannot
    leave a half-written JSON file that load_snapshot would silently swallow.
    """
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "items": sorted(
            (k.to_dict() for k in keys),
            key=lambda d: (
                d["sync_sub"],
                d["folder"],
                d.get("season") or -1,
                d.get("episode") or -1,
            ),
        ),
    }
    tmp = SNAPSHOT_FILE.with_suffix(SNAPSHOT_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(SNAPSHOT_FILE)


def bootstrap_if_missing() -> bool:
    """If the snapshot file does not exist, write one matching current disk.

    Returns True if bootstrap fired (cold-start state), False otherwise.
    """
    if SNAPSHOT_FILE.exists():
        return False
    save_snapshot(scan_on_disk())
    return True


# ── Snapshot mutation hooks ─────────────────────────────────────────────────


def add_keys(keys: Iterable[SnapshotKey]) -> None:
    """Add items to the snapshot. Called when a sync job completes."""
    keys = set(keys)
    if not keys:
        return
    bootstrap_if_missing()
    current = load_snapshot()
    save_snapshot(current | keys)


def remove_keys(keys: Iterable[SnapshotKey]) -> None:
    """Remove items from the snapshot.

    Called for two implicit-confirm paths: the maintenance "remove watched"
    flow (the user is removing precisely because they watched), and the
    explicit resolve flow whether confirm or reject.
    """
    keys = set(keys)
    if not keys:
        return
    current = load_snapshot()
    new = current - keys
    if new != current:
        save_snapshot(new)


def keys_for_paths(paths: Iterable[str | Path]) -> set[SnapshotKey]:
    """Translate a batch of file paths into SnapshotKeys.

    Non-video paths and paths outside SYNC_ROOT are silently dropped. Used by
    sync_ops.remove_files to update the snapshot when files are deleted via
    the maintenance UI.
    """
    out: set[SnapshotKey] = set()
    for raw in paths:
        key = path_to_snapshot_key(Path(raw))
        if key is not None:
            out.add(key)
    return out


# ── Pending computation ─────────────────────────────────────────────────────


def compute_pending() -> set[SnapshotKey]:
    """Return the set of SnapshotKeys present in the snapshot but not on disk."""
    bootstrap_if_missing()
    return load_snapshot() - scan_on_disk()


# ── Post-resolve filesystem cleanup ─────────────────────────────────────────


def _prune_empty_ancestors(start: Path, stop: Path) -> int:
    """Remove empty dirs from `start` walking upward, never crossing `stop`.

    Returns the number of directories removed. Both `start` and `stop` are
    inclusive at their respective ends: `stop` is the floor (never removed).
    """
    removed = 0
    current = start
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            break
        removed += 1
        current = current.parent
    return removed


def cleanup_after_resolve(key: SnapshotKey) -> dict[str, int]:
    """Sweep orphan sidecars + prune empty ancestor dirs for a resolved item.

    Only runs when the matching video is genuinely gone. For shows this means
    no file with the same `SxxEyy` pattern survives in the season dir; for
    movies it means no video file of any kind remains in the title folder.
    The presence of a video is the safety latch: it prevents stripping subs
    off an episode that's still synced (race / hand edit / partial delete).

    Returns counts so the API can surface a "removed N files, M folders" hint.
    """
    counts = {"removed_files": 0, "removed_dirs": 0}

    sub_root = SYNC_ROOT / key.sync_sub
    folder_path = sub_root / key.folder
    if not folder_path.exists():
        return counts

    if key.season is None or key.episode is None:
        # Movie: sweep when no videos remain in the title folder.
        videos_remaining = any(
            f.is_file() and f.suffix.lower() in VIDEO_EXTS
            for f in folder_path.iterdir()
        )
        if videos_remaining:
            return counts
        for f in list(folder_path.iterdir()):
            if f.is_file():
                try:
                    f.unlink()
                    counts["removed_files"] += 1
                except OSError:
                    continue
        # rmdir the title folder + walk up to (but not into) sub_root.
        counts["removed_dirs"] += _prune_empty_ancestors(folder_path, sub_root)
        return counts

    # Show / youtube: match SxxEyy on filenames inside the season dir(s).
    # Multiple season dirs with the same number are uncommon but possible
    # (e.g. "Season 01" vs "Specials 01"); iterate all that parse to the key.
    from synclet.scan import _season_num  # noqa: PLC0415

    ep_pat = re.compile(
        rf"[Ss]{key.season:02d}[Ee]{key.episode:02d}(?!\d)|"
        rf"[Ss]{key.season}[Ee]{key.episode:04d}(?!\d)",
        re.IGNORECASE,
    )
    season_dirs = [
        d
        for d in folder_path.iterdir()
        if d.is_dir() and _season_num(d) == key.season
    ]
    for season_dir in season_dirs:
        matching = [f for f in season_dir.iterdir() if ep_pat.search(f.name)]
        videos_remaining = any(
            f.suffix.lower() in VIDEO_EXTS for f in matching
        )
        if videos_remaining:
            continue
        for f in matching:
            if f.is_file():
                try:
                    f.unlink()
                    counts["removed_files"] += 1
                except OSError:
                    continue
        # If the season dir is now empty, rmdir it and walk up the show folder.
        if not any(season_dir.iterdir()):
            counts["removed_dirs"] += _prune_empty_ancestors(season_dir, sub_root)
    return counts


def aggregate_cleanup(keys: Iterable[SnapshotKey]) -> dict[str, int]:
    """Run cleanup_after_resolve over a batch and sum the counts."""
    total = {"removed_files": 0, "removed_dirs": 0}
    for k in keys:
        c = cleanup_after_resolve(k)
        total["removed_files"] += c["removed_files"]
        total["removed_dirs"] += c["removed_dirs"]
    return total


# ── Grouping for the API response ───────────────────────────────────────────


def grouped_pending() -> list[dict]:
    """Return pending items grouped by show/movie, enriched with Plex metadata.

    Shape (consumed by the frontend MaintenanceView pending pane):

        [
          {
            "sync_sub": "tv", "folder": "Better Call Saul (2015)...",
            "title": "Better Call Saul", "kind": "show",
            "lib": "tv", "rating_key": "100" or null,
            "seasons": [
              {
                "season": 1,
                "episodes": [
                  {"season": 1, "episode": 1,
                   "already_watched_in_plex": false,
                   "episode_rating_key": "1001" or null,
                   "title": "Uno"}
                ]
              }
            ]
          },
          {
            "sync_sub": "movies", "folder": "1917 (2019)...",
            "title": "1917", "kind": "movie",
            "lib": "movies", "rating_key": "300" or null,
            "already_watched_in_plex": false
          }
        ]

    Plex enrichment is best-effort. If section_index() returns nothing (Plex
    down, library re-scanning), rating_keys come back None and the UI shows
    the entries with reject-only affordances; confirm will fail per-item
    rather than the whole list failing to render.
    """
    # Local imports avoid cycles: plex -> scan -> ... and sync_ops needs us.
    from synclet.plex import episode_rating_keys, find_in_library  # noqa: PLC0415
    from synclet.scan import clean_name  # noqa: PLC0415
    from synclet.sync_ops import find_source_lib  # noqa: PLC0415
    from synclet.watchstate import movie_watch_state, show_watch_map  # noqa: PLC0415

    by_folder: dict[tuple[str, str], list[SnapshotKey]] = {}
    for key in compute_pending():
        by_folder.setdefault((key.sync_sub, key.folder), []).append(key)

    out: list[dict] = []
    kinds = _sync_sub_kinds()
    for (sync_sub, folder), keys in sorted(by_folder.items()):
        kind = kinds.get(sync_sub, "show")
        display = clean_name(folder)
        lib = find_source_lib(folder)
        meta = find_in_library(lib, folder) if lib else None
        show_rk = meta["ratingKey"] if meta else None

        if kind == "movie":
            already_watched = bool(movie_watch_state(display))
            out.append(
                {
                    "sync_sub": sync_sub,
                    "folder": folder,
                    "title": display,
                    "kind": kind,
                    "lib": lib,
                    "rating_key": show_rk,
                    "already_watched_in_plex": already_watched,
                },
            )
            continue

        # show / youtube
        ws_map = show_watch_map(display)
        ep_rk_map = episode_rating_keys(show_rk) if show_rk else {}

        seasons: dict[int, list[dict]] = {}
        for k in keys:
            if k.season is None or k.episode is None:
                continue
            episode_entry = {
                "season": k.season,
                "episode": k.episode,
                "already_watched_in_plex": bool(ws_map.get((k.season, k.episode))),
                "episode_rating_key": ep_rk_map.get((k.season, k.episode)),
                "title": "",
            }
            seasons.setdefault(k.season, []).append(episode_entry)

        if not seasons:
            # Hand-edited snapshot with show keys missing season/episode.
            # Don't surface a malformed group; let the snapshot decay naturally.
            continue

        out.append(
            {
                "sync_sub": sync_sub,
                "folder": folder,
                "title": display,
                "kind": kind,
                "lib": lib,
                "rating_key": show_rk,
                "seasons": [
                    {
                        "season": s,
                        "episodes": sorted(eps, key=itemgetter("episode")),
                    }
                    for s, eps in sorted(seasons.items())
                ],
            },
        )
    return out


# ── Resolve orchestration ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolveResult:
    """Per-item outcome of a resolve call.

    `status` is the wire contract field consumed by MaintenanceView.vue
    (see ResolveStatus in types.ts). Mirrored as a Literal alias above for
    typo-safety: a misspelled status here is a Python type error.
    """

    key: SnapshotKey
    status: ResolveStatusType

    def to_dict(self) -> dict:
        """JSON-serialisable dict: SnapshotKey fields plus status."""
        d = self.key.to_dict()
        d["status"] = self.status
        return d


def resolve(
    keys: Iterable[SnapshotKey], *, confirm: bool,
) -> tuple[list[ResolveResult], dict[str, int]]:
    """Resolve a batch of pending keys, sweep filesystem leftovers.

    On reject: every key is dropped from the snapshot, returns status
    "rejected" per key. On confirm: each key is scrobbled to Plex first
    (best-effort, per-item); successful scrobbles get status "ok", failures
    get "scrobble_failed" or "no_rating_key". The snapshot drops every key
    regardless of scrobble outcome — the user already deleted the file, so
    putting it back in pending would be incorrect.

    After the snapshot mutation, runs cleanup_after_resolve for each key to
    sweep orphan subtitle sidecars and prune empty parent dirs. Cleanup is
    safe to run for both confirm and reject because the trigger is "this
    item is gone from disk now," not "the user wants Plex to know."

    Returns (results, cleanup_totals). cleanup_totals has the shape
    {"removed_files": N, "removed_dirs": M} aggregated across the batch.
    """
    # Local imports keep the module's import graph small.
    from synclet.plex import (  # noqa: PLC0415
        episode_rating_keys,
        find_in_library,
        scrobble,
    )
    from synclet.sync_ops import find_source_lib  # noqa: PLC0415

    keys = list(keys)
    results: list[ResolveResult] = []

    if not confirm:
        results.extend(ResolveResult(key=k, status="rejected") for k in keys)
        remove_keys(keys)
        cleanup = aggregate_cleanup(keys)
        return results, cleanup

    for k in keys:
        lib = find_source_lib(k.folder)
        meta = find_in_library(lib, k.folder) if lib else None
        show_rk = meta["ratingKey"] if meta else None
        target_rk: str | None
        if k.season is None or k.episode is None:
            # Movie
            target_rk = show_rk
        elif show_rk:
            target_rk = episode_rating_keys(show_rk).get((k.season, k.episode))
        else:
            target_rk = None

        if target_rk is None:
            results.append(ResolveResult(key=k, status="no_rating_key"))
            continue

        if scrobble(target_rk):
            results.append(ResolveResult(key=k, status="ok"))
        else:
            results.append(ResolveResult(key=k, status="scrobble_failed"))

    remove_keys(keys)
    cleanup = aggregate_cleanup(keys)
    return results, cleanup


# ── Explicit mark-watched (no snapshot mutation, no cleanup) ────────────────


def mark_watched_scope(
    lib: str,
    folder: str,
    scope: str,
    season: int | None = None,
    episode: int | None = None,
) -> dict:
    """Scrobble Plex for an explicit user gesture, no filesystem mutation.

    Distinct from `resolve(confirm=True)`:
    - The file is still on disk; this is "Plex's watch state drifted, sync it
      up." The snapshot is NOT mutated, no orphan cleanup runs.
    - Scope determines how the lib/folder pair expands to individual scrobble
      targets:
        "movie"   -> scrobble the movie's ratingKey
        "episode" -> scrobble (season, episode)
        "season"  -> scrobble every episode where parentIndex == season
        "series"  -> scrobble every episode in the show

    Returns {"scrobbled": N, "failed": N, "results": [...]} where each result
    has the per-item scrobble status. A no_rating_key result means Plex's
    library doesn't have the item (folder mismatch or library scanning).
    """
    from synclet.plex import (  # noqa: PLC0415
        episode_rating_keys,
        find_in_library,
        scrobble,
    )

    if scope not in {"movie", "series", "season", "episode"}:
        return {
            "scrobbled": 0,
            "failed": 0,
            "results": [],
            "error": f"unknown scope: {scope}",
        }

    meta = find_in_library(lib, folder)
    show_or_movie_rk = meta["ratingKey"] if meta else None
    if not show_or_movie_rk:
        return {
            "scrobbled": 0,
            "failed": 1 if scope == "movie" else 0,
            "results": [
                {
                    "season": season,
                    "episode": episode,
                    "status": "no_rating_key",
                },
            ]
            if scope == "movie"
            else [],
            "error": (
                None
                if scope != "movie"
                else f"no Plex item for {lib}/{folder}"
            ),
        }

    targets: list[tuple[str, int | None, int | None]] = []
    if scope == "movie":
        targets.append((show_or_movie_rk, None, None))
    else:
        ep_map = episode_rating_keys(show_or_movie_rk)
        if scope == "episode":
            if season is None or episode is None:
                return {
                    "scrobbled": 0,
                    "failed": 0,
                    "results": [],
                    "error": "season and episode required for scope=episode",
                }
            rk = ep_map.get((season, episode))
            if rk is None:
                return {
                    "scrobbled": 0,
                    "failed": 1,
                    "results": [
                        {"season": season, "episode": episode, "status": "no_rating_key"},
                    ],
                }
            targets.append((rk, season, episode))
        elif scope == "season":
            if season is None:
                return {
                    "scrobbled": 0,
                    "failed": 0,
                    "results": [],
                    "error": "season required for scope=season",
                }
            for (s, e), rk in sorted(ep_map.items()):
                if s == season:
                    targets.append((rk, s, e))
        else:  # series
            for (s, e), rk in sorted(ep_map.items()):
                targets.append((rk, s, e))

    results: list[dict] = []
    scrobbled = 0
    failed = 0
    for rk, s, e in targets:
        ok = scrobble(rk)
        results.append(
            {"season": s, "episode": e, "status": "ok" if ok else "scrobble_failed"},
        )
        if ok:
            scrobbled += 1
        else:
            failed += 1
    return {"scrobbled": scrobbled, "failed": failed, "results": results}
