<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import { api } from "../api"
import type { Episode, TitleDetail } from "../types"
import {
    closeDetail,
    humanSize,
    isScrobbledThisSession,
    isUnwatchedThisSession,
    pushToast,
    recordScrobbled,
    recordUnwatched,
    store,
    trackJob,
} from "../store"
import EpisodeTile from "./EpisodeTile.vue"

// How long after a sync/unsync job kicks off before we re-fetch the title
// detail to surface updated badges. Unsync feels instant (file delete) so
// we refresh sooner; copy operations need more time before the first file
// shows up in synced-media.
const SYNC_REFRESH_DELAY_MS = 1500
const UNSYNC_REFRESH_DELAY_MS = 1000

const detail = ref<TitleDetail | null>(null)
const loading = ref(false)
const error = ref("")
const selected = ref<Set<string>>(new Set()) // "S-E"
const lastClick = ref<{ s: number; e: number } | null>(null)
const expandedSeasons = ref<Set<number>>(new Set())
const submitting = ref(false)

function key(s: number, e: number): string {
    return `${s}-${e}`
}

// Inverse of key(). Keys always come from key() above, so the segments must
// parse as two numbers. Throws on malformed input — silent fallback would
// hide a real bug as "season 0 episode 0" syncs.
function parseKey(k: string): [number, number] {
    const [s, e] = k.split("-").map(Number)
    if (
        s === undefined ||
        e === undefined ||
        Number.isNaN(s) ||
        Number.isNaN(e)
    ) {
        throw new Error(`parseKey: malformed selection key "${k}"`)
    }
    return [s, e]
}

// Apply the session watch-state overlay to fresh title-detail data. Plex's
// scrobble/unscrobble lands instantly but the WatchState daemon polls Plex on
// a slow schedule (minutes), so api.title() can return the stale pre-gesture
// state seconds after we successfully changed it. Without this overlay, the UI
// would flicker as refreshInPlace fires. Both directions are applied; the
// store keeps the two overlays mutually exclusive, so per item at most one of
// the two checks below is true.
function applyScrobbleOverlay(d: TitleDetail): TitleDetail {
    if (d.kind === "movie") {
        if (isUnwatchedThisSession(d.lib, d.folder)) d.watched = false
        else if (isScrobbledThisSession(d.lib, d.folder)) d.watched = true
        return d
    }
    for (const s of d.seasons) {
        for (const e of s.episodes) {
            if (isUnwatchedThisSession(d.lib, d.folder, e.season, e.episode)) {
                e.watch_state = "unwatched"
                e.watch_pct = 0
            } else if (
                isScrobbledThisSession(d.lib, d.folder, e.season, e.episode)
            ) {
                e.watch_state = "watched"
                e.watch_pct = 100
            }
        }
        // Recompute from the (possibly overlaid) episode states so the season
        // count stays consistent whichever direction the overlay pushed.
        s.watched_episodes = s.episodes.filter(
            (e) => e.watch_state === "watched"
        ).length
    }
    return d
}

async function load(lib: string, folder: string): Promise<void> {
    loading.value = true
    error.value = ""
    detail.value = null
    selected.value = new Set()
    lastClick.value = null
    expandedSeasons.value = new Set()
    try {
        const d = applyScrobbleOverlay(await api.title(lib, folder))
        detail.value = d
        // Expand the first season with unsynced or unwatched eps; otherwise
        // season 1. noUncheckedIndexedAccess means d.seasons[0] is T|undefined,
        // so capture it once and let the ?? collapse to a defined value.
        if (d.kind !== "movie") {
            const first = d.seasons[0]
            if (first) {
                const seed =
                    d.seasons.find(
                        (s) => s.synced_episodes < s.episodes.length
                    ) ?? first
                expandedSeasons.value.add(seed.season)
            }
        }
    } catch (err) {
        error.value = (err as Error).message
    } finally {
        loading.value = false
    }
}

