import { reactive, computed } from "vue"
import { api } from "./api"
import { fuzzyScore } from "./fuzzy"
import type {
    CoverageEntry,
    DiskUsage,
    Job,
    LibraryInfo,
    Tab,
    Title,
} from "./types"

interface State {
    // Catalog
    titles: Title[]
    disk: DiskUsage | null
    libraries: LibraryInfo[]
    loaded: boolean

    // UI
    tab: Tab
    query: string
    libFilter: Set<string> // empty = all
    stateFilter: Set<string> // empty = all; values: unwatched | watching | synced | new | watchlist

    // Drawer
    detail: { lib: string; folder: string } | null

    // Tab badge counts. Maintenance is the sum of actionable items
    // (pending + watched-in-synced + hanging); Watchlist is the watchlist
    // item count. Refreshed on app mount and after relevant actions.
    maintenanceCount: number | null
    watchlistCount: number | null

    // Per-library WatchState coverage. Loaded once on mount; entries with
    // watchstate_rows === 0 surface a banner so the user knows their marks
    // for that library round-trip via Plex direct (no Jellyfin aggregation).
    coverage: CoverageEntry[] | null

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
// Mark-watched buttons. The backend's WatchState SQLite lags Plex by minutes
// (when the WatchState daemon's poll cycle hasn't fired yet) AND has zero
// coverage for some Plex sections (notably YouTube — see /api/coverage).
// The backend now invalidates its Plex-direct caches on every successful
// scrobble so the next read pulls fresh viewCount data, but there is still
// a window between the scrobble response and that next read where the
// overlay keeps checkmarks visible without a refresh round-trip.
//
// Keyed by `${lib}/${folder}/${season}/${episode}` for episodes,
// `${lib}/${folder}//` for movies. Cleared on page reload (intentional —
// by then the backend caches have refreshed and Plex's viewCount /
// WatchState's row are authoritative again).
const scrobbledOverlay = new Set<string>()

// The inverse overlay: items the user explicitly marked UNwatched this session.
// Same staleness window as scrobbledOverlay (WatchState lags Plex), just the
// other direction. The two are kept mutually exclusive — recording one drops
// the key from the other — so the most recent gesture always wins (last-write-
// wins) and an item is never simultaneously "watched" and "unwatched" overlaid.
const unwatchedOverlay = new Set<string>()

function overlayKey(
    lib: string,
    folder: string,
    season: number | null = null,
    episode: number | null = null
): string {
    return `${lib}/${folder}/${season ?? ""}/${episode ?? ""}`
}

export function recordScrobbled(
    lib: string,
    folder: string,
    season: number | null = null,
    episode: number | null = null
): void {
    const k = overlayKey(lib, folder, season, episode)
    unwatchedOverlay.delete(k)
    scrobbledOverlay.add(k)
}

export function recordUnwatched(
    lib: string,
    folder: string,
    season: number | null = null,
    episode: number | null = null
): void {
    const k = overlayKey(lib, folder, season, episode)
    scrobbledOverlay.delete(k)
    unwatchedOverlay.add(k)
}

/** True if (lib, folder, season?, episode?) was scrobbled in this session. */
export function isScrobbledThisSession(
    lib: string,
    folder: string,
    season: number | null = null,
    episode: number | null = null
): boolean {
    return scrobbledOverlay.has(overlayKey(lib, folder, season, episode))
}

/** True if (lib, folder, season?, episode?) was marked unwatched this session. */
export function isUnwatchedThisSession(
    lib: string,
    folder: string,
    season: number | null = null,
    episode: number | null = null
): boolean {
    return unwatchedOverlay.has(overlayKey(lib, folder, season, episode))
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

    maintenanceCount: null,
    watchlistCount: null,
    coverage: null,

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

// Tab-badge count loaders. Failures intentionally swallow into null so the
// badge just disappears instead of throwing; counts are advisory UI, not
// load-bearing.

export async function loadMaintenanceCount(): Promise<void> {
    try {
        const r = await api.maintCounts()
        store.maintenanceCount = r.total
    } catch {
        store.maintenanceCount = null
    }
}

export async function loadWatchlistCount(): Promise<void> {
    try {
        const r = await api.watchlist()
        store.watchlistCount = r.items.length
    } catch {
        store.watchlistCount = null
    }
}

export async function loadCoverage(): Promise<void> {
    try {
        const r = await api.coverage()
        store.coverage = r.libraries
    } catch {
        store.coverage = null
    }
}

/** Libraries where WatchState's coverage is significantly below what Plex
 *  advertises (observed < half of expected). These are the libraries where
 *  Synclet's Plex-direct fallback is doing real work; the banner names them
 *  so the user knows the round trip differs (no Jellyfin overlay).
 *
 *  Empty array when coverage is fully loaded and every library is well-
 *  covered; null while loading or on fetch failure (banner stays hidden). */
const _COVERAGE_THRESHOLD = 0.5

export const uncoveredLibraries = computed<CoverageEntry[] | null>(() => {
    if (store.coverage == null) return null
    return store.coverage.filter((entry) => {
        if (entry.expected_rows === 0) {
            // Plex section is empty or unreachable; nothing to compare. Use
            // observed-only as a fallback signal (zero rows is still useful
            // to surface).
            return entry.watchstate_rows === 0
        }
        return entry.watchstate_rows < entry.expected_rows * _COVERAGE_THRESHOLD
    })
})

// ── Filters ────────────────────────────────────────────────────────────────

export const filteredTitles = computed<Title[]>(() => {
    const q = store.query.trim()
    const lib = store.libFilter
    const sf = store.stateFilter

    let list = store.titles
    if (lib.size > 0) list = list.filter((t) => lib.has(t.lib))

    if (sf.size > 0) {
        list = list.filter((t) => {
            if (sf.has("synced") && !t.has_synced) return false
            if (sf.has("unwatched") && t.watched_pct >= 80) return false
            if (
                sf.has("watching") &&
                (t.watched_pct === 0 || t.watched_pct >= 100)
            )
                return false
            // "new" = has synced + some eps remain unwatched
            if (sf.has("new") && !(t.has_synced && t.watched_pct < 100))
                return false
            return true
        })
    }

    if (!q) return list

    return list
        .map((t) => ({ t, s: fuzzyScore(q, t.name) }))
        .filter((x) => x.s > 0.1)
        .sort((a, b) => b.s - a.s)
        .map((x) => x.t)
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
    name: string // title to render in the toast
    totalMediaFiles: number // count of video files (subs/etc. not counted)
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
                    const tail =
                        label.action === "Sync"
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
    return new Promise((r) => setTimeout(r, ms))
}

// ── Toasts ─────────────────────────────────────────────────────────────────

let toastCounter = 0

export function pushToast(t: Omit<Toast, "id">): string {
    const id = `t${++toastCounter}`
    store.toasts.push({ id, ...t })
    return id
}

export function updateToast(id: string, patch: Partial<Toast>): void {
    const t = store.toasts.find((t) => t.id === id)
    if (t) Object.assign(t, patch)
}

export function dismissToast(id: string): void {
    const i = store.toasts.findIndex((t) => t.id === id)
    if (i >= 0) store.toasts.splice(i, 1)
}

// ── Format helpers (also used in components) ───────────────────────────────

export function libraryShort(libId: string): string {
    return store.libraries.find((l) => l.id === libId)?.short ?? "??"
}

export function libraryLabel(libId: string): string {
    return store.libraries.find((l) => l.id === libId)?.label ?? libId
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
