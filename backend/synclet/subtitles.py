"""Title-only subtitle evidence. No movie probing or provider requests."""

from __future__ import annotations

from pathlib import Path

from synclet import plex
from synclet.config import ENGLISH_CODES, SUBTITLE_EXTS
from synclet.fs_helpers import verify_subtitle
from synclet.scan import TitleDetail


def reported_tracks(lib: str, folder: str) -> dict[str, list[dict]]:
    """Plex's title metadata is evidence, not a fresh analysis of the MKV."""
    key = plex.cached_rating_key(lib, folder)
    if key is None:
        return {}
    root = plex._get_xml(f"/library/metadata/{key}")
    if root is None:
        return {}
    return {
        part.get("file", ""): [
            {
                "language": stream.get("languageCode", ""),
                "forced": stream.get("forced") == "1",
                "external": bool(stream.get("key")),
            }
            for stream in part.findall("Stream")
            if stream.get("streamType") == "3"
        ]
        for part in root.iter("Part")
    }


def assess(detail: TitleDetail, *, require_forced: bool = False) -> dict:
    """Inspect only this movie's files, cross-checking reported external tracks.

    An explicit caller requirement or an English forced external track reported
    by Plex is positive evidence. An absent track says nothing about translated
    dialogue or burned-in captions. Other languages never satisfy English need.
    """
    from synclet.sync_ops import sync_dest

    tracks = reported_tracks(detail.lib, detail.folder)
    checks = []
    for entry in detail.files:
        if not entry["is_video"]:
            continue
        video = Path(entry["path"])
        sidecars = [
            Path(f["path"])
            for f in detail.files
            if Path(f["path"]).suffix.lower() in SUBTITLE_EXTS
            and (
                Path(f["path"]).stem == video.stem
                or Path(f["path"]).stem.startswith(video.stem + ".")
            )
        ]
        forced = [
            f
            for f in sidecars
            if "forced" in f.stem[len(video.stem) :].lower().split(".")
            and any(
                lang in f.stem[len(video.stem) :].lower().split(".")
                for lang in ENGLISH_CODES
            )
            and f.is_file()
            and f.stat().st_size > 0
        ]
        reported = tracks.get(str(video))
        english_forced = [
            t
            for t in (reported or [])
            if t["forced"] and t["language"] in ENGLISH_CODES
        ]
        embedded = any(not t["external"] for t in english_forced)
        external_expected = any(t["external"] for t in english_forced)
        missing = (require_forced or external_expected) and not (forced or embedded)
        warnings = []
        if missing:
            warnings.append(
                "English forced subtitles expected but no usable sidecar or reported embedded track was found."
            )
        copies = []
        for src in sidecars:
            dst = sync_dest(src, detail.lib)
            try:
                verify_subtitle(src, dst)
                state = "verified"
            except OSError:
                state = "not_verified"
            copies.append(
                {"name": src.name, "source_exists": src.is_file(), "offline": state}
            )
        status = (
            "required_forced_missing"
            if missing
            else "forced_sidecar_available"
            if forced
            else "embedded_forced_reported"
            if embedded
            else "no_forced_track_reported"
            if reported is not None
            else "tracks_unknown"
        )
        checks.append(
            {
                "video": video.name,
                "status": status,
                "requirement": "caller"
                if require_forced
                else "plex_external"
                if external_expected
                else "unknown",
                "metadata_available": reported is not None,
                "sidecars": copies,
                "warnings": warnings,
            }
        )
    return {
        "videos": checks,
        "warnings": [w for c in checks for w in c["warnings"]],
        "note": "Plex track metadata can be stale. Need for forced dialogue and burned-in captions is unknown; no movie was analyzed.",
    }