// Pull fresh data for the SAME title without touching UI state. Used after a
// sync/unsync job to refresh per-episode is_synced flags. If the user has
// already navigated to a different title, this is a no-op — we don't want to
// stomp the new title's state.
async function refreshInPlace(lib: string, folder: string): Promise<void> {
    if (
        !detail.value ||
        detail.value.lib !== lib ||
        detail.value.folder !== folder
    ) {
        return
    }
    try {
        const d = applyScrobbleOverlay(await api.title(lib, folder))
        if (
            !detail.value ||
            detail.value.lib !== lib ||
            detail.value.folder !== folder
        ) {
            return // user navigated during the fetch
        }
        // Merge in-place: only mutate fields that can change as a result of sync ops.
        detail.value.total_bytes = d.total_bytes
        detail.value.synced_bytes = d.synced_bytes
        detail.value.files = d.files
        if (d.kind !== "movie") {
            // Build a lookup by season number for quick patching
            const next: Record<number, (typeof d.seasons)[number]> = {}
            for (const s of d.seasons) next[s.season] = s
            for (const s of detail.value.seasons) {
                const fresh = next[s.season]
                if (!fresh) continue
                s.synced_episodes = fresh.synced_episodes
                s.watched_episodes = fresh.watched_episodes
                for (const e of s.episodes) {
                    const freshEp = fresh.episodes.find(
                        (x) => x.season === e.season && x.episode === e.episode
                    )
                    if (freshEp) {
                        e.is_synced = freshEp.is_synced
                        e.watch_state = freshEp.watch_state
                        e.watch_pct = freshEp.watch_pct
                    }
                }
            }
        } else {
            detail.value.watched = d.watched
        }
    } catch {
        // Silent — user's in-flight interaction takes priority.
    }
}

onMounted(() => {
    if (store.detail) load(store.detail.lib, store.detail.folder)
})

watch(
    () => store.detail,
    (v) => {
        if (v) load(v.lib, v.folder)
    }
)

function flatEpisodes(): Episode[] {
    return detail.value?.seasons.flatMap((s) => s.episodes) ?? []
}

function toggle(s: number, e: number, shift: boolean): void {
    const k = key(s, e)
    const last = lastClick.value
    if (shift && last) {
        // Range select from lastClick to (s,e). Local const so the closures
        // below can narrow without a non-null assertion.
        const eps = flatEpisodes()
        const i1 = eps.findIndex(
            (x) => x.season === last.s && x.episode === last.e
        )
        const i2 = eps.findIndex((x) => x.season === s && x.episode === e)
        if (i1 >= 0 && i2 >= 0) {
            const [a, b] = i1 < i2 ? [i1, i2] : [i2, i1]
            const isOn = selected.value.has(k)
            for (let i = a; i <= b; i++) {
                const ep = eps[i]
                if (!ep) continue
                if (isOn) selected.value.delete(key(ep.season, ep.episode))
                else selected.value.add(key(ep.season, ep.episode))
            }
        }
    } else {
        if (selected.value.has(k)) selected.value.delete(k)
        else selected.value.add(k)
    }
    lastClick.value = { s, e }
    // Reactivity nudge
    selected.value = new Set(selected.value)
}

function selectAll(): void {
    const eps = flatEpisodes()
    if (selected.value.size === eps.length) selected.value = new Set()
    else selected.value = new Set(eps.map((e) => key(e.season, e.episode)))
}

function selectUnwatchedUnsynced(): void {
    const eps = flatEpisodes().filter(
        (e) => e.watch_state !== "watched" && !e.is_synced
    )
    selected.value = new Set(eps.map((e) => key(e.season, e.episode)))
    if (eps[0]) expandedSeasons.value.add(eps[0].season)
}

function selectSeason(s: number): void {
    if (!detail.value) return
    const season = detail.value.seasons.find((x) => x.season === s)
    if (!season) return
    const keys = season.episodes.map((e) => key(e.season, e.episode))
    const allOn = keys.every((k) => selected.value.has(k))
    if (allOn) for (const k of keys) selected.value.delete(k)
    else for (const k of keys) selected.value.add(k)
    selected.value = new Set(selected.value)
}

