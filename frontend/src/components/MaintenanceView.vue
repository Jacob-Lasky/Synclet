<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { api } from "../api"
import type {
    CleanupSummary,
    HangingFile,
    IgnoredGrouped,
    IgnoredPendingRef,
    IgnoreOp,
    PendingEpisode,
    PendingGroup,
    PendingItemRef,
    PendingSeason,
    ResolveAction,
    ResolveItemResult,
    ResolveResponse,
    WatchedFiles,
} from "../types"
import {
    humanSize,
    loadMaintenanceCount,
    loadState,
    pushToast,
    store,
} from "../store"

const watched = ref<WatchedFiles[]>([])
const hanging = ref<HangingFile[]>([])
const pending = ref<PendingGroup[]>([])
const ignoredList = ref<IgnoredGrouped>({
    version: 1,
    pending: [],
    watched: [],
    hanging: [],
})
const ignoredOpen = ref(false)
const loading = ref(true)
const error = ref("")
const removing = ref(false)
const resolving = ref(false)
// Tracks which show / season cards are expanded. Keys: `${folder}` for shows,
// `${folder}|${season}` for seasons.
const expanded = ref<Set<string>>(new Set())

// Session-scoped cache of entries that were ignored this session, keyed by
// ignoreCacheKey(op). Lets unignore restore the entry into its source list
// without a full refetch. Stale across browser reloads (the module re-inits
// and the cache is empty); cross-session unignore falls back to a focused
// per-list refetch, NOT the full `load()` that would gate the pane on
// `loading.value`. See unignoreItem.
type CachedIgnoredEntry =
    | { kind: "watched"; entry: WatchedFiles }
    | { kind: "hanging"; entry: HangingFile }
    | { kind: "pending-movie"; group: PendingGroup }
    | { kind: "pending-episode"; group: PendingGroup }

const ignoredEntryCache = new Map<string, CachedIgnoredEntry>()

function ignoreCacheKey(op: IgnoreOp): string {
    if (op.kind === "watched") {
        return `w:${op.ref.lib}/${op.ref.folder}`
    }
    if (op.kind === "hanging") {
        return `h:${op.ref.path}`
    }
    const r = op.ref
    return `p:${r.sync_sub}/${r.folder}/${r.season ?? ""}/${r.episode ?? ""}`
}

async function load(): Promise<void> {
    loading.value = true
    error.value = ""
    try {
        const [w, h, p, ig] = await Promise.all([
            api.maintWatched(),
            api.maintHanging(),
            api.maintPending(),
            api.maintIgnored(),
        ])
        watched.value = w.items
        hanging.value = h.items
        pending.value = p.items
        ignoredList.value = ig
        // Surface the same counts to the tab badge. Cheap: load() already did
        // the FS walks the counts endpoint would do; this round-trip just sums
        // them server-side.
        loadMaintenanceCount()
    } catch (e) {
        error.value = (e as Error).message
    } finally {
        loading.value = false
    }
}

const ignoredCount = computed(
    () =>
        ignoredList.value.pending.length +
        ignoredList.value.watched.length +
        ignoredList.value.hanging.length
)

// ── Optimistic-update primitive ────────────────────────────────────────────
//
// Snapshot-and-rollback wrapper for optimistic mutations of one-or-more refs.
// All four action paths in this component (resolve, ignore, unignore, remove)
// share the same shape:
//   1. capture the pre-mutation state,
//   2. apply a local mutation so the UI updates instantly,
//   3. call the server,
//   4a. on success, run a per-action onResult callback (which may toast or, in
//       the partial-failure case, decide to partially un-do the mutation),
//   4b. on a thrown error, restore the snapshot, re-sync the maintenance badge
//       from the server, and let the caller toast.
//
// Kept local (not in a shared module) because rollback needs the component's
// refs in scope. Lifted out only to DRY the boilerplate without leaking
// component-internal state to a generic helper.
//
// The onResult callback receives a `rollback` thunk so it can also restore
// state for logical failures (server returned `error` or `ok: false`). The
// thunk additionally calls loadMaintenanceCount() so badge drift gets repaired
// the same way it does for thrown errors.
async function optimisticUpdate<TSnap, TResult>(opts: {
    snapshot: () => TSnap
    mutate: () => void
    apiCall: () => Promise<TResult>
    rollback: (snap: TSnap) => void
    onResult: (result: TResult, rollback: () => void) => void
    onError: (err: Error) => void
}): Promise<void> {
    const snap = opts.snapshot()
    opts.mutate()
    try {
        const result = await opts.apiCall()
        opts.onResult(result, () => {
            opts.rollback(snap)
            loadMaintenanceCount()
        })
    } catch (e) {
        opts.rollback(snap)
        loadMaintenanceCount()
        opts.onError(e as Error)
    }
}

// Bump the maintenance tab badge by `delta` (negative shrinks, positive
// grows). No-op when the badge hasn't loaded yet. Authoritative refresh
// happens via loadMaintenanceCount() on rollback / after-action; this is
// only the optimistic delta so the badge tracks the UI instantly.
function bumpBadge(delta: number): void {
    if (typeof store.maintenanceCount !== "number") return
    store.maintenanceCount = Math.max(0, store.maintenanceCount + delta)
}

