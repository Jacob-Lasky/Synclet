import type {
  HangingFile,
  Job,
  ResolveResult,
  StateBundle,
  SyncedEntry,
  TitleDetail,
  WatchedFiles,
  WatchlistEntry,
} from "./types"

const BASE = ""

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  })
  if (!res.ok) {
    let body = ""
    try { body = await res.text() } catch { /* ignore */ }
    throw new Error(`${res.status} ${path}: ${body}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  state: (refresh = false) => json<StateBundle>(`/api/state${refresh ? "?refresh=true" : ""}`),
  title: (lib: string, folder: string) =>
    json<TitleDetail>(`/api/title/${encodeURIComponent(lib)}/${encodeURI(folder)}`),
  thumbUrl: (lib: string, folder: string) =>
    `/api/plex/thumb/${encodeURIComponent(lib)}/${encodeURI(folder)}`,
  artUrl: (lib: string, folder: string) =>
    `/api/plex/art/${encodeURIComponent(lib)}/${encodeURI(folder)}`,

  sync: (body: SyncBody) =>
    json<SyncResponse>("/api/sync", { method: "POST", body: JSON.stringify(body) }),
  unsync: (body: SyncBody) =>
    json<SyncResponse>("/api/unsync", { method: "POST", body: JSON.stringify(body) }),

  job: (id: string) => json<Job>(`/api/jobs/${id}`),
  jobs: () => json<{ jobs: Job[] }>(`/api/jobs`),

  synced: () => json<{ items: SyncedEntry[] }>(`/api/synced`),
  watchlist: () => json<{ items: WatchlistEntry[] }>(`/api/watchlist`),
  maintWatched: () => json<{ items: WatchedFiles[] }>(`/api/maintenance/watched`),
  maintHanging: () => json<{ items: HangingFile[] }>(`/api/maintenance/hanging`),
  maintRemove: (paths: string[]) =>
    json<{ removed: number; bytes_freed: number }>(`/api/maintenance/remove`, {
      method: "POST",
      body: JSON.stringify({ paths }),
    }),

  resolve: (url: string) =>
    json<ResolveResult>("/api/resolve-link", {
      method: "POST",
      body: JSON.stringify({ url }),
    }),

  refresh: () => json<{ ok: true }>("/api/refresh", { method: "POST" }),
}

export interface SyncBody {
  lib: string
  folder: string
  selection_type: "all" | "season" | "episodes" | "movie"
  season?: number
  episodes?: [number, number][]
  unwatched_only?: boolean
}

export interface SyncResponse {
  job_id: string | null
  total_files: number
  total_media_files: number
  total_bytes: number
  error?: string
}