const selectionStats = computed(() => {
    if (!detail.value)
        return { count: 0, bytes: 0, syncBytes: 0, unsyncCount: 0 }
    let bytes = 0
    let syncBytes = 0 // bytes to copy (not yet synced)
    let unsyncCount = 0 // count synced (eligible for unsync)
    for (const s of detail.value.seasons) {
        for (const e of s.episodes) {
            if (selected.value.has(key(e.season, e.episode))) {
                bytes += e.size_bytes
                if (!e.is_synced) syncBytes += e.size_bytes
                else unsyncCount++
            }
        }
    }
    return { count: selected.value.size, bytes, syncBytes, unsyncCount }
})

async function doSync(): Promise<void> {
    if (!detail.value || selected.value.size === 0) return
    submitting.value = true
    try {
        const eps: [number, number][] = Array.from(selected.value).map(parseKey)
        const r = await api.sync({
            lib: detail.value.lib,
            folder: detail.value.folder,
            selection_type: "episodes",
            episodes: eps,
        })
        if (r.job_id) {
            const lib = detail.value.lib
            const folder = detail.value.folder
            trackJob(r.job_id, {
                action: "Sync",
                name: detail.value.name,
                totalMediaFiles: r.total_media_files,
            })
            // In-place refresh after a short delay so the badges reflect new sync
            // state without resetting expansion / selection / scroll. Gated on the
            // user still viewing the same title (see refreshInPlace).
            setTimeout(() => refreshInPlace(lib, folder), SYNC_REFRESH_DELAY_MS)
            selected.value = new Set()
        }
    } finally {
        submitting.value = false
    }
}

async function doUnsync(): Promise<void> {
    if (!detail.value || selected.value.size === 0) return
    submitting.value = true
    try {
        const eps: [number, number][] = Array.from(selected.value).map(parseKey)
        const r = await api.unsync({
            lib: detail.value.lib,
            folder: detail.value.folder,
            selection_type: "episodes",
            episodes: eps,
        })
        if (r.job_id) {
            const lib = detail.value.lib
            const folder = detail.value.folder
            trackJob(r.job_id, {
                action: "Unsync",
                name: detail.value.name,
                totalMediaFiles: r.total_media_files,
            })
            setTimeout(
                () => refreshInPlace(lib, folder),
                UNSYNC_REFRESH_DELAY_MS
            )
            selected.value = new Set()
        }
    } finally {
        submitting.value = false
    }
}

async function syncMovie(): Promise<void> {
    if (!detail.value) return
    submitting.value = true
    try {
        const r = await api.sync({
            lib: detail.value.lib,
            folder: detail.value.folder,
            selection_type: "movie",
        })
        if (r.job_id) {
            const lib = detail.value.lib
            const folder = detail.value.folder
            trackJob(r.job_id, {
                action: "Sync",
                name: detail.value.name,
                totalMediaFiles: r.total_media_files,
            })
            setTimeout(() => refreshInPlace(lib, folder), SYNC_REFRESH_DELAY_MS)
        }
    } finally {
        submitting.value = false
    }
}

async function unsyncMovie(): Promise<void> {
    if (!detail.value) return
    submitting.value = true
    try {
        const r = await api.unsync({
            lib: detail.value.lib,
            folder: detail.value.folder,
            selection_type: "movie",
        })
        if (r.job_id) {
            const lib = detail.value.lib
            const folder = detail.value.folder
            trackJob(r.job_id, {
                action: "Unsync",
                name: detail.value.name,
                totalMediaFiles: r.total_media_files,
            })
            setTimeout(
                () => refreshInPlace(lib, folder),
                UNSYNC_REFRESH_DELAY_MS
            )
        }
    } finally {
        submitting.value = false
    }
}

function toggleSeasonExpand(s: number): void {
    if (expandedSeasons.value.has(s)) expandedSeasons.value.delete(s)
    else expandedSeasons.value.add(s)
}