// ── Local mutation helpers ─────────────────────────────────────────────────

// Drop an ignored ref from its source list (watched, hanging, or pending).
// Used during ignoreItem's optimistic mutate.
function removePendingLocal(op: IgnoreOp): void {
    if (op.kind === "watched") {
        const r = op.ref
        watched.value = watched.value.filter(
            (w) => !(w.lib === r.lib && w.folder === r.folder)
        )
    } else if (op.kind === "hanging") {
        const r = op.ref
        hanging.value = hanging.value.filter((h) => h.path !== r.path)
    } else {
        const r = op.ref
        if (r.season === null || r.season === undefined) {
            // Movie-level pending: drop the entire group.
            pending.value = pending.value.filter(
                (g) => !(g.sync_sub === r.sync_sub && g.folder === r.folder)
            )
        } else {
            // Episode-level pending: drop the episode; if the season becomes
            // empty, drop the season; if the group becomes empty, drop the group.
            pending.value = pending.value
                .map((g) => {
                    if (g.sync_sub !== r.sync_sub || g.folder !== r.folder)
                        return g
                    const seasons = (g.seasons ?? [])
                        .map((s) => {
                            if (s.season !== r.season) return s
                            return {
                                ...s,
                                episodes: s.episodes.filter(
                                    (e) => e.episode !== r.episode
                                ),
                            }
                        })
                        .filter((s) => s.episodes.length > 0)
                    return { ...g, seasons }
                })
                .filter(
                    (g) => g.kind === "movie" || (g.seasons?.length ?? 0) > 0
                )
        }
    }
}

// Drop a list of pending refs from pending.value, then prune empty seasons /
// groups. Used during resolveItems' optimistic mutate. Scoped specifically to
// PendingItemRef[] (the resolve flow never touches watched/hanging) so we
// avoid the watched/hanging branches that removePendingLocal carries for the
// ignore-flow.
function removeResolvedItemsLocal(items: PendingItemRef[]): void {
    if (items.length === 0) return
    // Index refs by group key for O(items) lookup during the single
    // pending.value walk below. "*" marks a movie-level drop (whole group).
    const byGroup = new Map<string, Set<string>>()
    for (const r of items) {
        const groupKey = `${r.sync_sub} ${r.folder}`
        if (r.season === null || r.season === undefined) {
            byGroup.set(groupKey, new Set(["*"]))
            continue
        }
        const epKey = `${r.season} ${r.episode}`
        const set = byGroup.get(groupKey)
        if (set?.has("*")) continue
        if (set) set.add(epKey)
        else byGroup.set(groupKey, new Set([epKey]))
    }
    pending.value = pending.value
        .map((g) => {
            const set = byGroup.get(`${g.sync_sub} ${g.folder}`)
            if (!set) return g
            if (set.has("*")) return null
            const seasons = (g.seasons ?? [])
                .map((s) => ({
                    ...s,
                    episodes: s.episodes.filter(
                        (e) => !set.has(`${s.season} ${e.episode}`)
                    ),
                }))
                .filter((s) => s.episodes.length > 0)
            return { ...g, seasons }
        })
        .filter(
            (g): g is PendingGroup =>
                g !== null &&
                (g.kind === "movie" || (g.seasons?.length ?? 0) > 0)
        )
}

// Remove an entry from ignoredList.value matching `op`. Mirrors the wire
// shape of api/maintenance/ignored. Used by unignoreItem's optimistic mutate.
function removeFromIgnoredListLocal(op: IgnoreOp): void {
    if (op.kind === "watched") {
        const r = op.ref
        ignoredList.value = {
            ...ignoredList.value,
            watched: ignoredList.value.watched.filter(
                (w) => !(w.lib === r.lib && w.folder === r.folder)
            ),
        }
    } else if (op.kind === "hanging") {
        const r = op.ref
        ignoredList.value = {
            ...ignoredList.value,
            hanging: ignoredList.value.hanging.filter((h) => h.path !== r.path),
        }
    } else {
        const r = op.ref
        const season = r.season ?? null
        const episode = r.episode ?? null
        ignoredList.value = {
            ...ignoredList.value,
            pending: ignoredList.value.pending.filter(
                (p) =>
                    !(
                        p.sync_sub === r.sync_sub &&
                        p.folder === r.folder &&
                        p.season === season &&
                        p.episode === episode
                    )
            ),
        }
    }
}

// Stash the entry being ignored so unignoreItem can restore it without a
// refetch. Called from ignoreItem BEFORE the optimistic mutate runs (so the
// entry is still in its source list and findable).
function cacheIgnoredEntry(op: IgnoreOp): void {
    const key = ignoreCacheKey(op)
    if (op.kind === "watched") {
        const r = op.ref
        const entry = watched.value.find(
            (w) => w.lib === r.lib && w.folder === r.folder
        )
        if (entry) ignoredEntryCache.set(key, { kind: "watched", entry })
        return
    }
    if (op.kind === "hanging") {
        const r = op.ref
        const entry = hanging.value.find((h) => h.path === r.path)
        if (entry) ignoredEntryCache.set(key, { kind: "hanging", entry })
        return
    }
    const r = op.ref
    const group = pending.value.find(
        (g) => g.sync_sub === r.sync_sub && g.folder === r.folder
    )
    if (!group) return
    if (r.season === null || r.season === undefined) {
        ignoredEntryCache.set(key, { kind: "pending-movie", group })
    } else {
        // Episode-level: cache the full original group so the episode shape
        // (with its already_watched_in_plex / rating_key) can be reconstructed
        // on unignore even if the group has since been drained.
        ignoredEntryCache.set(key, { kind: "pending-episode", group })
    }
}

