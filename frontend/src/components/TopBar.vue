<script setup lang="ts">
import { computed } from "vue"
import { store, humanSize } from "../store"

defineProps<{ onPasteLink: () => void; onRefresh: () => void }>()

const diskPct = computed(() => store.disk?.pct ?? 0)
const syncedBytes = computed(() => humanSize(store.disk?.synced_bytes ?? 0))
const freeBytes = computed(() => humanSize(store.disk?.free ?? 0))
const syncedCount = computed(() => store.disk?.synced_titles ?? 0)
</script>

<template>
    <header class="topbar">
        <div class="brand">
            <span class="dot"></span>
            <span class="name">synclet</span>
        </div>

        <div v-if="store.disk" class="disk">
            <div class="disk-label">
                <span class="dim">synced</span>
                <strong>{{ syncedBytes }}</strong>
                <span class="dim">·</span>
                <span class="dim"
                    >{{ syncedCount }} item{{
                        syncedCount === 1 ? "" : "s"
                    }}</span
                >
                <span class="sep">/</span>
                <span class="dim">{{ freeBytes }} free</span>
            </div>
            <div class="bar">
                <div class="fill" :style="{ width: diskPct + '%' }"></div>
            </div>
        </div>

        <div class="actions">
            <button
                class="ghost"
                title="Paste a Plex link or search query"
                @click="onPasteLink"
            >
                <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                >
                    <path
                        d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"
                    />
                    <path
                        d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"
                    />
                </svg>
                <span class="lbl">paste</span>
            </button>
            <button class="ghost" title="Rescan library" @click="onRefresh">
                <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                >
                    <path d="M23 4v6h-6" />
                    <path d="M20.49 15A9 9 0 116.51 5.36L1 10" />
                </svg>
            </button>
        </div>
    </header>
</template>

<style scoped>
.topbar {
    position: sticky;
    top: 0;
    z-index: 50;
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.65rem 1rem;
    background: rgba(12, 14, 18, 0.92);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
}
.brand {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    font-size: 1rem;
}
.brand .name {
    color: var(--fg);
}
.dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--accent-sync);
    box-shadow: 0 0 12px rgba(41, 208, 208, 0.55);
}
.disk {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
    max-width: 460px;
}
.disk-label {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.82rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.disk-label strong {
    color: var(--accent-sync);
}
.sep {
    color: var(--fg-dim);
    margin: 0 0.25rem;
}
.bar {
    height: 4px;
    background: var(--bg-elev);
    border-radius: 4px;
    overflow: hidden;
}
.fill {
    height: 100%;
    background: linear-gradient(
        90deg,
        var(--accent-action),
        var(--accent-sync)
    );
    transition: width 220ms ease;
}
.actions {
    display: flex;
    gap: 0.4rem;
}
.actions button {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.45rem 0.7rem;
}
.actions .lbl {
    font-size: 0.85rem;
}

@media (max-width: 600px) {
    .topbar {
        padding: 0.55rem 0.7rem;
        gap: 0.6rem;
    }
    .disk {
        display: none;
    }
    .actions .lbl {
        display: none;
    }
}
</style>
