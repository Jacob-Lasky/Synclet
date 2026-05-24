<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import TopBar from "./components/TopBar.vue"
import TabBar from "./components/TabBar.vue"
import FilterBar from "./components/FilterBar.vue"
import TitleGrid from "./components/TitleGrid.vue"
import DetailDrawer from "./components/DetailDrawer.vue"
import SyncedView from "./components/SyncedView.vue"
import WatchlistView from "./components/WatchlistView.vue"
import MaintenanceView from "./components/MaintenanceView.vue"
import SyncthingView from "./components/SyncthingView.vue"
import LinkPasteModal from "./components/LinkPasteModal.vue"
import JobToasts from "./components/JobToasts.vue"
import WatchStateCoverageBanner from "./components/WatchStateCoverageBanner.vue"
import { api } from "./api"
import {
    loadCoverage,
    loadMaintenanceCount,
    loadState,
    loadWatchlistCount,
    pushToast,
    store,
} from "./store"

const showPaste = ref(false)

onMounted(async () => {
    // Critical-path: the library grid is gated on store.loaded. Awaiting
    // /api/state alone first means it doesn't compete with the three
    // badge/banner endpoints for the backend's Plex section_index lru_cache
    // and the GIL; cold load drops from ~22s (four-way race) to roughly the
    // isolated /api/state cost (~1s with backend warm + parallel section
    // fetch).
    try {
        await loadState()
    } catch (e) {
        pushToast({
            kind: "error",
            text: `Failed to load state: ${(e as Error).message}`,
        })
    }
    // Badges and the WatchState coverage banner. None of these gate the grid,
    // so they fire-and-forget AFTER the grid has rendered; user-visible delay
    // ends at /api/state.
    loadMaintenanceCount()
    loadWatchlistCount()
    loadCoverage()
})

const counts = computed(() => ({
    library: store.titles.length,
    synced: store.disk?.synced_titles ?? 0,
    watchlist: store.watchlistCount ?? undefined,
    // Maintenance is "attention required" not "inventory" — only badge when
    // the count is positive. 0 means the user has nothing to do; no badge.
    maintenance:
        store.maintenanceCount != null && store.maintenanceCount > 0
            ? store.maintenanceCount
            : undefined,
}))

async function refresh(): Promise<void> {
    try {
        await api.refresh()
        await loadState(true)
        pushToast({ kind: "success", text: "Library rescanned." })
    } catch (e) {
        pushToast({ kind: "error", text: (e as Error).message })
    }
}

// Global ⌘K / Ctrl-K → paste modal
function onKeydown(e: KeyboardEvent): void {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        showPaste.value = true
    }
    if (e.key === "Escape" && showPaste.value) {
        showPaste.value = false
    }
}
window.addEventListener("keydown", onKeydown)
</script>

<template>
    <div class="app">
        <TopBar
            :on-paste-link="() => (showPaste = true)"
            :on-refresh="refresh"
        />
        <TabBar
            :tab="store.tab"
            :counts="counts"
            @change="(t) => (store.tab = t)"
        />
        <WatchStateCoverageBanner />

        <main class="main">
            <!-- KeepAlive preserves Synced / Watchlist / Maintenance state across
           tab switches so leaving and returning is instant. Once loaded,
           these views stay mounted and re-show without re-fetching. Library
           is excluded because its TitleGrid is already store-backed and
           cheap to remount. -->
            <template v-if="store.tab === 'library'">
                <FilterBar />
                <TitleGrid v-if="store.loaded" />
                <div v-else class="loading">Loading library…</div>
            </template>
            <KeepAlive v-else>
                <SyncedView v-if="store.tab === 'synced'" />
                <WatchlistView v-else-if="store.tab === 'watchlist'" />
                <MaintenanceView v-else-if="store.tab === 'maintenance'" />
                <SyncthingView v-else-if="store.tab === 'syncthing'" />
            </KeepAlive>
        </main>

        <DetailDrawer v-if="store.detail" />
        <LinkPasteModal v-if="showPaste" @close="showPaste = false" />
        <JobToasts />
    </div>
</template>

<style scoped>
.app {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--bg);
}
.main {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0; /* allow children to scroll */
}
.loading {
    padding: 4rem 1rem;
    text-align: center;
    color: var(--fg-muted);
}
</style>