// Inverse of cacheIgnoredEntry: re-insert a cached entry into its source list.
// Returns true on a successful restore, false on miss OR when an internal
// lookup fails (e.g. the cache has a pending-episode entry but the cached
// group somehow lacks the matching season). The caller falls back to a
// focused refetch on false.
//
// Intentionally does NOT delete the cache entry on success. The caller deletes
// AFTER the server confirms the unignore so a rolled-back call leaves the
// cache in a state where retrying still hits the fast path.
function restoreIgnoredEntry(op: IgnoreOp): boolean {
    const key = ignoreCacheKey(op)
    const cached = ignoredEntryCache.get(key)
    if (!cached) return false

    if (cached.kind === "watched") {
        watched.value = [...watched.value, cached.entry]
    } else if (cached.kind === "hanging") {
        hanging.value = [...hanging.value, cached.entry]
    } else if (cached.kind === "pending-movie") {
        pending.value = [...pending.value, cached.group]
    } else {
        // pending-episode: splice the specific cached episode back into the
        // current pending.value. The group may still be present (other eps
        // still pending) or fully drained.
        const r = op.ref as IgnoredPendingRef
        const cachedGroup = cached.group
        const cachedSeason = (cachedGroup.seasons ?? []).find(
            (s) => s.season === r.season
        )
        const cachedEpisode = cachedSeason?.episodes.find(
            (e) => e.episode === r.episode
        )
        if (!cachedSeason || !cachedEpisode) return false

        const existing = pending.value.find(
            (g) =>
                g.sync_sub === cachedGroup.sync_sub &&
                g.folder === cachedGroup.folder
        )
        if (!existing) {
            // Group fully drained; restore with just the cached episode.
            pending.value = [
                ...pending.value,
                {
                    ...cachedGroup,
                    seasons: [
                        {
                            season: cachedSeason.season,
                            episodes: [cachedEpisode],
                        },
                    ],
                },
            ]
        } else {
            const seasons = existing.seasons ?? []
            const sIdx = seasons.findIndex((s) => s.season === r.season)
            const updatedSeasons: PendingSeason[] =
                sIdx === -1
                    ? [
                          ...seasons,
                          {
                              season: cachedSeason.season,
                              episodes: [cachedEpisode],
                          },
                      ]
                    : seasons.map((s, i) =>
                          i === sIdx
                              ? {
                                    ...s,
                                    episodes: [...s.episodes, cachedEpisode],
                                }
                              : s
                      )
            const updated: PendingGroup = {
                ...existing,
                seasons: updatedSeasons,
            }
            pending.value = pending.value.map((g) =>
                g === existing ? updated : g
            )
        }
    }
    return true
}

// Refetch a single maintenance list. Cheaper than load() in two ways: only one
// of the four endpoints fires, and loading.value stays false so the
// maintenance pane does not collapse into "Scanning…". Used as the fallback
// when an action's optimistic mutate can't fully reconstruct local state.
//
// Errors are swallowed: badge drift gets repaired by the trailing
// loadMaintenanceCount() and the user can recover by switching tabs or
// refreshing. A toast would over-surface a transient hiccup.
function refetchSourceList(kind: IgnoreOp["kind"]): void {
    const fetchAndApply: () => Promise<void> =
        kind === "watched"
            ? () =>
                  api.maintWatched().then((r) => {
                      watched.value = r.items
                  })
            : kind === "hanging"
              ? () =>
                    api.maintHanging().then((r) => {
                        hanging.value = r.items
                    })
              : () =>
                    api.maintPending().then((r) => {
                        pending.value = r.items
                    })
    fetchAndApply().catch(() => {
        /* swallow, see header comment */
    })
    loadMaintenanceCount()
}

// ── Action handlers ────────────────────────────────────────────────────────

async function ignoreItem(op: IgnoreOp, label: string): Promise<void> {
    // Stash the entry first so unignoreItem can restore it without a refetch.
    cacheIgnoredEntry(op)

    await optimisticUpdate({
        snapshot: () => ({
            watched: watched.value,
            hanging: hanging.value,
            pending: pending.value,
            ignoredList: ignoredList.value,
        }),
        mutate: () => {
            removePendingLocal(op)
            if (op.kind === "watched") {
                ignoredList.value = {
                    ...ignoredList.value,
                    watched: [...ignoredList.value.watched, op.ref],
                }
            } else if (op.kind === "hanging") {
                ignoredList.value = {
                    ...ignoredList.value,
                    hanging: [...ignoredList.value.hanging, op.ref],
                }
            } else {
                const r = op.ref
                ignoredList.value = {
                    ...ignoredList.value,
                    pending: [
                        ...ignoredList.value.pending,
                        {
                            sync_sub: r.sync_sub,
                            folder: r.folder,
                            season: r.season ?? null,
                            episode: r.episode ?? null,
                        },
                    ],
                }
            }
            bumpBadge(-1)
        },
        apiCall: () => api.maintIgnore(op),
        rollback: (snap) => {
            watched.value = snap.watched
            hanging.value = snap.hanging
            pending.value = snap.pending
            ignoredList.value = snap.ignoredList
        },
        onResult: (r, rollback) => {
            if (!r.ok) {
                rollback()
                pushToast({ kind: "error", text: `Could not ignore ${label}` })
                return
            }
            pushToast({ kind: "success", text: `Ignored ${label}` })
        },
        onError: (e) => pushToast({ kind: "error", text: e.message }),
    })
}

