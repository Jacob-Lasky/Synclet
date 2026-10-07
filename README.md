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

Following is stored as intent: reading it never stats the source library, so a slow or unmounted source cannot hold up title-detail requests. A background check notices when a source folder has been moved or deleted outside Synclet and hides that waiting row from the Synced tab, but the saved follow is kept — the row comes back on its own once the source folder reappears. Deleting a title's source media through Synclet does drop its follow entry. An open Synced tab re-polls every 60 seconds so these background results land without a manual refresh.

## Permanent media deletion

The production compose mounts the source library writable so Synclet can delete selected movies or episodes. From a show's drawer you can delete the episodes you have selected, or use "Delete watched (N)" to sweep every watched episode across all seasons at once. Each deletion shows a file preview, requires the exact title, and requires a separate deletion password. Set `SYNCLET_DELETE_KEY` to a long random value in the gitignored `.env` beside the compose file. Without it, the delete endpoint is disabled. The development compose keeps source media read-only.

Deleting an episode also removes its generated sidecars: subtitles, `.nfo`, `.trickplay` thumbnail directories, and its `.chapters` XML. When every video in a season is selected, season-level `season.nfo` and recognized orphan sidecars are removed too. Unindexed videos and unknown directories or file types prevent that season-level cleanup. Unrelated files are left in place, so a season folder is removed only when it is empty. Episode rows with no video file, such as a leftover subtitle, are skipped by both delete actions.
