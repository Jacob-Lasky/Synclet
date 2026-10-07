<script setup lang="ts">
import {
    computed,
    onActivated,
    onDeactivated,
    onMounted,
    onUnmounted,
    ref,
    watch,
} from "vue"
import { api } from "../api"
import type { SyncedEntry } from "../types"
import {
    humanSize,
    libraryLabel,
    openDetail,
    pushToast,
    store,
    trackJob,
} from "../store"

type SortKey = "name" | "recent" | "size"

const SORTS: { id: SortKey; label: string }[] = [
    { id: "name", label: "Name" },
    { id: "recent", label: "Recent" },
    { id: "size", label: "Size" },
]

// Session-local view state. SyncedView lives inside the tab-group KeepAlive, so
// these survive tab switches (the user's sort/filter choice sticks) and reset
// only on a full reload. Default sort is Name A-Z.
const sortKey = ref<SortKey>("name")
const libFilter = ref<Set<string>>(new Set()) // empty = all libraries

const items = ref<SyncedEntry[]>([])
const loading = ref(true)
// Whether the backend's new-episode enrichment has landed. The synced list
// paints immediately regardless; while this is false we re-poll so the
// "+N new" badges fill in without a manual refresh.
const enriched = ref(true)
const error = ref("")
const submitting = ref<Record<string, boolean>>({})

// How long to wait between silent re-polls while enrichment is pending. The
// backend rebuilds dirty caches every few seconds; 4s comfortably clears it.
const ENRICH_POLL_MS = 4000
// Source folders can move outside Synclet. Their followed status is checked
// by a background cache, so an open tab must refresh after that check lands.
const LIST_REFRESH_MS = 60_000
let enrichTimer: ReturnType<typeof setTimeout> | undefined
let refreshTimer: ReturnType<typeof setInterval> | undefined
let hasActivated = false
let lastFetchAt = 0
let active = true

async function fetchSynced(showSpinner: boolean): Promise<void> {
    if (showSpinner) loading.value = true
    error.value = ""
    try {
        const r = await api.synced()
        items.value = r.items
        lastFetchAt = Date.now()
        store.syncedListCount = r.items.length
        enriched.value = r.enriched
        scheduleEnrichPoll()
    } catch (e) {
        error.value = (e as Error).message
    } finally {
        if (showSpinner) loading.value = false
    }
}

function scheduleEnrichPoll(): void {
    clearTimeout(enrichTimer)
    // Only poll while the badges are still missing; once enriched, stop so we
    // are not hammering the API for a steady-state tab.
    if (active && !enriched.value) {
        enrichTimer = setTimeout(() => fetchSynced(false), ENRICH_POLL_MS)
    }
}

function load(): Promise<void> {
    return fetchSynced(true)
}

function startPeriodicRefresh(): void {
    if (refreshTimer) return
    refreshTimer = setInterval(() => void fetchSynced(false), LIST_REFRESH_MS)
}

function stopPolling(): void {
    clearTimeout(enrichTimer)
    clearInterval(refreshTimer)
    refreshTimer = undefined
}

onMounted(() => {
    void load()
    startPeriodicRefresh()
})
onActivated(() => {
    active = true
    startPeriodicRefresh()
    if (hasActivated && Date.now() - lastFetchAt >= LIST_REFRESH_MS)
        void fetchSynced(false)
    hasActivated = true
})
onDeactivated(() => {
    active = false
    stopPolling()
})
watch(
    () => store.syncedVersion,
    () => void fetchSynced(false)
)
onUnmounted(stopPolling)

async function syncNew(entry: SyncedEntry, n: number): Promise<void> {
    if (!entry.lib) return
    const eps = entry.new_unwatched
        .slice(0, n)
        .map<[number, number]>((e) => [e.season, e.episode])
    submitting.value[entry.folder] = true
    try {
        const r = await api.sync({
            lib: entry.lib,
            folder: entry.folder,
            selection_type: "episodes",
            episodes: eps,
        })
        if (r.job_id) {
            trackJob(r.job_id, {
                action: "Sync",
                name: entry.title,
                totalMediaFiles: r.total_media_files,
            })
            setTimeout(load, 1500)
        }
    } finally {
        submitting.value[entry.folder] = false
    }
}