async function unignoreItem(op: IgnoreOp, label: string): Promise<void> {
    // Compute whether the local cache can fully restore the entry BEFORE the
    // mutate runs. Use the boolean result, not just cache.has(), because the
    // pending-episode branch can fail an internal lookup even when the cache
    // key is present (defensive: stale or partial cache). On false the
    // onResult success branch falls back to a focused refetch.
    let restored = false
    const cacheKey = ignoreCacheKey(op)
    await optimisticUpdate({
        snapshot: () => ({
            watched: watched.value,
            hanging: hanging.value,
            pending: pending.value,
            ignoredList: ignoredList.value,
        }),
        mutate: () => {
            restored = restoreIgnoredEntry(op)
            if (restored) {
                // Local restore bumped the source list; mirror that in the
                // badge. On a non-restore the badge bump rides along with the
                // refetch path's loadMaintenanceCount() below.
                bumpBadge(+1)
            }
            removeFromIgnoredListLocal(op)
        },
        apiCall: () => api.maintUnignore(op),
        rollback: (snap) => {
            watched.value = snap.watched
            hanging.value = snap.hanging
            pending.value = snap.pending
            ignoredList.value = snap.ignoredList
        },
        onResult: (r, rollback) => {
            if (!r.ok) {
                rollback()
                pushToast({
                    kind: "error",
                    text: `Could not unignore ${label}`,
                })
                return
            }
            pushToast({ kind: "success", text: `Unignored ${label}` })
            if (restored) {
                // Server confirmed; drop the cache entry now (not in
                // restoreIgnoredEntry) so a thrown-then-retried unignore keeps
                // hitting the fast path.
                ignoredEntryCache.delete(cacheKey)
            } else {
                // Cross-session unignore: re-fetch only the relevant source
                // list so the entry reappears. Does NOT call load(); the
                // maintenance pane stays mounted.
                refetchSourceList(op.kind)
            }
        },
        onError: (e) => pushToast({ kind: "error", text: e.message }),
    })
}

onMounted(load)

function toggleShow(folder: string): void {
    if (expanded.value.has(folder)) {
        expanded.value.delete(folder)
    } else {
        expanded.value.add(folder)
    }
    // reactive Set: reassign to trigger re-render
    expanded.value = new Set(expanded.value)
}

function toggleSeason(folder: string, season: number): void {
    const key = `${folder}|${season}`
    if (expanded.value.has(key)) {
        expanded.value.delete(key)
    } else {
        expanded.value.add(key)
    }
    expanded.value = new Set(expanded.value)
}

function totalEpisodes(g: PendingGroup): number {
    return (g.seasons ?? []).reduce((n, s) => n + s.episodes.length, 0)
}

function refsForGroup(g: PendingGroup): PendingItemRef[] {
    if (g.kind === "movie") {
        return [{ sync_sub: g.sync_sub, folder: g.folder }]
    }
    return (g.seasons ?? []).flatMap((s) =>
        s.episodes.map((e) => ({
            sync_sub: g.sync_sub,
            folder: g.folder,
            season: e.season,
            episode: e.episode,
        }))
    )
}

function refsForSeason(g: PendingGroup, season: number): PendingItemRef[] {
    const s = (g.seasons ?? []).find((s) => s.season === season)
    if (!s) return []
    return s.episodes.map((e) => ({
        sync_sub: g.sync_sub,
        folder: g.folder,
        season: e.season,
        episode: e.episode,
    }))
}

function refForEpisode(g: PendingGroup, ep: PendingEpisode): PendingItemRef {
    return {
        sync_sub: g.sync_sub,
        folder: g.folder,
        season: ep.season,
        episode: ep.episode,
    }
}

function summarizeResolve(results: ResolveItemResult[]): string {
    const counts = { ok: 0, scrobble_failed: 0, no_rating_key: 0, rejected: 0 }
    for (const r of results) counts[r.status] += 1
    const parts: string[] = []
    if (counts.ok) parts.push(`${counts.ok} scrobbled`)
    if (counts.rejected) parts.push(`${counts.rejected} rejected`)
    if (counts.scrobble_failed)
        parts.push(`${counts.scrobble_failed} scrobble failed`)
    if (counts.no_rating_key)
        parts.push(`${counts.no_rating_key} not found in Plex`)
    return parts.join(", ") || "no items"
}

