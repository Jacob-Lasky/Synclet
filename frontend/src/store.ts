import { reactive, computed } from "vue"
import { api } from "./api"
import { fuzzyScore } from "./fuzzy"
import type { DiskUsage, Job, LibraryInfo, Tab, Title } from "./types"

interface State {
  // Catalog
  titles: Title[]
  disk: DiskUsage | null
  libraries: LibraryInfo[]
  loaded: boolean

  // UI
  tab: Tab
  query: string
  libFilter: Set<string>           // empty = all
  stateFilter: Set<string>         // empty = all; values: unwatched | watching | synced | new | watchlist

  // Drawer
  detail: { lib: string; folder: string } | null

  // Jobs
  jobs: Record<string, Job>
  toasts: Toast[]
}

export interface Toast {
  id: string
  kind: "info" | "success" | "error"
  text: string
  jobId?: string
}

// Session-only overlay of items the user explicitly marked watched via the
// Mark-watched buttons. We need this because Plex's scrobble is asynchronous
// from Synclet's perspective: the WatchState daemon polls Plex on its own
// schedule and the local SQLite DB lags by minutes. Without this overlay,
// any refreshInPlace after a successful scrobble would re-read the stale
// watchstate and overwrite the optimistic update — the checkmarks would
// appear, then revert seconds later.
//
// Keyed by `${lib}/${folder}/${season}/${episode}` for episodes,
// `${lib}/${folder}//` for movies. Cleared on page reload (intentional —
// by then WatchState should have caught up).
const scrobbledOverlay = new Set<string>()

function overlayKey(lib: string, folder: string, season: number | null = null, episode: number | null = null): string {
  return `${lib}/${folder}/${season ?? ""}/${episode ?? ""}`
}

export function recordScrobbled(lib: string, folder: string, season: number | null = null, episode: number | null = null): void {
  scrobbledOverlay.add(overlayKey(lib, folder, season, episode))
}

/** True if (lib, folder, season?, episode?) was scrobbled in this session. */
export function isScrobbledThisSession(lib: string, folder: string, season: number | null = null, episode: number | null = null): boolean {
  return scrobbledOverlay.has(overlayKey(lib, folder, season, episode))
}

export const store = reactive<State>({
  titles: [],
  disk: null,
  libraries: [],
  loaded: false,

  tab: "library",
  query: "",
  libFilter: new Set(),
  stateFilter: new Set(),

  detail: null,

  jobs: {},
  toasts: [],
})

// ── Loading ────────────────────────────────────────────────────────────────

let stateInflight: Promise<void> | null = null

export async function loadState(refresh = false): Promise<void> {
  if (stateInflight) return stateInflight
  stateInflight = (async () => {
    try {
      const data = await api.state(refresh)
      store.titles = data.titles
      store.disk = data.disk
      store.libraries = data.libraries ?? []
      store.loaded = true
    } finally {
      stateInflight = null
    }
  })()
  return stateInflight
}

// ── Filters ────────────────────────────────────────────────────────────────

export const filteredTitles = computed<Title[]>(() => {
  const q = store.query.trim()
  const lib = store.libFilter
  const sf = store.stateFilter

  let list = store.titles
  if (lib.size > 0) list = list.filter(t => lib.has(t.lib))

  if (sf.size > 0) {
    list = list.filter(t => {
      if (sf.has("synced") && !t.has_synced) return false
      if (sf.has("unwatched") && t.watched_pct >= 80) return false
      if (sf.has("watching") && (t.watched_pct === 0 || t.watched_pct >= 100)) return false
      // "new" = has synced + some eps remain unwatched
      if (sf.has("new") && !(t.has_synced && t.watched_pct < 100)) return false
      return true
    })
  }

  if (!q) return list

  return list
    .map(t => ({ t, s: fuzzyScore(q, t.name) }))
    .filter(x => x.s > 0.1)
    .sort((a, b) => b.s - a.s)
    .map(x => x.t)
})

// ── Drawer ─────────────────────────────────────────────────────────────────

export function openDetail(lib: string, folder: string): void {
  store.detail = { lib, folder }
}

export function closeDetail(): void {
  store.detail = null
}

// ── Jobs ───────────────────────────────────────────────────────────────────

const JOB_POLL_MS = 600
const activePolls = new Set<string>()

export interface JobLabel {
  action: "Sync" | "Unsync"
  name: string                     // title to render in the toast
  totalMediaFiles: number          // count of video files (subs/etc. not counted)
}

function items(n: number): string {
  return `${n} item${n === 1 ? "" : "s"}`
}

export function trackJob(jobId: string, label: JobLabel): void {
  if (activePolls.has(jobId)) return
  activePolls.add(jobId)

  const toastId = pushToast({
    kind: "info",
    text: `${label.action}ing ${label.name} — ${items(label.totalMediaFiles)}`,
    jobId,
  })

  ;(async () => {
    try {
      while (true) {
        const job = await api.job(jobId)
        store.jobs[jobId] = job

        if (job.status === "done") {
          const past = label.action === "Sync" ? "Synced" : "Unsynced"
          const n = job.processed_media_files
          // Bytes are only meaningful for sync (unsync just deletes); show both
          // for sync, count-only for unsync.
          const tail = label.action === "Sync"
            ? ` (${humanSize(job.processed_bytes)})`
            : ""
          updateToast(toastId, {
            kind: "success",
            text: `${past} ${label.name} — ${items(n)}${tail}`,
          })
          await loadState(true)
          break
        }
        if (job.status === "error") {
          updateToast(toastId, {
            kind: "error",
            text: `${label.action} ${label.name} failed: ${job.error}`,
          })
          break
        }
        await sleep(JOB_POLL_MS)
      }
    } finally {
      activePolls.delete(jobId)
      // Auto-dismiss after success
      setTimeout(() => dismissToast(toastId), 4500)
    }
  })()
}

function sleep(ms: number): Promise<void> {
  return new Promise(r => setTimeout(r, ms))
}

// ── Toasts ─────────────────────────────────────────────────────────────────

let toastCounter = 0

export function pushToast(t: Omit<Toast, "id">): string {
  const id = `t${++toastCounter}`
  store.toasts.push({ id, ...t })
  return id
}

export function updateToast(id: string, patch: Partial<Toast>): void {
  const t = store.toasts.find(t => t.id === id)
  if (t) Object.assign(t, patch)
}

export function dismissToast(id: string): void {
  const i = store.toasts.findIndex(t => t.id === id)
  if (i >= 0) store.toasts.splice(i, 1)
}

// ── Format helpers (also used in components) ───────────────────────────────

export function libraryShort(libId: string): string {
  return store.libraries.find(l => l.id === libId)?.short ?? "??"
}

export function libraryLabel(libId: string): string {
  return store.libraries.find(l => l.id === libId)?.label ?? libId
}

export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ["KB", "MB", "GB", "TB", "PB"]
  let n = bytes / 1024
  for (const u of units) {
    if (n < 1024) return `${n.toFixed(n < 10 ? 1 : 0)} ${u}`
    n /= 1024
  }
  return `${n.toFixed(1)} EB`
}

export function epCode(s: number, e: number): string {
  return `S${String(s).padStart(2, "0")}E${String(e).padStart(2, "0")}`
}
