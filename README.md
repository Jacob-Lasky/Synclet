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