function summarizeCleanup(c: CleanupSummary | undefined): string {
    if (!c || (c.removed_files === 0 && c.removed_dirs === 0)) return ""
    const parts: string[] = []
    if (c.removed_files)
        parts.push(
            `${c.removed_files} sidecar${c.removed_files === 1 ? "" : "s"}`
        )
    if (c.removed_dirs)
        parts.push(`${c.removed_dirs} folder${c.removed_dirs === 1 ? "" : "s"}`)
    return ` (cleaned ${parts.join(", ")})`
}

async function resolveItems(
    items: PendingItemRef[],
    action: ResolveAction,
    label: string
): Promise<void> {
    if (items.length === 0) return
    const verb = action === "confirm" ? "Mark watched" : "Reject"
    if (
        !confirm(
            `${verb} ${items.length} item${items.length === 1 ? "" : "s"}?\n\n${label}`
        )
    ) {
        return
    }
    resolving.value = true
    try {
        await optimisticUpdate<{ pending: PendingGroup[] }, ResolveResponse>({
            snapshot: () => ({ pending: pending.value }),
            mutate: () => {
                removeResolvedItemsLocal(items)
                bumpBadge(-items.length)
            },
            apiCall: () => api.maintResolve(items, action),
            rollback: (snap) => {
                pending.value = snap.pending
            },
            onResult: (res, rollback) => {
                if (res.error) {
                    rollback()
                    pushToast({ kind: "error", text: res.error })
                    return
                }
                const anyFailed = res.results.some(
                    (r) =>
                        r.status === "scrobble_failed" ||
                        r.status === "no_rating_key"
                )
                pushToast({
                    kind: anyFailed ? "error" : "success",
                    text: `${verb}: ${summarizeResolve(res.results)}${summarizeCleanup(res.cleanup)}`,
                })
                if (anyFailed) {
                    // Items the server failed to resolve are still pending
                    // server-side; the optimistic mutate wrongly removed
                    // them. Refetch just the pending list to bring them
                    // back. Doesn't set loading.value, so the pane stays
                    // mounted.
                    refetchSourceList("pending")
                }
                // Refresh state in the background (for the library grid's
                // synced/watched percentages); not awaited so the
                // maintenance view stays snappy.
                loadState(true)
            },
            onError: (e) => pushToast({ kind: "error", text: e.message }),
        })
    } finally {
        resolving.value = false
    }
}

const totalPendingEpisodes = computed(() =>
    pending.value.reduce(
        (n, g) => n + (g.kind === "movie" ? 1 : totalEpisodes(g)),
        0
    )
)

async function removePaths(paths: string[], label: string): Promise<void> {
    if (paths.length === 0) return
    if (
        !confirm(
            `Remove ${paths.length} file${paths.length === 1 ? "" : "s"}?\n\n${label}`
        )
    )
        return
    const pathSet = new Set(paths)
    removing.value = true
    try {
        await optimisticUpdate({
            snapshot: () => ({
                watched: watched.value,
                hanging: hanging.value,
            }),
            mutate: () => {
                const snapW = watched.value
                const snapH = hanging.value
                watched.value = watched.value
                    .map((w) => ({
                        ...w,
                        files: w.files.filter((p) => !pathSet.has(p)),
                    }))
                    .filter((w) => w.files.length > 0)
                hanging.value = hanging.value.filter(
                    (h) => !pathSet.has(h.path)
                )
                // Approximate badge decrement: number of titles+files dropped
                // from view. Server-authoritative refresh happens on rollback
                // (loadMaintenanceCount) and on the loadState(true) below.
                const dropped =
                    snapW.length -
                    watched.value.length +
                    (snapH.length - hanging.value.length)
                if (dropped > 0) bumpBadge(-dropped)
            },
            apiCall: () => api.maintRemove(paths),
            rollback: (snap) => {
                watched.value = snap.watched
                hanging.value = snap.hanging
            },
            onResult: (r) => {
                pushToast({
                    kind: "success",
                    text: `Removed ${r.removed} files (${humanSize(r.bytes_freed)})${summarizeCleanup(r.cleanup)}`,
                })
                // Refresh state in the background (for the library grid's
                // synced/watched percentages); not awaited so the
                // maintenance view stays snappy.
                loadState(true)
            },
            onError: (e) => pushToast({ kind: "error", text: e.message }),
        })
    } finally {
        removing.value = false
    }
}

function removeAllWatched(): void {
    const paths = watched.value.flatMap((w) => w.files)
    const total = watched.value.reduce((s, w) => s + w.size_bytes, 0)
    removePaths(
        paths,
        `${watched.value.length} title${watched.value.length === 1 ? "" : "s"} · ${humanSize(total)}`
    )
}

function removeAllHanging(): void {
    const paths = hanging.value.map((h) => h.path)
    const total = hanging.value.reduce((s, h) => s + h.size_bytes, 0)
    removePaths(
        paths,
        `${hanging.value.length} hanging files · ${humanSize(total)}`
    )
}

function totalWatchedBytes(): number {
    return watched.value.reduce((s, w) => s + w.size_bytes, 0)
}
function totalHangingBytes(): number {
    return hanging.value.reduce((s, h) => s + h.size_bytes, 0)
}
</script>

