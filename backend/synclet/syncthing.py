"""Async Syncthing REST client. READ-ONLY by design.

DO NOT add PUT, POST, or DELETE methods here. Synclet observes Syncthing;
it never mutates it. Read-only is the load-bearing invariant from issue
#25; widening this surface breaks the contract and the security posture
(no Synclet code path should be able to alter Syncthing folder configs,
device pairings, or API key).

Auth: Syncthing requires an X-API-Key header. The key and base URL come
from synclet.config (SYNCTHING_URL, SYNCTHING_API_KEY). Both are env-only
with no committed defaults; absent config degrades the UI to a "not
configured" panel rather than crashing.

Cost: /rest/db/status is documented upstream as expensive ("increasing
CPU and RAM usage on the device, use sparingly"). The frontend polls
/api/syncthing/overview at 8s while visible, paused otherwise. We do not
cache server-side because the frontend's interval is the only consumer
today; adding caching now would be premature given the small per-poll
cost (~3 base calls + N device-completion calls for a single-folder
homelab setup).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from synclet import config


def is_configured() -> bool:
    """True iff both SYNCTHING_URL and SYNCTHING_API_KEY are set."""
    return bool(config.SYNCTHING_URL) and bool(config.SYNCTHING_API_KEY)


async def _get(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, str] | None = None,
) -> Any:
    """Single GET against Syncthing's REST API.

    No retries; the caller poll-loops at 8s and any transient failure just
    surfaces as an empty overview for one cycle.
    """
    headers = {"X-API-Key": config.SYNCTHING_API_KEY or ""}
    r = await client.get(path, params=params, headers=headers, timeout=10.0)
    r.raise_for_status()
    return r.json()


async def overview() -> list[dict]:
    """Joined Syncthing view: per folder with per-device completion.

    Returns an empty list when env unconfigured, Syncthing unreachable, or
    the upstream response is malformed. Errors are swallowed because the
    Syncthing surface is supplementary; throwing would 500 the Synclet API
    even though the user's other workflows (sync, maintenance) are unaffected
    by Syncthing being down.
    """
    if not is_configured():
        return []

    # is_configured guarantees the URL is non-None; the assert documents
    # that for pyright and trips loudly if the guard ever drifts.
    assert config.SYNCTHING_URL is not None  # noqa: S101

    try:
        async with httpx.AsyncClient(base_url=config.SYNCTHING_URL) as client:
            # Three base calls fire concurrently. Folder + device configs are
            # cheap; system/connections is needed for the online indicator.
            folders_raw, devices_raw, conns_raw = await asyncio.gather(
                _get(client, "/rest/config/folders"),
                _get(client, "/rest/config/devices"),
                _get(client, "/rest/system/connections"),
            )

            # Friendly-name lookup for devices. Syncthing's device list has
            # entries shaped {deviceID, name, ...}; name is user-supplied
            # and may be empty, in which case we fall back to the device ID
            # short prefix (matches the Syncthing GUI's behavior).
            device_name = {
                d["deviceID"]: (d.get("name") or d["deviceID"][:7]) for d in devices_raw
            }

            # conns_raw shape: {"connections": {deviceID: {connected, ...}},
            # "this": {deviceID: ...}, ...}. The "this" entry is the local
            # Syncthing instance and is not a sync partner; exclude it from
            # the device chips for each folder.
            conns = conns_raw.get("connections", {})
            this = conns_raw.get("this", {})
            own_device_id = this.get("deviceID") if isinstance(this, dict) else None

            result: list[dict] = []
            for folder_cfg in folders_raw:
                folder_id = folder_cfg["id"]
                folder_label = folder_cfg.get("label") or folder_id

                shared_device_ids = [
                    d["deviceID"]
                    for d in folder_cfg.get("devices", [])
                    if d["deviceID"] != own_device_id
                ]

                # Per-folder status and per-(folder, device) completion fire
                # concurrently. For Jake's single-folder topology with ~3
                # devices, that is 4 parallel calls per overview poll.
                status_task = _get(
                    client,
                    "/rest/db/status",
                    params={"folder": folder_id},
                )
                completion_tasks = [
                    _get(
                        client,
                        "/rest/db/completion",
                        params={"folder": folder_id, "device": dev_id},
                    )
                    for dev_id in shared_device_ids
                ]
                status, *completions = await asyncio.gather(
                    status_task,
                    *completion_tasks,
                )

                global_bytes = status.get("globalBytes", 0)
                in_sync_bytes = status.get("inSyncBytes", 0)
                need_bytes = status.get("needBytes", 0)
                state = status.get("state", "unknown")
                # Manual percent: upstream /rest/db/status intentionally does
                # not return one because the meaningful denominator depends
                # on the caller (bytes vs files). Bytes match what the UI
                # progress bar visualizes.
                percent = (
                    round(100.0 * in_sync_bytes / global_bytes, 1)
                    if global_bytes
                    else 100.0
                )

                devices_view = []
                for dev_id, completion in zip(
                    shared_device_ids,
                    completions,
                    strict=True,
                ):
                    conn = conns.get(dev_id, {})
                    devices_view.append(
                        {
                            "device_id": dev_id,
                            "name": device_name.get(dev_id, dev_id[:7]),
                            "completion": round(
                                completion.get("completion", 0.0),
                                1,
                            ),
                            "need_bytes": completion.get("needBytes", 0),
                            "connected": bool(conn.get("connected", False)),
                            "paused": bool(conn.get("paused", False)),
                        },
                    )

                result.append(
                    {
                        "folder_id": folder_id,
                        "label": folder_label,
                        "percent": percent,
                        "state": state,
                        "need_bytes": need_bytes,
                        "in_sync_bytes": in_sync_bytes,
                        "global_bytes": global_bytes,
                        "devices": devices_view,
                    },
                )
            return result
    except httpx.HTTPError, KeyError, TypeError, ValueError:
        # Network failure, malformed Syncthing response, or unreachable
        # service: surface as empty rather than 500. The Syncthing panel
        # treats empty as "nothing to show right now"; other Synclet
        # functions (sync, maintenance, watchstate) are unaffected.
        return []
