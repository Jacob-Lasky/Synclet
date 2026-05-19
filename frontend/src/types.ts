export type Kind = "show" | "movie" | "youtube"

export interface Title {
  id: string
  lib: string
  folder: string
  name: string
  kind: Kind
  year: number | null
  ep_count: number
  synced_files: number
  has_synced: boolean
  watched_count: number
  watched_pct: number
  synced_pct: number
}

export interface Episode {
  season: number
  episode: number
  title: string
  size_bytes: number
  files: string[]
  is_synced: boolean
  watch_state: "watched" | "unwatched" | "progress"
  watch_pct: number
}

export interface Season {
  season: number
  total_bytes: number
  synced_episodes: number
  watched_episodes: number
  episodes: Episode[]
}

export interface MovieFile {
  path: string
  name: string
  size_bytes: number
  is_video: boolean
  is_synced: boolean
}

export interface TitleDetail {
  id: string
  lib: string
  folder: string
  name: string
  kind: Kind
  year: number | null
  total_bytes: number
  synced_bytes: number
  files: MovieFile[]
  seasons: Season[]
  watched_episodes?: number
  watched?: boolean
}

export interface DiskUsage {
  total: number
  used: number
  free: number
  pct: number
  synced_titles: number
  synced_bytes: number
}

export interface LibraryInfo {
  id: string
  label: string
  short: string
  kind: Kind
  sync_sub: string
}

export interface StateBundle {
  titles: Title[]
  disk: DiskUsage
  libraries: LibraryInfo[]
}

export interface Job {
  id: string
  op: "sync" | "unsync"
  status: "queued" | "running" | "done" | "error"
  total_files: number
  total_media_files: number
  processed_files: number
  processed_media_files: number
  processed_bytes: number
  total_bytes: number
  current_file: string
  title: string
  started_at: number
  ended_at: number
  error: string
}

export interface SyncedEntry {
  title: string
  folder: string
  lib: string | null
  kind: string
  size_bytes: number
  new_unwatched: { season: number; episode: number; title: string; size_bytes: number }[]
}

export interface WatchlistEntry {
  title: string
  category: string
  guid: string
  matched: boolean
  lib?: string
  folder?: string
  name?: string
  watched_pct?: number
  synced_pct?: number
  kind?: Kind
}

export interface WatchedFiles {
  title: string
  lib: string
  folder: string
  files: string[]
  size_bytes: number
  file_count: number
}

export interface HangingFile {
  path: string
  rel: string
  size_bytes: number
}

export interface ResolveResult {
  found: boolean
  lib?: string
  folder?: string
  name?: string
  kind?: Kind
  reason?: string
  match_method?: string
  via?: string
}

export type Tab = "library" | "synced" | "watchlist" | "maintenance"
