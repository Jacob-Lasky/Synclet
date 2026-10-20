"""Sonarr/Radarr integration: per-title rescans out, Connect webhooks in.

Out: after Synclet deletes source media, the owning arr is told to rescan ONLY
that series or movie (RescanSeries / RescanMovie). Without it, Sonarr learns of
a "Delete watched" only at its next periodic full-disk rescan, which takes hours
on a cold shfs cache. Telling it per title is what lets the arrs drop those
periodic full rescans (homelab#103 proposal A).

When the delete was a "Delete watched" (or a watched movie), the deleted
episodes or the movie are unmonitored FIRST, so the rescan that marks them
missing cannot lead to them being downloaded again. DO NOT replace this with
the arrs' global "Unmonitor deleted episodes/movies" setting: that also
unmonitors files the Tdarr replacement pipeline deletes on purpose so the arr
re-grabs a better copy (Jake, 2026-10-20). Only Synclet knows the delete meant
"done with it", so only Synclet's delete unmonitors.

In: arr Connect webhooks name the series or movie folder an event touched, so
synclet.library can re-check that one title instead of listing the library.

Both directions identify a title by its folder: arr paths are
`<root>/<library>/<folder>` with the same library and folder names Synclet uses
under MEDIA_ROOT, so the match is on the last two path components and never on
a display title.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from common.log_utils import get_logger
from synclet import config
from synclet.scan import folder_external_ids

logger = get_logger(__name__)

# Which external ID each arr can filter its library by, cheapest lookup first.
_ID_FILTER = {"sonarr": ("tvdb", "tvdbId"), "radarr": ("tmdb", "tmdbId")}
_COLLECTION = {"sonarr": "series", "radarr": "movie"}
_RESCAN = {"sonarr": ("RescanSeries", "seriesId"), "radarr": ("RescanMovie", "movieId")}


def _request(
    base: str,
    key: str,
    path: str,
    *,
    params: dict | None = None,
    body: dict | None = None,
    method: str | None = None,
) -> object:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    request = urllib.request.Request(  # noqa: S310 (operator-configured arr URL)
        f"{base}{path}{query}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
        method=method or ("POST" if body is not None else "GET"),
    )
    with urllib.request.urlopen(request, timeout=config.ARR_TIMEOUT_S) as r:  # noqa: S310
        return json.loads(r.read() or b"null")


def _item_ref(item: dict) -> tuple[str, str] | None:
    """(library, folder) from an arr item's path, the last two components."""
    path = item.get("path") or item.get("folderPath")
    if not isinstance(path, str) or not path:
        return None
    p = PurePosixPath(path)
    return p.parent.name, p.name


def _find_item_id(app: str, base: str, key: str, lib: str, folder: str) -> int | None:
    """The arr's id for the series/movie whose path ends in `lib/folder`.

    Tries the external-ID filter first (one small response), then the full
    collection. Either way library and folder must both match, so a 4K and a
    1080p copy of one title never resolve to each other and a mis-tagged
    folder is never rescanned under another title. More than one match is
    ambiguous and returns None.
    """
    collection = f"/api/v3/{_COLLECTION[app]}"
    scheme, param = _ID_FILTER[app]
    want = (lib, folder)
    candidates: list[dict] = []
    ext_id = folder_external_ids(folder).get(scheme)
    if ext_id:
        found = _request(base, key, collection, params={param: ext_id})
        candidates = found if isinstance(found, list) else []
    if not any(_item_ref(c) == want for c in candidates):
        found = _request(base, key, collection)
        candidates = found if isinstance(found, list) else []
    ids = {
        item["id"]
        for item in candidates
        if _item_ref(item) == want and isinstance(item.get("id"), int)
    }
    # Two arr items ending in the same library/folder (two root folders with the
    # same layout) cannot be told apart from Synclet's side. Rescan neither
    # rather than guess.
    return ids.pop() if len(ids) == 1 else None


def _owning_item(
    lib: str, folder: str
) -> tuple[tuple[str, str, str, int] | None, dict | None]:
    """((app, base, key, item_id), None) for the arr item, or (None, why not)."""
    instance = config.arr_instance(lib)
    if instance is None:
        return None, {
            "requested": False,
            "reason": "no arr configured for this library",
        }
    app, base, key = instance
    item_id = _find_item_id(app, base, key, lib, folder)
    if item_id is None:
        return None, {
            "requested": False,
            "app": app,
            "reason": "title not found in arr",
        }
    return (app, base, key, item_id), None