function newBytes(entry: SyncedEntry, n: number): number {
    return entry.new_unwatched.slice(0, n).reduce((s, e) => s + e.size_bytes, 0)
}

// Episodic titles lead with their downloaded-episode count, e.g.
// "8 episodes (2.6 GB)"; movies (one file) just show the size.
function sizeLabel(entry: SyncedEntry): string {
    if (entry.followed && entry.synced_episodes === 0) {
        return "Waiting for new episodes"
    }
    const size = humanSize(entry.size_bytes)
    const episodic = entry.kind === "show" || entry.kind === "youtube"
    if (episodic && entry.synced_episodes > 0) {
        const n = entry.synced_episodes
        return `${n} episode${n === 1 ? "" : "s"} (${size})`
    }
    return size
}

async function toggleFollowing(entry: SyncedEntry): Promise<void> {
    if (!entry.lib) return
    submitting.value[entry.folder] = true
    try {
        const r = await api.follow(entry.lib, entry.folder, !entry.followed)
        if (!r.following && entry.synced_episodes === 0) {
            items.value = items.value.filter(
                (it) => !(it.lib === entry.lib && it.folder === entry.folder)
            )
            store.syncedListCount = items.value.length
        } else {
            entry.followed = r.following
        }
        pushToast({
            kind: "success",
            text: r.following
                ? `Following ${entry.title} for new episodes`
                : `Stopped following ${entry.title}`,
        })
    } catch (e) {
        pushToast({ kind: "error", text: (e as Error).message })
    } finally {
        submitting.value[entry.folder] = false
    }
}

// Library filter pills, one per library that actually has synced items. Order
// follows the canonical library order from the state bundle (store.libraries);
// libraries not yet in the store fall to the end. Stray folders with no source
// library (lib === null) are grouped under no pill and always shown.
const libs = computed(() => {
    const counts = new Map<string, number>()
    for (const it of items.value) {
        if (it.lib) counts.set(it.lib, (counts.get(it.lib) ?? 0) + 1)
    }
    const order = store.libraries.map((l) => l.id)
    const rank = (id: string) => {
        const i = order.indexOf(id)
        return i === -1 ? order.length : i
    }
    return [...counts.entries()]
        .map(([id, count]) => ({ id, label: libraryLabel(id), count }))
        .sort((a, b) => rank(a.id) - rank(b.id))
})
const offlineCount = computed(
    () => items.value.filter((item) => item.size_bytes > 0).length
)
const waitingCount = computed(
    () =>
        items.value.filter((item) => item.followed && item.size_bytes === 0)
            .length
)