// ── Set watch state (explicit gesture, no file deletion) ───────────────────

async function setWatched(
    scope: "movie" | "series" | "season" | "episode",
    watched: boolean,
    season?: number,
    episode?: number,
    confirmLabel?: string
): Promise<void> {
    if (!detail.value) return
    const verb = watched ? "Mark watched" : "Mark unwatched"
    if (confirmLabel) {
        if (!confirm(`${verb}: ${confirmLabel}?`)) return
    }
    submitting.value = true
    try {
        const r = await api.scrobble({
            lib: detail.value.lib,
            folder: detail.value.folder,
            scope,
            season,
            episode,
            watched,
        })
        // Record successful gestures in the session overlay, then funnel the
        // optimistic update through the same applyScrobbleOverlay helper that
        // refreshInPlace uses. One code path = no drift between "fresh-mark
        // optimistic update" and "post-refresh re-apply." record() picks the
        // right overlay; the store keeps the two mutually exclusive.
        const record = watched ? recordScrobbled : recordUnwatched
        if (detail.value.kind === "movie") {
            if (r.scrobbled > 0) {
                record(detail.value.lib, detail.value.folder)
            }
        } else {
            for (const it of r.results) {
                if (
                    it.status === "ok" &&
                    it.season !== null &&
                    it.episode !== null
                ) {
                    record(
                        detail.value.lib,
                        detail.value.folder,
                        it.season,
                        it.episode
                    )
                }
            }
        }
        applyScrobbleOverlay(detail.value)
        const past = watched ? "watched" : "unwatched"
        if (r.error) {
            pushToast({ kind: "error", text: r.error })
        } else if (r.failed > 0) {
            pushToast({
                kind: "error",
                text: `Marked ${r.scrobbled} ${past}, ${r.failed} failed`,
            })
        } else {
            pushToast({
                kind: "success",
                text: `Marked ${r.scrobbled} ${past}`,
            })
        }
        // Let WatchState catch up; this picks up any drift between optimistic and
        // canonical state without resetting the UI.
        const lib = detail.value.lib
        const folder = detail.value.folder
        setTimeout(() => refreshInPlace(lib, folder), UNSYNC_REFRESH_DELAY_MS)
    } catch (e) {
        pushToast({ kind: "error", text: (e as Error).message })
    } finally {
        submitting.value = false
    }
}

function onBackdropClick(e: MouseEvent): void {
    if ((e.target as HTMLElement).classList.contains("backdrop")) closeDetail()
}

const artUrl = computed(() =>
    detail.value ? api.artUrl(detail.value.lib, detail.value.folder) : ""
)
const movieSynced = computed(
    () => detail.value?.kind === "movie" && detail.value.synced_bytes > 0
)
</script>