def _rescan(app: str, base: str, key: str, item_id: int) -> dict:
    name, id_field = _RESCAN[app]
    command = _request(
        base, key, "/api/v3/command", body={"name": name, id_field: item_id}
    )
    command_id = command.get("id") if isinstance(command, dict) else None
    logger.info(
        "arr rescan requested: %s %s %s=%s (command %s)",
        app,
        name,
        id_field,
        item_id,
        command_id,
    )
    return {
        "requested": True,
        "app": app,
        "command": name,
        "item_id": item_id,
        "command_id": command_id,
    }


def _put_unmonitored(base: str, key: str, episode_ids: list[int]) -> None:
    _request(
        base,
        key,
        "/api/v3/episode/monitor",
        body={"episodeIds": episode_ids, "monitored": False},
        method="PUT",
    )


def _unmonitor_movie(app: str, base: str, key: str, item_id: int) -> dict:
    _request(
        base,
        key,
        "/api/v3/movie/editor",
        body={"movieIds": [item_id], "monitored": False},
        method="PUT",
    )
    logger.info("arr unmonitored: radarr movieId=%s", item_id)
    return {"requested": True, "app": app, "item_id": item_id}


def _episodes(base: str, key: str, item_id: int) -> list[dict]:
    listed = _request(base, key, "/api/v3/episode", params={"seriesId": item_id})
    return [
        ep
        for ep in (listed if isinstance(listed, list) else [])
        if isinstance(ep.get("id"), int)
    ]


def _report(app: str, item_id: int, episode_ids: list[int]) -> dict:
    if not episode_ids:
        return {"requested": False, "app": app, "reason": "episodes not found in arr"}
    logger.info(
        "arr unmonitored: sonarr seriesId=%s episodeIds=%s", item_id, episode_ids
    )
    return {
        "requested": True,
        "app": app,
        "item_id": item_id,
        "episode_ids": episode_ids,
    }


def _unmonitor_deleted(
    app: str,
    base: str,
    key: str,
    item_id: int,
    deleted: DeletedMedia,
) -> dict:
    """Unmonitor the episodes whose media a delete removed (or the movie).

    DO NOT match by the numbers in file names first: the arr links each
    episode (episodeFileId) or movie (movieFile) to a file, and that link is
    the truth. A multi-episode file (S01E01E02) is linked to both episodes, and
    a file imported under different numbering is linked to the episode Sonarr
    thinks it is. A link counts as deleted when Synclet deleted that file, or
    when the linked file is no longer on disk at all (the arr's path went stale
    after a rename nobody reported) and the deleted file's name names that
    episode. Names alone decide only for a deleted file the arr has no record
    of, and then only for episodes with no live linked file, so an episode whose
    media is still on disk is never touched. `deleted.keep` holds episodes that
    still have a copy among the files this delete planned (a failed unlink).
    """
    if app == "radarr":
        movie = _request(base, key, f"/api/v3/movie/{item_id}")
        linked = movie.get("movieFile") if isinstance(movie, dict) else None
        rel = linked.get("relativePath") if isinstance(linked, dict) else None
        if rel and rel not in deleted.files and deleted.on_disk(rel):
            return {
                "requested": False,
                "app": app,
                "reason": "the movie file Radarr knows is still on disk",
            }
        return _unmonitor_movie(app, base, key, item_id)
    episodes = _episodes(base, key, item_id)
    listed = _request(base, key, "/api/v3/episodefile", params={"seriesId": item_id})
    file_paths = {
        f["id"]: f.get("relativePath")
        for f in (listed if isinstance(listed, list) else [])
        if isinstance(f.get("id"), int)
    }
    known = set(file_paths.values())
    named = {n for nums in deleted.files.values() for n in nums}
    unknown_named = {
        n for rel, nums in deleted.files.items() if rel not in known for n in nums
    }

    def lost_media(ep: dict) -> bool:
        number = (ep.get("seasonNumber"), ep.get("episodeNumber"))
        if number in deleted.keep:
            return False
        rel = file_paths.get(ep.get("episodeFileId") or 0)
        if rel is None:
            return number in unknown_named
        if rel in deleted.files:
            return True
        return number in named and not deleted.on_disk(rel)

    ids = sorted(ep["id"] for ep in episodes if lost_media(ep))
    if ids:
        _put_unmonitored(base, key, ids)
    return _report(app, item_id, ids)


def _unmonitor_numbers(
    app: str, base: str, key: str, item_id: int, numbers: set[tuple[int, int]]
) -> dict:
    """Unmonitor episodes by (season, episode), or the movie. On-demand path."""
    if app == "radarr":
        return _unmonitor_movie(app, base, key, item_id)
    ids = sorted(
        ep["id"]
        for ep in _episodes(base, key, item_id)
        if (ep.get("seasonNumber"), ep.get("episodeNumber")) in numbers
    )
    if ids:
        _put_unmonitored(base, key, ids)
    return _report(app, item_id, ids)