function toggleLib(id: string): void {
    // Reassign (not mutate) so the computed dependency tracks the change.
    const next = new Set(libFilter.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    libFilter.value = next
}

// The rendered list: library-filtered (empty selection = all), then sorted by
// the active key. Name is A-Z case-insensitive; Recent is newest-synced first;
// Size is largest first. Sorting is stable (Array.prototype.sort) so ties keep
// the backend's sub-major, name-sorted order.
const visibleItems = computed<SyncedEntry[]>(() => {
    const sel = libFilter.value
    const list = items.value.filter(
        (it) => sel.size === 0 || (it.lib !== null && sel.has(it.lib))
    )
    const sorted = [...list]
    if (sortKey.value === "name") {
        sorted.sort((a, b) =>
            a.title.localeCompare(b.title, undefined, { sensitivity: "base" })
        )
    } else if (sortKey.value === "recent") {
        sorted.sort((a, b) => b.mtime - a.mtime)
    } else {
        sorted.sort((a, b) => b.size_bytes - a.size_bytes)
    }
    return sorted
})

async function unsyncTitle(entry: SyncedEntry): Promise<void> {
    if (!entry.lib) return
    // Destructive: removes the title from synced-media and Syncthing propagates
    // the deletion to paired devices. Guard with an explicit confirm.
    if (
        !confirm(
            `Unsync (delete) all of ${entry.title}?\n\n` +
                `This removes its files from synced-media (${humanSize(entry.size_bytes)}) ` +
                `and Syncthing will propagate the deletion to your other devices. ` +
                `The source library is untouched.`
        )
    ) {
        return
    }
    submitting.value[entry.folder] = true
    try {
        const r = await api.unsync({
            lib: entry.lib,
            folder: entry.folder,
            selection_type: "all",
        })
        if (r.job_id) {
            trackJob(r.job_id, {
                action: "Unsync",
                name: entry.title,
                totalMediaFiles: r.total_media_files,
            })
            setTimeout(load, 1500)
        }
    } finally {
        submitting.value[entry.folder] = false
    }
}
</script>

<template>
    <div class="view fade-in">
        <div v-if="loading" class="info">Loading synced library…</div>
        <div v-else-if="error" class="info err">{{ error }}</div>
        <template v-else>
            <div class="view-heading">
                <div>
                    <p class="eyebrow">Your collection</p>
                    <h1>Offline & following</h1>
                    <p class="subtitle">
                        Keep media close and catch the next episode.
                    </p>
                </div>
                <div class="summary" aria-label="Collection summary">
                    <span
                        ><strong>{{ offlineCount }}</strong> offline</span
                    >
                    <span
                        ><strong>{{ waitingCount }}</strong> waiting</span
                    >
                </div>
            </div>
            <div v-if="items.length === 0" class="info">
                <p>No offline or followed titles yet.</p>
                <p class="dim">Pick a title in the Library to start.</p>
            </div>

            <div v-else class="list">
                <div class="toolbar" data-testid="synced-toolbar">
                    <div class="sort" role="group" aria-label="Sort">
                        <button
                            v-for="s in SORTS"
                            :key="s.id"
                            :class="['seg', { on: sortKey === s.id }]"
                            :data-testid="`sort-${s.id}`"
                            @click="sortKey = s.id"
                        >
                            {{ s.label }}
                        </button>
                    </div>
                    <div v-if="libs.length > 1" class="chips">
                        <button
                            v-for="l in libs"
                            :key="l.id"
                            :class="['chip', { on: libFilter.has(l.id) }]"
                            :data-testid="`lib-${l.id}`"
                            @click="toggleLib(l.id)"
                        >
                            {{ l.label }}
                            <span class="chip-count">{{ l.count }}</span>
                        </button>
                    </div>
                </div>
                <div
                    v-if="!enriched"
                    class="enriching dim"
                    data-testid="synced-enriching"
                >
                    Checking for new episodes…
                </div>
                <div
                    v-for="item in visibleItems"
                    :key="`${item.lib}/${item.folder}`"
                    class="row"
                    :class="{ waiting: item.followed && item.size_bytes === 0 }"
                >
                    <div class="thumb-wrap">
                        <img
                            v-if="item.lib"
                            :src="api.thumbUrl(item.lib, item.folder)"
                            :alt="item.title"
                            loading="lazy"
                            @error="
                                (
                                    $event.target as HTMLImageElement
                                ).style.display = 'none'
                            "
                        />
                    </div>
                    <div
                        class="meta-col"
                        @click="item.lib && openDetail(item.lib, item.folder)"
                    >
                        <div class="title-line">
                            <span class="title">{{ item.title }}</span>
                            <span v-if="item.lib" class="lib-tag dim">{{
                                libraryLabel(item.lib)
                            }}</span>
                        </div>
                        <div class="size-line">
                            <span>{{ sizeLabel(item) }}</span>
                            <span v-if="item.followed" class="followed-tag">
                                Following
                            </span>
                            <span
                                v-if="item.new_unwatched.length > 0"
                                class="new"
                            >
                                +{{ item.new_unwatched.length }} new
                            </span>
                        </div>
                    </div>
                    <div class="actions">
                        <button
                            v-if="item.new_unwatched.length > 0"
                            class="primary"
                            :disabled="submitting[item.folder]"
                            @click="
                                syncNew(
                                    item,
                                    Math.min(5, item.new_unwatched.length)
                                )
                            "
                        >
                            Sync next
                            {{ Math.min(5, item.new_unwatched.length) }}
                            <span class="dim small"
                                >({{ humanSize(newBytes(item, 5)) }})</span
                            >
                        </button>
                        <button
                            v-if="item.new_unwatched.length > 5"
                            :disabled="submitting[item.folder]"
                            @click="syncNew(item, item.new_unwatched.length)"
                        >
                            Sync all {{ item.new_unwatched.length }}
                        </button>
                        <button
                            v-if="item.lib && item.size_bytes > 0"
                            class="danger-outline"
                            data-testid="unsync-title"
                            :disabled="submitting[item.folder]"
                            @click="unsyncTitle(item)"
                        >
                            Unsync
                        </button>
                        <button
                            v-if="item.lib && item.kind !== 'movie'"
                            class="ghost"
                            data-testid="follow-title"
                            :disabled="submitting[item.folder]"
                            @click="toggleFollowing(item)"
                        >
                            {{ item.followed ? "Stop following" : "Follow" }}
                        </button>
                    </div>
                </div>
            </div>
        </template>
    </div>
</template>

<style scoped>
.view {
    padding: clamp(1rem, 2.5vw, 2rem);
    overflow-y: auto;
    flex: 1;
}
.view-heading {
    max-width: 1020px;
    margin: 0 auto 1.4rem;
    display: flex;
    align-items: end;
    justify-content: space-between;
    gap: 1rem;
}
.eyebrow {
    margin: 0 0 0.25rem;
    color: var(--accent-sync);
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.12em;
}
.view-heading h1 {
    margin: 0;
    font-size: clamp(1.55rem, 3vw, 2.2rem);
    line-height: 1.1;
    letter-spacing: -0.035em;
}
.subtitle {
    margin: 0.45rem 0 0;
    color: var(--fg-muted);
    font-size: 0.9rem;
}
.summary {
    display: flex;
    gap: 1rem;
    color: var(--fg-muted);
    font-size: 0.83rem;
    white-space: nowrap;
}
.summary strong {
    color: var(--fg);
    font-size: 1rem;
}
.info {
    padding: 3rem 1rem;
    text-align: center;
    color: var(--fg-muted);
}
.info.err {
    color: var(--danger);
}

.list {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    max-width: 1020px;
    margin: 0 auto;
}
.toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem 1rem;
    margin-bottom: 0.2rem;
}
.sort {
    display: inline-flex;
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 2px;
}
.sort .seg {
    padding: 0.28rem 0.75rem;
    font-size: 0.8rem;
    border-radius: 999px;
    color: var(--fg-muted);
    background: transparent;
    border: none;
}
.sort .seg:hover {
    color: var(--fg);
}
.sort .seg.on {
    background: var(--bg-elev-2);
    color: var(--fg);
}
.chips {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.35rem;
}
/* Base .chip pill lives in style.css (shared with FilterBar); only the
 * per-item count badge is scoped here. */
