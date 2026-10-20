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

- **Sonarr/Radarr webhooks.** Add a Webhook connection in each arr (Settings, Connect) pointing at `http://<synclet-host>:1313/api/arr/webhook`, method POST, with On Import, On Series/Movie Add, On Series/Movie Delete, On File Delete and On Rename enabled. Each event makes Synclet check that one folder.
- **Synclet's own deletes** update their title directly.
- **A full re-list** runs only as a backstop, one library at a time, every `SYNCLET_LIBRARY_RELIST` seconds (default 6 hours), or when you press Rescan in the top bar. The button waits for the re-list to finish before it says "Library rescanned". `GET /api/library` shows each library's title count and listing time.

Sources with no arr (YouTube) get new channels at the next backstop or Rescan. New episodes inside a followed YouTube channel are picked up within one to two `SYNCLET_TITLE_RESCAN` intervals (default 1 hour): each title's limit is stretched by a stable amount so the rescans spread out instead of every followed channel being walked in the same pass.

After deleting source media, Synclet asks the owning arr to rescan just that series or movie (`RescanSeries` / `RescanMovie`), so the arr does not need a periodic full-disk rescan to notice. Configure one URL and API key per library in the gitignored `.env`: `SYNCLET_SONARR_URL` / `SYNCLET_SONARR_API_KEY` (TV), `SYNCLET_SONARR_4K_*` (TV 4K), `SYNCLET_RADARR_*` (Movies) and `SYNCLET_RADARR_4K_*` (Movies 4K). A library with no URL and key skips the rescan. `POST /api/arr/rescan` with `{"lib", "folder"}` runs the same rescan on demand.

## Permanent media deletion

The production compose mounts the source library writable so Synclet can delete selected movies or episodes. From a show's drawer you can delete the episodes you have selected, or use "Delete watched (N)" to sweep every watched episode across all seasons at once. Each deletion shows a file preview, requires the exact title, and requires a separate deletion password. Set `SYNCLET_DELETE_KEY` to a long random value in the gitignored `.env` beside the compose file. Without it, the delete endpoint is disabled. The development compose keeps source media read-only.

Deleting an episode also removes its generated sidecars: subtitles, `.nfo`, `.trickplay` thumbnail directories, and its `.chapters` XML. When every video in a season is selected, season-level `season.nfo` and recognized orphan sidecars are removed too. Unindexed videos and unknown directories or file types prevent that season-level cleanup. Unrelated files are left in place, so a season folder is removed only when it is empty. Episode rows with no video file, such as a leftover subtitle, are skipped by both delete actions.
