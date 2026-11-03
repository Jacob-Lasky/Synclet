"""Filesystem scan: turn the library directory layout into structured data.

The grid's title list comes from synclet.library's cached listing, never from
listing the library roots per request or per cache pass. The grid scan does no
per-file stat; sizes are computed only inside title-detail responses, which
walk one title's folder live.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from synclet.config import (
    ENGLISH_CODES,
    EXTERNAL_ID_SCHEMES,
    LANGUAGE_CODE_QUALIFIERS,
    LIBRARIES,
    MEDIA_ROOT,
    SUBTITLE_EXTS,
    SUBTITLE_QUALIFIERS,
    SYNC_ROOT,
    VIDEO_EXTS,
)
from synclet.episodes import EPISODE_PAT
from synclet.fs_helpers import iter_sync_subs

# Strips the {tvdb-...}/{tmdb-...}/{imdb-tt...} suffix Sonarr/Radarr append, so
# clean_name and watchstate_key produce the bare title WatchState/Plex store.
# Driven off EXTERNAL_ID_SCHEMES so it covers every scheme the ID join does ,
# without imdb here an {imdb-tt...} folder kept its cruft and missed the title
# key (the ID join now backstops it, but the keys should agree on their own).
_ID_CRUFT = re.compile(r"\s*\{(?:" + "|".join(EXTERNAL_ID_SCHEMES) + r")-(?:tt)?\d+\}")
# Capturing form of _ID_CRUFT: pulls the (scheme, id) out instead of stripping
# it. Used to join a synced folder to its Plex / WatchState entry by stable
# external ID when display titles disagree (Plex stylizes "Pluribus" as
# "PLUR1BUS", so the title key misses but {tvdb-436457} still matches Plex's
# Guid). Same config-sourced schemes as _ID_CRUFT and plex.py's Guid parser.
_EXTERNAL_ID = re.compile(r"\{(" + "|".join(EXTERNAL_ID_SCHEMES) + r")-(tt\d+|\d+)\}")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# A language tag in a subtitle name: an ISO 639 code ("en", "eng"), or a
# two-letter one with a script and/or region subtag ("pt-br", "zh-hant-tw").
# Group 1 or 2 is the code. DO NOT allow subtags on three-letter codes: release
# tokens like "web-dl" would read as language "web".
_LANG_TAG = re.compile(r"([a-z]{3})|([a-z]{2})(?:-[a-z]{4})?(?:-(?:[a-z]{2}|\d{3}))?")
_YEAR = re.compile(r"\((\d{4})\)\s*$")
_EP_TITLE_FROM_FILENAME = re.compile(
    r"[Ss]\d+[Ee]\d+\s*-\s*(.+?)(?:\s*[\[\(]|\.[a-z0-9]{2,4}$)", re.IGNORECASE
)


@dataclass
class Title:
    """A show, movie, or YouTube channel as seen from disk."""

    id: str  # f"{lib}/{folder}" , stable composite key
    lib: str
    folder: str  # raw folder name (with {tvdb-...} cruft)
    name: str  # display name (cleaned)
    kind: str  # "show" | "movie" | "youtube"
    year: int | None
    ep_count: int  # 0 for movies; episode count for shows
    synced_files: int  # count of files present in synced-media for this title
    has_synced: bool  # convenience: synced_files > 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lib": self.lib,
            "folder": self.folder,
            "name": self.name,
            "kind": self.kind,
            "year": self.year,
            "ep_count": self.ep_count,
            "synced_files": self.synced_files,
            "has_synced": self.has_synced,
        }


@dataclass
class Episode:
    season: int
    episode: int
    title: str
    size_bytes: int
    files: list[str] = field(default_factory=list)  # absolute paths
    has_video: bool = False
    is_synced: bool = False
    watch_state: str = "unwatched"  # "unwatched" | "watched" | "progress"
    watch_pct: int = 0


@dataclass
class Season:
    season: int
    episodes: list[Episode]
    total_bytes: int
    synced_episodes: int
    watched_episodes: int


@dataclass
class TitleDetail:
    id: str
    lib: str
    folder: str
    name: str
    kind: str
    year: int | None
    seasons: list[Season] = field(default_factory=list)
    # For movies and YouTube channels
    files: list[dict] = field(default_factory=list)
    total_bytes: int = 0
    synced_bytes: int = 0


# ── Helpers ────────────────────────────────────────────────────────────────


def clean_name(folder: str) -> str:
    return _ID_CRUFT.sub("", folder).strip()


def watchstate_key(folder_or_name: str) -> str:
    """Lowercased title without the {tvdb/tmdb/imdb-...} suffix or trailing
    (YYYY) , matches the `state.title` shape that WatchState writes (Plex-side
    title, no year)."""
    s = _ID_CRUFT.sub("", folder_or_name).strip()
    s = re.sub(r"\s*\(\d{4}\)\s*$", "", s).strip()
    return s.lower()


def folder_external_ids(folder_or_name: str) -> dict[str, str]:
    """Extract external IDs embedded in a folder name.

    'Pluribus (2025) {tvdb-436457}' -> {'tvdb': '436457'}. imdb values keep
    their 'tt' prefix to match Plex's `Guid id="imdb://tt..."`. Returns an
    empty dict when no IDs are present (e.g. YouTube channel folders), which is
    the signal for callers to fall back to a title-based join.
    """
    return {m.group(1): m.group(2) for m in _EXTERNAL_ID.finditer(folder_or_name)}


def normalized_key(folder_or_name: str) -> str:
    """watchstate_key with every non-alphanumeric stripped.

    A punctuation-tolerant last-resort join for ID-less items (YouTube
    channels) where Plex drops a separator the folder keeps:
    'Complexly - Ask Hank Anything' and 'Complexly Ask Hank Anything' both
    collapse to 'complexlyaskhankanything'.
    """
    return _NON_ALNUM.sub("", watchstate_key(folder_or_name))


def parse_year(folder: str) -> int | None:
    m = _YEAR.search(clean_name(folder))
    return int(m.group(1)) if m else None


def _language_of(token: str) -> str | None:
    """The primary language code of a language tag token ("pt-br" is "pt")."""
    m = _LANG_TAG.fullmatch(token)
    return (m.group(1) or m.group(2)) if m else None


def _is_tag(token: str) -> bool:
    """A qualifier, or a copy number ("Movie.1.en.srt"); neither is a language."""
    return token in SUBTITLE_QUALIFIERS or token.isdigit()


def _qualifier_follows_language(tokens: list[str]) -> bool:
    """True when the nearest token in `tokens` (those before a qualifier, the
    video's base name excluded), past any other tags, is a language."""
    for token in reversed(tokens):
        if not _is_tag(token):
            return _language_of(token) is not None
    return False


def subtitle_language(f: Path) -> str | None:
    """The primary language code in a subtitle's name, or None when it has none.

    Sonarr, Radarr and Bazarr name a subtitle `<video>[.<copy>].<lang>[.<tag>...]`
    (Sonarr and Radarr also parse tags before the code), so the language is the
    last token that is not a tag (_is_tag): "Show.S01E01.1.en-US.forced.srt" is
    "en". DO NOT look further back than that token: past it is the video's own
    name, where "Mr.Robot.S01E01.srt" would read as Marathi ("mr") and be
    dropped. The first token is always the video's name, never a language
    ("Ted.srt" has none). A qualifier that is also a language code
    (config.LANGUAGE_CODE_QUALIFIERS) is the language unless a language comes
    before it: "Movie.hi.srt" is Hindi, "Movie.en.hi.srt" is English hearing
    impaired.
    """
    tokens = f.stem.lower().split(".")
    for i in range(len(tokens) - 1, 0, -1):
        token = tokens[i]
        if _is_tag(token) and (
            token not in LANGUAGE_CODE_QUALIFIERS
            or _qualifier_follows_language(tokens[1:i])
        ):
            continue
        return _language_of(token)
    return None


def is_wanted_file(f: Path) -> bool:
    """Keep videos, non-subtitle files, and subtitles in a wanted language.

    A subtitle in any other language is dropped, forced ones included (a forced
    French track translates for a French viewer). A subtitle whose name carries
    no language code is kept.
    """
    if f.suffix.lower() not in SUBTITLE_EXTS:
        return True
    lang = subtitle_language(f)
    return lang is None or lang in ENGLISH_CODES


def _season_num(p: Path) -> int:
    m = re.search(r"\d+", p.name)
    return int(m.group()) if m else 0


def _is_video(f: Path) -> bool:
    return f.suffix.lower() in VIDEO_EXTS


def _ep_title_from_files(files: list[Path]) -> str:
    for f in files:
        if _is_video(f):
            m = _EP_TITLE_FROM_FILENAME.search(f.name)
            if m:
                return m.group(1).strip()
    return ""


def _synced_folder_index() -> dict[str, int]:
    """{folder_name: video_file_count} across SYNC_ROOT/* subfolders.

    One pass over the (small) synced tree , typical sync has ~20 titles, so
    this is well under a second. Replaces a per-title rglob from grid scan,
    which would have been O(titles x per-title-walk).
    """
    out: dict[str, int] = {}
    for sub_path in iter_sync_subs():
        with os.scandir(sub_path) as it:
            for entry in it:
                if not entry.is_dir(follow_symlinks=False) or entry.name.startswith(
                    "."
                ):
                    continue
                # rglob over a single synced title , small (one show's worth).
                n = 0
                for f in Path(entry.path).rglob("*"):
                    if f.is_file() and _is_video(f):
                        n += 1
                out[entry.name] = n
    return out


# ── Grid scan ──────────────────────────────────────────────────────────────


def scan_titles() -> list[Title]:
    """Return one Title per library folder, from the cached library listing.

    DO NOT os.scandir the library roots here. This runs on every grid rebuild,
    and listing a merged shfs root on a timer is what stalled the share
    (Synclet#66). synclet.library owns the listing and keeps it current per
    title. Episode counts come from WatchState/Plex; sync state comes from a
    single pass over SYNC_ROOT, a much smaller tree.
    """
    from synclet import library  # local to avoid cycle
    from synclet.watchstate import all_show_aggregates  # local to avoid cycle

    # {show_title_lower: total_episode_count}, merged WatchState + Plex so YouTube
    # (and any other section WatchState does not index) still gets a sensible
    # denominator for the grid's watched% badge.
    show_ep_count = {k: agg.total for k, agg in all_show_aggregates().items()}
    synced_index = _synced_folder_index()

    out: list[Title] = []
    for lib, info in LIBRARIES.items():
        names = library.folders(lib)
        if names is None:
            continue
        for folder in names:
            kind = info["kind"]
            ws_key = watchstate_key(folder)
            ep_count = (
                show_ep_count.get(ws_key, 0) if kind in ("show", "youtube") else 0
            )
            synced_files = synced_index.get(folder, 0)

            out.append(
                Title(
                    id=f"{lib}/{folder}",
                    lib=lib,
                    folder=folder,
                    name=clean_name(folder),
                    kind=kind,
                    year=parse_year(folder),
                    ep_count=ep_count,
                    synced_files=synced_files,
                    has_synced=synced_files > 0,
                )
            )
    return out


# ── Detail scan ────────────────────────────────────────────────────────────


def _files_for_episode(season_dir: Path, s_num: int, e_num: int) -> list[Path]:
    pats = [
        re.compile(rf"[Ss]{s_num:02d}[Ee]{e_num:02d}(?!\d)", re.IGNORECASE),
        re.compile(rf"[Ss]{s_num}[Ee]{e_num:04d}(?!\d)", re.IGNORECASE),
    ]
    return sorted(
        f
        for f in season_dir.iterdir()
        if not f.is_dir() and any(p.search(f.name) for p in pats)
    )


def _episodes_in_season(season_dir: Path) -> dict[int, list[Path]]:
    eps: dict[int, list[Path]] = {}
    for f in season_dir.iterdir():
        if f.is_dir():
            continue
        m = EPISODE_PAT.search(f.name)
        if m:
            eps.setdefault(int(m.group(2)), []).append(f)
    return {k: sorted(v) for k, v in sorted(eps.items())}


def _synced_dir(lib: str, folder: str) -> Path:
    """Path to a title's synced copy: SYNC_ROOT / <sync-sub> / <folder>.

    The synced tree mirrors the source folder name under the library's sync-sub
    (see config.LIBRARIES). Single source of truth for the synced title location
    so the episode-index and movie-presence reads stay in agreement.
    """
    return SYNC_ROOT / LIBRARIES[lib]["sync_sub"] / folder


def _synced_episode_index(lib: str, folder: str) -> set[tuple[int, int]]:
    """{(season, episode)} for which a video file exists in the synced copy.

    DO NOT match synced episodes by exact filename. The source library gets
    re-encoded in place (e.g. x264 -> h265), which renames the file while it
    stays the same episode; an exact synced-path `.exists()` check then reads
    every episode as un-synced even though the synced folder holds it under the
    old name. Match on the SxxExx tag instead. We mirror the source-side walk
    exactly , dir-derived season number + filename-derived episode number, and
    require at least one video , so the index lines up with the eps_map keys
    the detail builder compares against.
    """
    synced_dir = _synced_dir(lib, folder)
    out: set[tuple[int, int]] = set()
    if not synced_dir.is_dir():
        return out
    for sd in synced_dir.iterdir():
        if not sd.is_dir() or not re.search(r"\d", sd.name):
            continue
        s_num = _season_num(sd)
        for e_num, files in _episodes_in_season(sd).items():
            if any(_is_video(f) for f in files):
                out.add((s_num, e_num))
    return out


def _synced_has_video(lib: str, folder: str) -> bool:
    """True if the synced copy of this movie holds at least one video file.

    Movie identity is the title folder (name + {tmdb/imdb-id}); the synced copy
    mirrors it under SYNC_ROOT. Same rationale as _synced_episode_index: a
    source-side re-encode renames the video (and its stem-matched sidecars), so
    an exact filename check reads the movie as un-synced even though it is
    present. A movie's wanted files are synced/unsynced together, so this
    folder-level signal is the right granularity.
    """
    synced_dir = _synced_dir(lib, folder)
    if not synced_dir.is_dir():
        return False
    return any(_is_video(f) for f in synced_dir.rglob("*") if f.is_file())


def scan_title_detail(lib: str, folder: str) -> TitleDetail | None:
    if lib not in LIBRARIES:
        return None
    path = MEDIA_ROOT / lib / folder
    if not path.exists() or not path.is_dir():
        return None

    info = LIBRARIES[lib]
    detail = TitleDetail(
        id=f"{lib}/{folder}",
        lib=lib,
        folder=folder,
        name=clean_name(folder),
        kind=info["kind"],
        year=parse_year(folder),
    )

    if info["kind"] == "movie":
        files = sorted(
            f for f in path.iterdir() if not f.is_dir() and is_wanted_file(f)
        )
        # Sync state is a folder-level fact: a movie's wanted files are copied
        # and removed together, so all rows share the title's synced state.
        movie_synced = _synced_has_video(lib, folder)
        total = 0
        synced = 0
        for f in files:
            size = f.stat().st_size
            total += size
            is_video = _is_video(f)
            if movie_synced and is_video:
                synced += size
            detail.files.append(
                {
                    "path": str(f),
                    "name": f.name,
                    "size_bytes": size,
                    "is_video": is_video,
                    "is_synced": movie_synced,
                }
            )
        detail.total_bytes = total
        detail.synced_bytes = synced
        return detail

    # show / youtube: walk seasons
    season_dirs = sorted(
        (d for d in path.iterdir() if d.is_dir() and re.search(r"\d", d.name)),
        key=_season_num,
    )

    # SxxExx presence in the synced copy, resilient to source-side re-encodes
    # that rename the file (see _synced_episode_index).
    synced_eps = _synced_episode_index(lib, folder)

    grand_total = 0
    grand_synced = 0

    for sd in season_dirs:
        s_num = _season_num(sd)
        eps_map = _episodes_in_season(sd)
        eps: list[Episode] = []
        season_bytes = 0
        season_synced = 0

        for e_num, files in eps_map.items():
            wanted = [f for f in files if is_wanted_file(f)]
            videos = [f for f in wanted if _is_video(f)]
            size = sum(f.stat().st_size for f in wanted)
            is_synced = bool(videos) and (s_num, e_num) in synced_eps
            ep = Episode(
                season=s_num,
                episode=e_num,
                title=_ep_title_from_files(wanted),
                size_bytes=size,
                files=[str(f) for f in wanted],
                has_video=bool(videos),
                is_synced=is_synced,
            )
            eps.append(ep)
            season_bytes += size
            if is_synced:
                season_synced += size

        detail.seasons.append(
            Season(
                season=s_num,
                episodes=eps,
                total_bytes=season_bytes,
                synced_episodes=sum(1 for e in eps if e.is_synced),
                watched_episodes=0,  # filled in by watchstate later
            )
        )
        grand_total += season_bytes
        grand_synced += season_synced

    detail.total_bytes = grand_total
    detail.synced_bytes = grand_synced
    return detail


def title_detail_to_dict(d: TitleDetail) -> dict:
    return {
        "id": d.id,
        "lib": d.lib,
        "folder": d.folder,
        "name": d.name,
        "kind": d.kind,
        "year": d.year,
        "total_bytes": d.total_bytes,
        "synced_bytes": d.synced_bytes,
        "files": d.files,
        "seasons": [
            {
                "season": s.season,
                "total_bytes": s.total_bytes,
                "synced_episodes": s.synced_episodes,
                "watched_episodes": s.watched_episodes,
                "episodes": [
                    {
                        "season": e.season,
                        "episode": e.episode,
                        "title": e.title,
                        "size_bytes": e.size_bytes,
                        "files": e.files,
                        "has_video": e.has_video,
                        "is_synced": e.is_synced,
                        "watch_state": e.watch_state,
                        "watch_pct": e.watch_pct,
                    }
                    for e in s.episodes
                ],
            }
            for s in d.seasons
        ],
    }
