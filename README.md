# Synclet

Easy syncing of media, where Plex and Jellyfin fall short. That's Synclet's mission.

Synclet sits on top of your Plex library and a Syncthing-shared folder. You pick what you want available offline (a season, an episode, a movie). Synclet copies the files into the Syncthing-watched folder. Syncthing propagates them to your other devices. Synclet then tracks what's watched, what's hanging, and what to clean up.

## Access
- Frontend: http://localhost:1313
- Backend: http://localhost:1314

## Tools
### Backend
- Python 3.14
- [uv](https://docs.astral.sh/uv/)
- [litestar](https://litestar.dev/)

### Frontend
- Node.js 24
- [pnpm](https://pnpm.io/)
- [vue](https://vuejs.org/) 3
- [vite](https://vite.dev/)

### Storage
- SQLite

## Setup
Install the frontend dependencies by running:
```bash
pnpm install
```

Approve pnpm builds by running:
```bash
pnpm approve-builds
```

Now, you can run the app by running:
```bash
docker compose -f docker-compose.dev.yml down --rmi local && docker compose -f docker-compose.dev.yml up --build
```

## Following shows

Shows and YouTube channels can be followed so Synclet keeps surfacing their new unwatched episodes. Syncing any episode follows the title automatically; you can also toggle it from the title drawer or the Synced tab. A followed title stays listed after its last offline episode is removed — shown as "Waiting for new episodes" — until you choose "Stop following". Follow state lives in `followed.json` alongside the other persistent app data (`SYNCLET_FOLLOWED_FILE`), seeded on first run from the shows already synced offline; titles that match more than one source library are left for an explicit Follow.

Following is stored as intent: reading it never stats the source library, so a slow or unmounted source cannot hold up title-detail requests. When the library listing (below) says a source folder has been moved or deleted outside Synclet, that waiting row is hidden from the Synced tab, but the saved follow is kept, and the row comes back once the listing sees the folder again. Deleting a title's source media through Synclet does drop its follow entry. An open Synced tab re-polls every 60 seconds so these background results land without a manual refresh.

## Library listing and arr integration

Synclet does not list the library folders on a timer. On Unraid each library root is a directory merged across every array disk, and listing one on a cold cache takes tens of seconds while Plex, Jellyfin and SMB wait behind it. Instead the title list is kept in `.library-listing.json` (`SYNCLET_LIBRARY_LISTING_FILE`, persisted beside the other app data) and updated one title at a time:

- **Sonarr/Radarr webhooks.** Add a Webhook connection in each arr (Settings, Connect) pointing at `http://<synclet-host>:1313/api/arr/webhook`, method POST, with On Import, On Upgrade, On Import Complete (Sonarr only), On Series/Movie Add, On Series/Movie Delete, On File Delete and On Rename enabled. Each event makes Synclet check that one folder; the import, upgrade and rename events also refresh the title's synced copy (below).
- **Synclet's own deletes** update their title directly.
- **A full re-list** runs only as a backstop, one library at a time, every `SYNCLET_LIBRARY_RELIST` seconds (default 6 hours), or when you press Rescan in the top bar. The button waits for the re-list to finish before it says "Library rescanned". `GET /api/library` shows each library's title count and listing time.

Sources with no arr (YouTube) get new channels at the next backstop or Rescan. New episodes inside a followed YouTube channel are picked up within one to two `SYNCLET_TITLE_RESCAN` intervals (default 1 hour): each title's limit is stretched by a stable amount so the rescans spread out instead of every followed channel being walked in the same pass.

After deleting source media, Synclet asks the owning arr to rescan just that series or movie (`RescanSeries` / `RescanMovie`), so the arr does not need a periodic full-disk rescan to notice. Configure one URL and API key per library in the gitignored `.env`: `SYNCLET_SONARR_URL` / `SYNCLET_SONARR_API_KEY` (TV), `SYNCLET_SONARR_4K_*` (TV 4K), `SYNCLET_RADARR_*` (Movies) and `SYNCLET_RADARR_4K_*` (Movies 4K). A library with no URL and key skips the rescan. Point those URLs at the host's LAN IP (or the docker bridge gateway), not `host.docker.internal`: Sonarr and Radarr reject that Host header with `400 Bad Request`, so every rescan would fail. `POST /api/arr/rescan` with `{"lib", "folder"}` runs the same rescan on demand. When the delete meant "done with it" (see below), the arr is asked to unmonitor what was deleted before that rescan; `POST /api/arr/unmonitor` with `{"lib", "folder", "episodes"}` (`episodes` is a list of `[season, episode]` pairs, ignored for a movie) runs just the unmonitor half on demand. Because it changes what the arr will download, that endpoint requires the deletion password (`X-Synclet-Delete-Key`) like the delete itself; the plain rescan does not.

## Keeping synced copies current

A synced copy is a copy, not a link (Syncthing does not support hard links), so it keeps serving the file that was copied even after the library replaces it with a better release, a renamed file, or a Tdarr re-encode. Synclet brings the copy back in line instead. Per synced item (a movie, or one episode), it copies the library file when the synced one is missing, a different size, or older than the library file, then removes the synced files those copies supersede. A refresh never adds an episode you did not sync, and a title whose library video is gone is left alone: the offline copy is all that is left of it.

Three triggers run the same plan:

- **Arr webhooks**, right away for the title the event names (the connection above). On File Delete alone does not refresh; the import that follows an upgrade carries it.
- **A background check**, on each full cache pass, starting one refresh at a time so a multi-gigabyte copy does not contend with Plex for the array. A library file must have sat unchanged for `SYNCLET_REFRESH_SETTLE` seconds (default 600) before it is copied, so a file an import or Tdarr is still writing is not published half-written; webhooks skip that wait because the arr only sends them once its import has finished. A title whose refresh failed is left alone for `SYNCLET_REFRESH_RETRY` seconds (default 3600). `GET /api/synced/stale` lists what the last check found.
- **On demand**, `POST /api/synced/refresh` with `{"lib", "folder"}`: refreshes one title now and returns the plan, including why any synced item was left alone (no library video, a library file that now also holds episodes you did not sync, a title in both the HD and 4K library with no way to tell which one the synced copy came from, or a 4K copy whose 4K title is gone while the HD one remains, and the reverse).

Refreshes are jobs like sync and unsync (`op: "refresh"` in `/api/jobs`). Sync, unsync, refresh and source deletes on the same synced folder run one after another rather than at once, and each job re-checks the title once it is its turn. Copies land through a temporary file that is renamed into place, so an interrupted copy never leaves a partial file for Syncthing to send to your devices.

## Permanent media deletion

The production compose mounts the source library writable so Synclet can delete selected movies or episodes. From a show's drawer you can delete the episodes you have selected, or use "Delete watched (N)" to sweep every watched episode across all seasons at once. Each deletion shows a file preview, requires the exact title, and requires a separate deletion password. Set `SYNCLET_DELETE_KEY` to a long random value in the gitignored `.env` beside the compose file. Without it, the delete endpoint is disabled. The development compose keeps source media read-only.

"Delete watched (N)", and deleting a movie you have already watched, say you are done with that media, so Synclet asks the owning arr to stop monitoring exactly what it deleted before rescanning the title — otherwise the rescan would mark the files missing and still wanted, and the arr would download them again. The confirmation dialog says so before you confirm. A hand-picked episode delete, or deleting an unwatched movie, leaves monitoring alone, so deleting a bad copy still lets the arr re-grab it. Unmonitoring runs first, and if it fails the rescan is skipped: an arr that still believes the files exist will not go looking for them. The delete response reports both steps (`arr_unmonitor`, `arr_rescan`); a failure in either is reported, never raised, because the files are already gone.

Leave Sonarr's and Radarr's own "Unmonitor deleted episodes" / "Unmonitor deleted movies" settings off. Those also unmonitor files that are deleted on purpose so the arr fetches a better copy (for example by a Tdarr replacement pipeline). Only Synclet knows which delete meant "done with it", so only Synclet's delete unmonitors.

Deleting an episode also removes its generated sidecars: subtitles, `.nfo`, `.trickplay` thumbnail directories, and its `.chapters` XML. When every video in a season is selected, season-level `season.nfo` and recognized orphan sidecars are removed too. Unindexed videos and unknown directories or file types prevent that season-level cleanup. Unrelated files are left in place, so a season folder is removed only when it is empty. Episode rows with no video file, such as a leftover subtitle, are skipped by both delete actions.
