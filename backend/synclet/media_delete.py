"""Delete a selected source media item and its offline copy with bounded paths.

Every call plans from the current disk tree. The preview fingerprint must still
match at deletion time, so a stale confirmation cannot target changed files.
The source and synced roots are both checked again before each unlink.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from synclet import config, followed, maint_cache, pending, state, sync_ops
from synclet.scan import _EP_PAT, scan_title_detail


@dataclass(frozen=True)
class DeletePlan:
    source_files: tuple[Path, ...]
    offline_files: tuple[Path, ...]
    fingerprint: str
    source_bytes: int
    offline_bytes: int

    @property
    def source_file_count(self) -> int:
        return len(self.source_files)

    @property
    def offline_file_count(self) -> int:
        return len(self.offline_files)

    def to_dict(self) -> dict:
        return {
            "source_files": self.source_file_count,
            "offline_files": self.offline_file_count,
            "source_bytes": self.source_bytes,
            "offline_bytes": self.offline_bytes,
            "fingerprint": self.fingerprint,
        }


def _title_root(lib: str, folder: str) -> Path:
    if lib not in config.LIBRARIES:
        raise ValueError("unknown library")
    if (
        folder in ("", ".", "..")
        or folder.startswith(".")
        or "/" in folder
        or "\\" in folder
    ):
        raise ValueError("invalid title folder")
    library = config.MEDIA_ROOT / lib
    title = library / folder
    if library.is_symlink() or title.is_symlink():
        raise ValueError("symlinked library or title is not deletable")
    if not title.is_dir():
        raise ValueError("title does not exist")
    if not title.resolve(strict=True).is_relative_to(library.resolve(strict=True)):
        raise ValueError("title escapes library")
    return title


def _check_regular_file(path: Path, root: Path) -> os.stat_result:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("media file escapes title") from exc
    current = root
    if current.is_symlink():
        raise ValueError("symlinked title is not deletable")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlinked media is not deletable")
    if not path.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
        raise ValueError("media file escapes title")
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("non-regular media is not deletable")
    return metadata


def _walk_files(root: Path) -> list[Path]:
    out: list[Path] = []

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_symlink():
                    raise ValueError("symlinked media is not deletable")
                if entry.is_dir(follow_symlinks=False):
                    visit(path)
                elif entry.is_file(follow_symlinks=False):
                    out.append(path)
                else:
                    raise ValueError("non-regular media is not deletable")

    visit(root)
    return out


def _source_files(
    lib: str, folder: str, selection_type: str, episodes: list[tuple[int, int]]
) -> tuple[Path, list[Path]]:
    title = _title_root(lib, folder)
    detail = scan_title_detail(lib, folder)
    if detail is None:
        raise ValueError("title could not be scanned")

    if detail.kind == "movie":
        if selection_type != "movie" or episodes:
            raise ValueError("movie deletion requires movie selection")
        selected = _walk_files(title)
    else:
        if selection_type != "episodes" or not episodes:
            raise ValueError("show deletion requires selected episodes")
        requested = set(episodes)
        available = {
            (ep.season, ep.episode): ep
            for season in detail.seasons
            for ep in season.episodes
            if any(Path(file).suffix.lower() in config.VIDEO_EXTS for file in ep.files)
        }
        if not requested <= available.keys():
            raise ValueError("one or more selected episodes no longer exist")
        selected_set: set[Path] = set()
        for key in requested:
            ep = available[key]
            parent = Path(ep.files[0]).parent
            if parent.is_symlink():
                raise ValueError("symlinked season is not deletable")
            for path in parent.iterdir():
                match = _EP_PAT.search(path.name)
                if match and (int(match.group(1)), int(match.group(2))) == key:
                    if path.is_dir():
                        raise ValueError("episode path is a directory")
                    selected_set.add(path)
        selected = sorted(selected_set)

    if not selected or not any(p.suffix.lower() in config.VIDEO_EXTS for p in selected):
        raise ValueError("selection has no video files")
    for path in selected:
        _check_regular_file(path, title)
    # Leave videos until the end. On a partial failure, the remaining video
    # keeps the episode selectable for a safe retry of its sidecars.
    return title, sorted(
        selected,
        key=lambda path: (path.suffix.lower() in config.VIDEO_EXTS, str(path)),
    )


def _offline_files(
    lib: str, folder: str, selection_type: str, episodes: list[tuple[int, int]]
) -> tuple[Path, list[Path]]:
    sub = config.LIBRARIES[lib]["sync_sub"]
    subroot = config.SYNC_ROOT / sub
    root = subroot / folder
    if subroot.is_symlink() or root.is_symlink():
        raise ValueError("symlinked offline path is not deletable")
    if not root.exists():
        return root, []
    if not root.resolve(strict=True).is_relative_to(
        config.SYNC_ROOT.resolve(strict=True)
    ):
        raise ValueError("offline title escapes sync root")
    # Two source libraries sharing a sync sub can have the same title folder.
    # In that case the offline copy has no unambiguous owner, so leave it alone.
    owners = [
        candidate
        for candidate, info in config.LIBRARIES.items()
        if info["sync_sub"] == sub and (config.MEDIA_ROOT / candidate / folder).is_dir()
    ]
    if len(owners) > 1:
        raise ValueError("offline title belongs to multiple source libraries")
    targets = sync_ops.resolve_unsync_selection(
        lib,
        folder,
        selection_type=selection_type,
        episodes=[[season, episode] for season, episode in episodes],
    )
    for path in targets:
        _check_regular_file(path, root)
    return root, sorted(targets)


def plan_selection(
    lib: str, folder: str, selection_type: str, episodes: list[tuple[int, int]]
) -> DeletePlan:
    source_root, source_files = _source_files(lib, folder, selection_type, episodes)
    offline_root, offline_files = _offline_files(lib, folder, selection_type, episodes)
    digest = hashlib.sha256()
    source_bytes = offline_bytes = 0
    for label, root, paths in (
        ("source", source_root, source_files),
        ("offline", offline_root, offline_files),
    ):
        for path in paths:
            metadata = _check_regular_file(path, root)
            digest.update(
                f"{label}/{path.relative_to(root)}\0{metadata.st_dev}\0{metadata.st_ino}\0{metadata.st_size}\0{metadata.st_mtime_ns}\0{metadata.st_ctime_ns}\n".encode()
            )
            if label == "source":
                source_bytes += metadata.st_size
            else:
                offline_bytes += metadata.st_size
    return DeletePlan(
        source_files=tuple(source_files),
        offline_files=tuple(offline_files),
        fingerprint=digest.hexdigest(),
        source_bytes=source_bytes,
        offline_bytes=offline_bytes,
    )


def _prune_empty_parents(paths: tuple[Path, ...], title_root: Path) -> None:
    parents = {
        parent
        for path in paths
        for parent in path.parents
        if parent == title_root or title_root in parent.parents
    }
    for parent in sorted(parents, key=lambda p: len(p.parts), reverse=True):
        with contextlib.suppress(OSError):
            parent.rmdir()


def delete_selection(
    lib: str,
    folder: str,
    selection_type: str,
    episodes: list[tuple[int, int]],
    fingerprint: str,
) -> dict:
    """Delete exactly the previewed files, then invalidate all affected views."""
    plan = plan_selection(lib, folder, selection_type, episodes)
    if plan.fingerprint != fingerprint:
        raise ValueError("media changed since preview; preview again")
    if sync_ops.has_active_job_for_title(f"{lib}/{folder}"):
        raise ValueError("wait for the active sync job before deleting media")
    source_root = _title_root(lib, folder)
    offline_root = config.SYNC_ROOT / config.LIBRARIES[lib]["sync_sub"] / folder
    for root, paths in (
        (source_root, plan.source_files),
        (offline_root, plan.offline_files),
    ):
        for path in paths:
            _check_regular_file(path, root)
            if not os.access(path.parent, os.W_OK):
                raise ValueError("media folder is not writable")
        if paths and os.statvfs(root).f_flag & os.ST_RDONLY:
            raise ValueError("media mount is read-only")

    deleted_offline: list[Path] = []
    deleted_source: list[Path] = []
    deleted_offline_bytes = 0
    deleted_source_bytes = 0
    failure: str | None = None
    current_path: Path | None = None
    try:
        # Remove offline copies first. If they cannot be removed, source media
        # stays available and the user can retry without orphaning a copy.
        for path in plan.offline_files:
            current_path = path
            _check_regular_file(path, offline_root)
            size = path.stat().st_size
            path.unlink()
            deleted_offline.append(path)
            deleted_offline_bytes += size
        for path in plan.source_files:
            current_path = path
            _check_regular_file(path, source_root)
            size = path.stat().st_size
            path.unlink()
            deleted_source.append(path)
            deleted_source_bytes += size
    except (OSError, ValueError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) else str(exc)
        failure = f"Stopped at {current_path.name if current_path else 'media file'}: {reason or str(exc)}"
    finally:
        keys = {
            pending.path_to_snapshot_key(path)
            for path in deleted_offline
            if path.suffix.lower() in config.VIDEO_EXTS
        }
        pending.remove_keys(k for k in keys if k is not None)
        _prune_empty_parents(tuple(deleted_offline), offline_root)
        _prune_empty_parents(tuple(deleted_source), source_root)
        if not source_root.exists():
            followed.set_following(lib, folder, False)
        state.invalidate()
        maint_cache.invalidate()
    return {
        "source_deleted": len(deleted_source),
        "offline_deleted": len(deleted_offline),
        "source_bytes": deleted_source_bytes,
        "offline_bytes": deleted_offline_bytes,
        "title_remaining": source_root.exists(),
        "error": failure,
    }