def _failed(app: str | None, lib: str, folder: str, step: str, exc: Exception) -> dict:
    logger.warning("arr %s for %s/%s failed: %r", step, lib, folder, exc)
    out: dict = {"requested": False, "reason": str(exc)}
    if app:
        out["app"] = app
    return out


def rescan_title(lib: str, folder: str) -> dict:
    """Tell the arr that owns `lib` to rescan the one series or movie in `folder`.

    Never raises: a source delete must not fail because an arr is down. The
    result says what happened, for the delete response and the logs.
    """
    try:
        item, why_not = _owning_item(lib, folder)
        if item is None:
            return why_not or {}
        return _rescan(*item)
    except (OSError, ValueError) as exc:
        instance = config.arr_instance(lib)
        return _failed(instance[0] if instance else None, lib, folder, "rescan", exc)


def unmonitor_title(lib: str, folder: str, episodes: set[tuple[int, int]]) -> dict:
    """Unmonitor `episodes` of the series in `folder` (or the movie). Never raises."""
    try:
        item, why_not = _owning_item(lib, folder)
        if item is None:
            return why_not or {}
        return _unmonitor_numbers(*item, episodes)
    except (OSError, ValueError) as exc:
        instance = config.arr_instance(lib)
        return _failed(instance[0] if instance else None, lib, folder, "unmonitor", exc)


@dataclass(frozen=True)
class DeletedMedia:
    """What a source delete removed, as the arr needs to know it.

    files:   each deleted video, relative to the title folder (the arr's
             relativePath), with the episode numbers its name carries.
    keep:    episode numbers that still have a planned video on disk.
    on_disk: whether a path relative to the title folder still exists.
    """

    files: dict[str, set[tuple[int, int]]]
    keep: set[tuple[int, int]]
    on_disk: Callable[[str], bool]


def after_source_delete(
    lib: str, folder: str, *, unmonitor: bool, deleted: DeletedMedia
) -> dict:
    """What a source delete tells the owning arr: unmonitor (if asked), then rescan.

    See DeletedMedia and _unmonitor_deleted for what is unmonitored. One arr lookup for
    both steps. Unmonitor runs first so the rescan that marks the files missing
    never sees them as wanted. If unmonitoring FAILS, the rescan is skipped:
    an arr that still believes the files exist will not go looking for them,
    while one that sees them missing and still monitored may download them
    again, which is exactly what the unmonitor was for. Neither step raises:
    the files are already gone.
    """
    instance = config.arr_instance(lib)
    app = instance[0] if instance else None
    try:
        item, why_not = _owning_item(lib, folder)
    except (OSError, ValueError) as exc:
        failed = _failed(app, lib, folder, "lookup", exc)
        return {"unmonitor": failed if unmonitor else None, "rescan": failed}
    if item is None:
        return {"unmonitor": why_not if unmonitor else None, "rescan": why_not}
    unmonitored = None
    if unmonitor:
        try:
            unmonitored = _unmonitor_deleted(*item, deleted)
        except (OSError, ValueError) as exc:
            failed = _failed(app, lib, folder, "unmonitor", exc)
            skipped = {
                "requested": False,
                "app": app,
                "reason": "skipped because unmonitoring failed",
            }
            return {"unmonitor": failed, "rescan": skipped}
    try:
        rescanned = _rescan(*item)
    except (OSError, ValueError) as exc:
        rescanned = _failed(app, lib, folder, "rescan", exc)
    return {"unmonitor": unmonitored, "rescan": rescanned}


def title_from_webhook(payload: dict) -> tuple[str, str] | None:
    """(lib, folder) named by a Sonarr/Radarr Connect payload, or None.

    Sonarr sends `series.path`, Radarr `movie.folderPath`. The parent directory
    name must be a configured library and the folder a valid title name, so a
    payload can only ever point note_title at a direct child of a library.
    """
    from synclet.library import valid_folder  # local to avoid cycle

    for section, field in (
        ("series", "path"),
        ("movie", "folderPath"),
        ("movie", "path"),
    ):
        item = payload.get(section)
        path = item.get(field) if isinstance(item, dict) else None
        if isinstance(path, str) and path:
            p = PurePosixPath(path)
            lib, folder = p.parent.name, p.name
            if valid_folder(lib, folder):
                return lib, folder
            return None
    return None
