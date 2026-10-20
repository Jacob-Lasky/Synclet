"""Sonarr/Radarr integration: per-title rescans out, Connect webhooks in.

Out: after Synclet deletes source media, the owning arr is told to rescan ONLY
that series or movie (RescanSeries / RescanMovie). Without it, Sonarr learns of
a "Delete watched" only at its next periodic full-disk rescan, which takes hours
on a cold shfs cache. Telling it per title is what lets the arrs drop those
periodic full rescans (homelab#103 proposal A).

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
) -> object:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    request = urllib.request.Request(  # noqa: S310 (operator-configured arr URL)
        f"{base}{path}{query}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
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


def rescan_title(lib: str, folder: str) -> dict:
    """Tell the arr that owns `lib` to rescan the one series or movie in `folder`.

    Never raises: a source delete must not fail because an arr is down. The
    result says what happened, for the delete response and the logs.
    """
    instance = config.arr_instance(lib)
    if instance is None:
        return {"requested": False, "reason": "no arr configured for this library"}
    app, base, key = instance
    try:
        item_id = _find_item_id(app, base, key, lib, folder)
        if item_id is None:
            return {"requested": False, "app": app, "reason": "title not found in arr"}
        name, id_field = _RESCAN[app]
        command = _request(
            base, key, "/api/v3/command", body={"name": name, id_field: item_id}
        )
    except (OSError, ValueError) as exc:
        logger.warning("arr rescan for %s/%s failed: %r", lib, folder, exc)
        return {"requested": False, "app": app, "reason": str(exc)}
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
