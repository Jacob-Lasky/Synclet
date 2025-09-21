# Project Template
A template repo for Python + Vue.js + Docker

## Access
- Frontend: http://localhost:1313
- Backend: http://localhost:1314

## Tools
### Backend
- Python 3.11
- [uv](https://docs.astral.sh/uv/)
- [litestar](https://litestar.dev/)

### Frontend
- Node.js 22
- [pnpm](https://pnpm.io/)
- [vue](https://vuejs.org/)
- [vite](https://vite.dev/)

### Database
- [Meilisearch](https://www.meilisearch.com/)
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