.chip-count {
    font-size: 0.72rem;
    color: var(--fg-dim);
}
.chip.on .chip-count {
    color: var(--fg-muted);
}
.row {
    display: flex;
    gap: 0.8rem;
    align-items: center;
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 0.85rem;
}
.row.waiting {
    border-color: rgba(75, 214, 203, 0.38);
    background: linear-gradient(
        90deg,
        rgba(75, 214, 203, 0.07),
        var(--bg-elev) 28%
    );
}
.thumb-wrap {
    width: 58px;
    aspect-ratio: 2 / 3;
    background: var(--bg-elev-2);
    border-radius: 7px;
    overflow: hidden;
    flex-shrink: 0;
}
.thumb-wrap img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}
.meta-col {
    flex: 1;
    min-width: 0;
    cursor: pointer;
}
.title-line {
    display: flex;
    gap: 0.5rem;
    align-items: baseline;
    overflow: hidden;
    white-space: nowrap;
}
.title {
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
}
.lib-tag {
    font-size: 0.75rem;
}
.size-line {
    display: flex;
    gap: 0.7rem;
    align-items: center;
    font-size: 0.83rem;
    color: var(--fg-muted);
}
.new {
    color: var(--accent-progress);
    font-weight: 600;
}
.followed-tag {
    color: var(--accent-sync);
    font-weight: 600;
}
.enriching {
    font-size: 0.8rem;
    text-align: center;
    padding: 0.2rem 0 0.4rem;
}
.actions {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
    justify-content: flex-end;
}
.actions .small {
    font-size: 0.75rem;
    font-weight: 400;
    margin-left: 4px;
}

@media (max-width: 600px) {
    .view {
        padding: 0.9rem;
    }
    .view-heading {
        align-items: start;
        flex-direction: column;
        gap: 0.65rem;
    }
    .subtitle {
        font-size: 0.82rem;
    }
    .summary {
        gap: 0.8rem;
    }
    .row {
        flex-wrap: wrap;
    }
    .actions {
        flex: 1 1 100%;
    }
    .actions button {
        flex: 1;
    }
}
</style>
