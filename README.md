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

## Permanent media deletion

The production compose mounts the source library writable so Synclet can delete selected movies or episodes. Each deletion shows a file preview, requires the exact title, and requires a separate deletion password. Set `SYNCLET_DELETE_KEY` to a long random value in the gitignored `.env` beside the compose file. Without it, the delete endpoint is disabled. The development compose keeps source media read-only.