<template>
    <div class="view fade-in">
        <div v-if="loading" class="info">Scanning…</div>
        <div v-else-if="error" class="info err">{{ error }}</div>

        <template v-else>
            <section class="panel">
                <header>
                    <h2>Watched in synced-media</h2>
                    <span class="dim"
                        >{{ watched.length }} title{{
                            watched.length === 1 ? "" : "s"
                        }}
                        · {{ humanSize(totalWatchedBytes()) }}</span
                    >
                    <span class="spacer"></span>
                    <button
                        v-if="watched.length > 0"
                        class="danger"
                        :disabled="removing"
                        @click="removeAllWatched"
                    >
                        Remove all
                    </button>
                </header>
                <p v-if="watched.length === 0" class="empty">
                    Nothing watched in synced-media. Tidy!
                </p>
                <ul v-else>
                    <li v-for="w in watched" :key="w.lib + '/' + w.folder">
                        <span class="name">{{ w.title }}</span>
                        <span class="lib dim">{{ w.lib }}</span>
                        <span class="count dim">{{ w.file_count }} files</span>
                        <span class="size mono dim">{{
                            humanSize(w.size_bytes)
                        }}</span>
                        <button
                            class="danger mini"
                            :disabled="removing"
                            @click="removePaths(w.files, w.title)"
                        >
                            Remove
                        </button>
                        <button
                            class="ghost mini"
                            title="Mute this entry"
                            @click="
                                ignoreItem(
                                    {
                                        kind: 'watched',
                                        ref: { lib: w.lib, folder: w.folder },
                                    },
                                    w.title
                                )
                            "
                        >
                            Ignore
                        </button>
                    </li>
                </ul>
            </section>

            <section class="panel" data-testid="pending-deletions">
                <header>
                    <h2>Deleted, awaiting Plex sync</h2>
                    <span class="dim">
                        {{ pending.length }} title{{
                            pending.length === 1 ? "" : "s"
                        }}
                        <template
                            v-if="totalPendingEpisodes !== pending.length"
                        >
                            · {{ totalPendingEpisodes }} item{{
                                totalPendingEpisodes === 1 ? "" : "s"
                            }}
                        </template>
                    </span>
                </header>
                <p v-if="pending.length === 0" class="empty">
                    No pending deletions. Synced media matches the snapshot.
                </p>
                <ul v-else class="pending-list">
                    <li
                        v-for="g in pending"
                        :key="g.sync_sub + '/' + g.folder"
                        class="group"
                    >
                        <!-- Movie: single row, no nesting -->
                        <template v-if="g.kind === 'movie'">
                            <div class="row movie">
                                <span class="name">{{ g.title }}</span>
                                <span
                                    v-if="g.already_watched_in_plex"
                                    class="badge dim"
                                    >already watched in Plex</span
                                >
                                <span class="lib dim">{{ g.lib ?? "—" }}</span>
                                <span class="spacer"></span>
                                <button
                                    class="primary mini"
                                    :disabled="resolving"
                                    @click="
                                        resolveItems(
                                            refsForGroup(g),
                                            'confirm',
                                            g.title
                                        )
                                    "
                                >
                                    Mark watched
                                </button>
                                <button
                                    class="danger mini"
                                    :disabled="resolving"
                                    @click="
                                        resolveItems(
                                            refsForGroup(g),
                                            'reject',
                                            g.title
                                        )
                                    "
                                >
                                    Reject
                                </button>
                                <button
                                    class="ghost mini"
                                    title="Mute this entry"
                                    @click="
                                        ignoreItem(
                                            {
                                                kind: 'pending',
                                                ref: {
                                                    sync_sub: g.sync_sub,
                                                    folder: g.folder,
                                                    season: null,
                                                    episode: null,
                                                },
                                            },
                                            g.title
                                        )
                                    "
                                >
                                    Ignore
                                </button>
                            </div>
                        </template>

                        <!-- Show / YouTube: expandable header + nested seasons -->
                        <template v-else>
                            <div class="row show-header">
                                <button
                                    class="toggle"
                                    :aria-expanded="expanded.has(g.folder)"
                                    @click="toggleShow(g.folder)"
                                >
                                    <span class="caret">{{
                                        expanded.has(g.folder) ? "▾" : "▸"
                                    }}</span>
                                    <span class="name">{{ g.title }}</span>
                                </button>
                                <span class="dim">
                                    {{ totalEpisodes(g) }} episode{{
                                        totalEpisodes(g) === 1 ? "" : "s"
                                    }}
                                </span>
                                <span class="spacer"></span>
                                <button
                                    class="primary mini"
                                    :disabled="resolving"
                                    @click="
                                        resolveItems(
                                            refsForGroup(g),
                                            'confirm',
                                            `${g.title} (all)`
                                        )
                                    "
                                >
                                    Mark all watched
                                </button>
                                <button
                                    class="danger mini"
                                    :disabled="resolving"
                                    @click="
                                        resolveItems(
                                            refsForGroup(g),
                                            'reject',
                                            `${g.title} (all)`
                                        )
                                    "
                                >
                                    Reject all
                                </button>
                            </div>
                            <ul v-if="expanded.has(g.folder)" class="seasons">
                                <li
                                    v-for="s in g.seasons ?? []"
                                    :key="g.folder + '|' + s.season"
                                    class="season"
                                >
                                    <div class="row season-header">
                                        <button
                                            class="toggle"
                                            :aria-expanded="
                                                expanded.has(
                                                    `${g.folder}|${s.season}`
                                                )
                                            "
                                            @click="
                                                toggleSeason(g.folder, s.season)
                                            "
                                        >
                                            <span class="caret">
                                                {{
                                                    expanded.has(
                                                        `${g.folder}|${s.season}`
                                                    )
                                                        ? "▾"
                                                        : "▸"
                                                }}
                                            </span>
                                            <span class="season-label"
                                                >Season {{ s.season }}</span
                                            >
                                        </button>
                                        <span class="dim">
                                            {{ s.episodes.length }} ep{{
                                                s.episodes.length === 1
                                                    ? ""
                                                    : "s"
                                            }}
                                        </span>
                                        <span class="spacer"></span>
                                        <button
                                            class="primary mini"
                                            :disabled="resolving"
                                            @click="
                                                resolveItems(
                                                    refsForSeason(g, s.season),
                                                    'confirm',
                                                    `${g.title} S${s.season}`
                                                )
                                            "
                                        >
                                            Mark season watched
                                        </button>
                                        <button
                                            class="danger mini"
                                            :disabled="resolving"
                                            @click="
                                                resolveItems(
                                                    refsForSeason(g, s.season),
                                                    'reject',
                                                    `${g.title} S${s.season}`
                                                )
                                            "
                                        >
                                            Reject season
                                        </button>
                                    </div>
                                    <ul
                                        v-if="
                                            expanded.has(
                                                `${g.folder}|${s.season}`
                                            )
                                        "
                                        class="episodes"
                                    >
                                        <li
                                            v-for="e in s.episodes"
                                            :key="`${g.folder}|${e.season}|${e.episode}`"
                                            class="row episode"
                                        >
                                            <span class="ep-code mono">
                                                S{{
                                                    String(e.season).padStart(
                                                        2,
                                                        "0"
                                                    )
                                                }}E{{
                                                    String(e.episode).padStart(
                                                        2,
                                                        "0"
                                                    )
                                                }}
                                            </span>
                                            <span
                                                v-if="e.already_watched_in_plex"
                                                class="badge dim"
                                            >
                                                already watched in Plex
                                            </span>
                                            <span class="spacer"></span>
                                            <button
                                                class="primary mini"
                                                :disabled="resolving"
                                                @click="
                                                    resolveItems(
                                                        [refForEpisode(g, e)],
                                                        'confirm',
                                                        `${g.title} S${e.season}E${e.episode}`
                                                    )
                                                "
                                            >
                                                Mark watched
                                            </button>
                                            <button
                                                class="danger mini"
                                                :disabled="resolving"
                                                @click="
                                                    resolveItems(
                                                        [refForEpisode(g, e)],
                                                        'reject',
                                                        `${g.title} S${e.season}E${e.episode}`
                                                    )
                                                "
                                            >
                                                Reject
                                            </button>
                                            <button
                                                class="ghost mini"
                                                title="Mute this entry"
                                                @click="
                                                    ignoreItem(
                                                        {
                                                            kind: 'pending',
                                                            ref: {
                                                                sync_sub:
                                                                    g.sync_sub,
                                                                folder: g.folder,
                                                                season: e.season,
                                                                episode:
                                                                    e.episode,
                                                            },
                                                        },
                                                        `${g.title} S${e.season}E${e.episode}`
                                                    )
                                                "
                                            >
                                                Ignore
                                            </button>
                                        </li>
                                    </ul>
                                </li>
                            </ul>
                        </template>
                    </li>
                </ul>
            </section>

            <section class="panel">
                <header>
                    <h2>Hanging files</h2>
                    <span class="dim"
                        >{{ hanging.length }} file{{
                            hanging.length === 1 ? "" : "s"
                        }}
                        · {{ humanSize(totalHangingBytes()) }}</span
                    >
                    <span class="spacer"></span>
                    <button
                        v-if="hanging.length > 0"
                        class="danger"
                        :disabled="removing"
                        @click="removeAllHanging"
                    >
                        Remove all
                    </button>
                </header>
                <p v-if="hanging.length === 0" class="empty">
                    No hanging files — every synced item has a source.
                </p>
                <ul v-else>
                    <li v-for="h in hanging" :key="h.path">
                        <span class="name mono">{{ h.rel }}</span>
                        <span class="spacer"></span>
                        <span class="size dim mono">{{
                            humanSize(h.size_bytes)
                        }}</span>
                        <button
                            class="danger mini"
                            :disabled="removing"
                            @click="removePaths([h.path], h.rel)"
                        >
                            Remove
                        </button>
                        <button
                            class="ghost mini"
                            title="Mute this entry"
                            @click="
                                ignoreItem(
                                    { kind: 'hanging', ref: { path: h.path } },
                                    h.rel
                                )
                            "
                        >
                            Ignore
                        </button>
                    </li>
                </ul>
            </section>

            <section
                v-if="ignoredCount > 0"
                class="panel"
                data-testid="ignored-section"
            >
                <header>
                    <h2>
                        <button
                            class="toggle"
                            :aria-expanded="ignoredOpen"
                            @click="ignoredOpen = !ignoredOpen"
                        >
                            <span class="caret">{{
                                ignoredOpen ? "▾" : "▸"
                            }}</span>
                            Ignored
                        </button>
                    </h2>
                    <span class="dim">{{ ignoredCount }} muted</span>
                </header>
                <div v-if="ignoredOpen">
                    <ul v-if="ignoredList.pending.length">
                        <li
                            v-for="p in ignoredList.pending"
                            :key="`p-${p.sync_sub}-${p.folder}-${p.season}-${p.episode}`"
                        >
                            <span class="kind-tag dim">pending</span>
                            <span class="name">
                                {{ p.folder }}
                                <span
                                    v-if="
                                        p.season !== null && p.episode !== null
                                    "
                                    class="dim mono"
                                >
                                    S{{ String(p.season).padStart(2, "0") }}E{{
                                        String(p.episode).padStart(2, "0")
                                    }}
                                </span>
                            </span>
                            <span class="spacer"></span>
                            <button
                                class="ghost mini"
                                @click="
                                    unignoreItem(
                                        { kind: 'pending', ref: p },
                                        p.folder
                                    )
                                "
                            >
                                Unignore
                            </button>
                        </li>
                    </ul>
                    <ul v-if="ignoredList.watched.length">
                        <li
                            v-for="w in ignoredList.watched"
                            :key="`w-${w.lib}-${w.folder}`"
                        >
                            <span class="kind-tag dim">watched</span>
                            <span class="name">{{ w.folder }}</span>
                            <span class="lib dim">{{ w.lib }}</span>
                            <span class="spacer"></span>
                            <button
                                class="ghost mini"
                                @click="
                                    unignoreItem(
                                        { kind: 'watched', ref: w },
                                        w.folder
                                    )
                                "
                            >
                                Unignore
                            </button>
                        </li>
                    </ul>
                    <ul v-if="ignoredList.hanging.length">
                        <li
                            v-for="h in ignoredList.hanging"
                            :key="`h-${h.path}`"
                        >
                            <span class="kind-tag dim">hanging</span>
                            <span class="name mono">{{ h.path }}</span>
                            <span class="spacer"></span>
                            <button
                                class="ghost mini"
                                @click="
                                    unignoreItem(
                                        { kind: 'hanging', ref: h },
                                        h.path
                                    )
                                "
                            >
                                Unignore
                            </button>
                        </li>
                    </ul>
                </div>
            </section>
        </template>
    </div>