<template>
    <div class="backdrop" @click="onBackdropClick">
        <aside class="drawer fade-in">
            <button class="close" aria-label="Close" @click="closeDetail">
                <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                >
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
            </button>

            <div v-if="loading" class="loading">Loading…</div>
            <div v-else-if="error" class="error">{{ error }}</div>

            <template v-else-if="detail">
                <div class="hero">
                    <img
                        class="art"
                        :src="artUrl"
                        alt=""
                        loading="lazy"
                        @error="
                            ($event.target as HTMLImageElement).style.display =
                                'none'
                        "
                    />
                    <div class="hero-overlay"></div>
                    <div class="hero-body">
                        <h1 class="title">{{ detail.name }}</h1>
                        <div class="meta">
                            <span v-if="detail.year">{{ detail.year }}</span>
                            <span class="dim">·</span>
                            <span class="dim">{{ detail.lib }}</span>
                            <span v-if="detail.kind !== 'movie'">
                                <span class="dim">·</span>
                                <span
                                    >{{ detail.seasons.length }} season{{
                                        detail.seasons.length === 1 ? "" : "s"
                                    }}</span
                                >
                            </span>
                            <span class="dim">·</span>
                            <span>{{ humanSize(detail.total_bytes) }}</span>
                            <span v-if="detail.synced_bytes > 0">
                                <span class="dim">·</span>
                                <span class="sync"
                                    >⬇
                                    {{ humanSize(detail.synced_bytes) }}
                                    synced</span
                                >
                            </span>
                        </div>
                    </div>
                </div>

                <!-- Movie -->
                <template v-if="detail.kind === 'movie'">
                    <div class="movie-pane">
                        <div class="movie-files">
                            <div
                                v-for="f in detail.files"
                                :key="f.path"
                                class="file-row"
                                :class="{ synced: f.is_synced }"
                            >
                                <span class="file-name mono">{{ f.name }}</span>
                                <span class="file-size dim">{{
                                    humanSize(f.size_bytes)
                                }}</span>
                                <span v-if="f.is_synced" class="file-tag sync"
                                    >⬇</span
                                >
                            </div>
                        </div>
                        <div class="movie-actions">
                            <button
                                v-if="!movieSynced"
                                class="primary"
                                :disabled="submitting"
                                @click="syncMovie"
                            >
                                Sync ({{ humanSize(detail.total_bytes) }})
                            </button>
                            <button
                                v-else
                                class="danger"
                                :disabled="submitting"
                                @click="unsyncMovie"
                            >
                                Unsync
                            </button>
                            <button
                                v-if="!detail.watched"
                                class="ghost"
                                :disabled="submitting"
                                data-testid="mark-movie-watched"
                                @click="
                                    setWatched(
                                        'movie',
                                        true,
                                        undefined,
                                        undefined,
                                        detail.name
                                    )
                                "
                            >
                                Mark watched
                            </button>
                            <button
                                v-else
                                class="ghost"
                                :disabled="submitting"
                                data-testid="mark-movie-unwatched"
                                @click="
                                    setWatched(
                                        'movie',
                                        false,
                                        undefined,
                                        undefined,
                                        detail.name
                                    )
                                "
                            >
                                Mark unwatched
                            </button>
                        </div>
                    </div>
                </template>

                <!-- Show / YouTube -->
                <template v-else>
                    <div class="actions-row">
                        <button class="ghost" @click="selectAll">
                            {{
                                selected.size === flatEpisodes().length &&
                                flatEpisodes().length > 0
                                    ? "Clear"
                                    : "Select all"
                            }}
                        </button>
                        <button class="ghost" @click="selectUnwatchedUnsynced">
                            Unwatched + unsynced
                        </button>
                        <span class="spacer"></span>
                        <button
                            class="ghost"
                            :disabled="submitting"
                            data-testid="mark-series-watched"
                            @click="
                                setWatched(
                                    'series',
                                    true,
                                    undefined,
                                    undefined,
                                    `entire series ${detail.name}`
                                )
                            "
                        >
                            Mark series watched
                        </button>
                        <button
                            class="ghost"
                            :disabled="submitting"
                            data-testid="mark-series-unwatched"
                            @click="
                                setWatched(
                                    'series',
                                    false,
                                    undefined,
                                    undefined,
                                    `entire series ${detail.name}`
                                )
                            "
                        >
                            Mark series unwatched
                        </button>
                    </div>

                    <div class="seasons">
                        <section
                            v-for="s in detail.seasons"
                            :key="s.season"
                            class="season"
                        >
                            <header
                                class="season-head"
                                @click="toggleSeasonExpand(s.season)"
                            >
                                <span
                                    class="chev"
                                    :class="{
                                        open: expandedSeasons.has(s.season),
                                    }"
                                    >▸</span
                                >
                                <strong>Season {{ s.season }}</strong>
                                <span class="dim"
                                    >{{ s.episodes.length }} ep</span
                                >
                                <span class="dim">·</span>
                                <span class="dim">{{
                                    humanSize(s.total_bytes)
                                }}</span>
                                <span
                                    v-if="s.watched_episodes"
                                    class="tag watched"
                                    >{{ s.watched_episodes }} ✓</span
                                >
                                <span v-if="s.synced_episodes" class="tag sync"
                                    >{{ s.synced_episodes }} ⬇</span
                                >
                                <span class="spacer"></span>
                                <button
                                    class="ghost mini"
                                    @click.stop="selectSeason(s.season)"
                                >
                                    select
                                </button>
                                <button
                                    class="ghost mini"
                                    :disabled="submitting"
                                    data-testid="mark-season-watched"
                                    @click.stop="
                                        setWatched(
                                            'season',
                                            true,
                                            s.season,
                                            undefined,
                                            `Season ${s.season}`
                                        )
                                    "
                                >
                                    ✓ season
                                </button>
                                <button
                                    class="ghost mini"
                                    :disabled="submitting"
                                    data-testid="mark-season-unwatched"
                                    @click.stop="
                                        setWatched(
                                            'season',
                                            false,
                                            s.season,
                                            undefined,
                                            `Season ${s.season}`
                                        )
                                    "
                                >
                                    ✗ season
                                </button>
                            </header>
                            <div
                                v-show="expandedSeasons.has(s.season)"
                                class="eps"
                            >
                                <EpisodeTile
                                    v-for="e in s.episodes"
                                    :key="e.season + '-' + e.episode"
                                    :ep="e"
                                    :selected="
                                        selected.has(key(e.season, e.episode))
                                    "
                                    @toggle="toggle"
                                    @mark-watched="
                                        (season, episode) =>
                                            setWatched(
                                                'episode',
                                                true,
                                                season,
                                                episode
                                            )
                                    "
                                    @mark-unwatched="
                                        (season, episode) =>
                                            setWatched(
                                                'episode',
                                                false,
                                                season,
                                                episode
                                            )
                                    "
                                />
                            </div>
                        </section>
                    </div>

                    <div v-if="selected.size > 0" class="action-bar fade-in">
                        <div class="selection-info">
                            <strong>{{ selected.size }}</strong> selected
                            <span class="dim"
                                >· {{ humanSize(selectionStats.bytes) }}</span
                            >
                        </div>
                        <div class="action-buttons">
                            <button
                                v-if="selectionStats.syncBytes > 0"
                                class="primary"
                                :disabled="submitting"
                                @click="doSync"
                            >
                                Sync ({{ humanSize(selectionStats.syncBytes) }})
                            </button>
                            <button
                                v-if="selectionStats.unsyncCount > 0"
                                class="danger"
                                :disabled="submitting"
                                @click="doUnsync"
                            >
                                Unsync {{ selectionStats.unsyncCount }}
                            </button>
                        </div>
                    </div>
                </template>
            </template>
        </aside>
    </div>