</template>

<style scoped>
.view {
    padding: 1rem;
    overflow-y: auto;
    flex: 1;
    max-width: 1100px;
    margin: 0 auto;
    width: 100%;
}
.info {
    padding: 3rem 1rem;
    text-align: center;
    color: var(--fg-muted);
}
.info.err {
    color: var(--danger);
}

.panel {
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 1rem;
}
.panel header {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border);
}
.panel h2 {
    margin: 0;
    font-size: 1rem;
    font-weight: 600;
}

.empty {
    padding: 1.2rem 1rem;
    color: var(--fg-muted);
    margin: 0;
}
ul {
    list-style: none;
    padding: 0;
    margin: 0;
}
li {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    padding: 0.55rem 1rem;
    border-bottom: 1px solid var(--border);
    font-size: 0.88rem;
}
li:last-child {
    border-bottom: none;
}
.name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
}
.size,
.count {
    font-size: 0.78rem;
}
.mini {
    padding: 0.25rem 0.55rem;
    font-size: 0.78rem;
}
.spacer {
    flex: 1;
}

@media (max-width: 600px) {
    li {
        flex-wrap: wrap;
    }
    .name {
        flex: 1 1 100%;
    }
}

/* ── Pending-deletions pane ──────────────────────────────────────────── */

.pending-list {
    display: flex;
    flex-direction: column;
}
.pending-list > li.group {
    display: block;
    padding: 0;
    border-bottom: 1px solid var(--border);
}
.pending-list > li.group:last-child {
    border-bottom: none;
}

.row {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    padding: 0.55rem 1rem;
    font-size: 0.88rem;
}
.row.show-header {
    font-weight: 500;
}
.row.season-header {
    padding-left: 2rem;
    background: var(--bg-elev);
}
.row.episode {
    padding-left: 3rem;
    font-size: 0.83rem;
}

.toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    background: none;
    border: none;
    color: inherit;
    cursor: pointer;
    padding: 0;
    font: inherit;
    text-align: left;
}
.caret {
    width: 0.9rem;
    display: inline-block;
    color: var(--fg-muted);
}

.seasons,
.episodes {
    list-style: none;
    padding: 0;
    margin: 0;
}
.seasons > li.season {
    border-top: 1px solid var(--border);
}

.badge {
    font-size: 0.72rem;
    padding: 0.1rem 0.45rem;
    border-radius: 999px;
    background: var(--bg);
    border: 1px solid var(--border);
}

.ep-code {
    font-size: 0.78rem;
}
.lib {
    font-size: 0.78rem;
}
.season-label {
    font-weight: 500;
}

button.primary {
    background: var(--accent, #2563eb);
    color: white;
    border: 1px solid var(--accent, #2563eb);
    border-radius: 4px;
    cursor: pointer;
}
button.primary:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.kind-tag {
    font-size: 0.7rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding: 1px 6px;
    border: 1px solid var(--border);
    border-radius: 4px;
    font-weight: 600;
}
</style>