</template>

<style scoped>
.backdrop {
    position: fixed;
    inset: 0;
    z-index: 100;
    background: rgba(0, 0, 0, 0.55);
    display: flex;
    justify-content: flex-end;
    animation: fade-in 140ms ease-out;
}
.drawer {
    width: 540px;
    max-width: 100vw;
    /* dvh accounts for dynamic browser chrome (URL bar, etc.) where
     100vh would extend the scroll region beyond the visible viewport,
     leaving the bottom of long content unreachable. 100vh stays as the
     fallback for browsers without dvh support. */
    height: 100vh;
    height: 100dvh;
    /* flex-item with overflow:auto needs min-height:0 to size against its
     content correctly; without it, deeply-stacked content can render
     past the scroll plane and become unreachable. */
    min-height: 0;
    background: var(--bg);
    border-left: 1px solid var(--border);
    overflow-y: auto;
    position: relative;
    display: flex;
    flex-direction: column;
}
.close {
    position: absolute;
    top: 10px;
    right: 10px;
    z-index: 5;
    width: 36px;
    height: 36px;
    padding: 0;
    border-radius: 50%;
    background: rgba(0, 0, 0, 0.55);
    border-color: transparent;
    backdrop-filter: blur(8px);
    display: inline-flex;
    align-items: center;
    justify-content: center;
}

.loading,
.error {
    padding: 4rem 1rem;
    text-align: center;
    color: var(--fg-muted);
}
.error {
    color: var(--danger);
}

.hero {
    position: relative;
    aspect-ratio: 16 / 9;
    background: linear-gradient(135deg, #1a2030, #2a3349);
    overflow: hidden;
}
.art {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}
.hero-overlay {
    position: absolute;
    inset: 0;
    background: linear-gradient(
        to bottom,
        rgba(12, 14, 18, 0.2),
        var(--bg) 95%
    );
}
.hero-body {
    position: absolute;
    left: 1rem;
    right: 1rem;
    bottom: 1rem;
}
.title {
    margin: 0 0 0.2rem;
    font-size: 1.6rem;
    line-height: 1.15;
    font-weight: 700;
    letter-spacing: -0.015em;
    text-shadow: 0 2px 8px rgba(0, 0, 0, 0.7);
}
.meta {
    font-size: 0.85rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    align-items: center;
    text-shadow: 0 1px 4px rgba(0, 0, 0, 0.7);
}
.meta .sync {
    color: var(--accent-sync);
}

.actions-row {
    padding: 0.8rem 1rem 0;
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
}

.movie-pane {
    padding: 1rem;
}
.movie-files {
    margin-bottom: 1rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
}
.file-row {
    display: flex;
    gap: 0.6rem;
    padding: 0.55rem 0.7rem;
    align-items: center;
    border-bottom: 1px solid var(--border);
    font-size: 0.85rem;
}
.file-row:last-child {
    border-bottom: none;
}
.file-row.synced {
    background: rgba(41, 208, 208, 0.07);
}
.file-name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.8rem;
}
.file-tag.sync {
    color: var(--accent-sync);
    font-weight: 700;
}
.movie-actions {
    display: flex;
    gap: 0.5rem;
}
.movie-actions button {
    padding: 0.7rem 1.1rem;
    flex: 1;
    font-size: 0.95rem;
}

.seasons {
    padding: 0.6rem 1rem 6rem; /* extra bottom padding for action bar */
}
.season + .season {
    margin-top: 0.6rem;
}
.season-head {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.6rem 0.7rem;
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    cursor: pointer;
    font-size: 0.9rem;
}
.season-head:hover {
    background: var(--bg-hover);
}
.chev {
    display: inline-block;
    width: 12px;
    transition: transform 100ms ease;
    color: var(--fg-dim);
}
.chev.open {
    transform: rotate(90deg);
}
.tag {
    font-size: 0.72rem;
    padding: 1px 6px;
    border-radius: 4px;
    font-weight: 600;
}
.tag.watched {
    background: rgba(76, 175, 108, 0.18);
    color: var(--accent-watched);
}
.tag.sync {
    background: rgba(41, 208, 208, 0.18);
    color: var(--accent-sync);
}
.mini {
    padding: 0.25rem 0.55rem;
    font-size: 0.78rem;
}

.eps {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 0.5rem;
    padding: 0.6rem 0.2rem;
}

.action-bar {
    position: sticky;
    bottom: 0;
    display: flex;
    align-items: center;
    gap: 0.8rem;
    padding: 0.75rem 1rem;
    background: rgba(12, 14, 18, 0.97);
    border-top: 1px solid var(--border);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    z-index: 4;
}
.selection-info {
    font-size: 0.9rem;
    flex: 1;
}
.action-buttons {
    display: flex;
    gap: 0.5rem;
}

@media (max-width: 720px) {
    .drawer {
        width: 100vw;
        border-left: none;
    }
    .hero {
        aspect-ratio: 16 / 10;
    }
    .title {
        font-size: 1.3rem;
    }
    .eps {
        grid-template-columns: 1fr 1fr;
    }
}
@media (max-width: 380px) {
    .eps {
        grid-template-columns: 1fr;
    }
}
</style>
