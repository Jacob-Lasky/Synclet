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
            <span class="dot" aria-hidden="true"></span>
            <span class="name">synclet</span>
            <span class="brand-sub">Your offline media</span>
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
                aria-label="Paste a Plex link or search query"
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
            <button
                class="ghost"
                title="Rescan library"
                aria-label="Rescan library"
                @click="onRefresh"
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
                    <path d="M23 4v6h-6" />
                    <path d="M20.49 15A9 9 0 116.51 5.36L1 10" />
                </svg>
            </button>
        </div>
    </header>
</template>

<style scoped>
.topbar {
    z-index: 50;
    display: flex;
    align-items: center;
    gap: 1rem;
    min-height: 62px;
    padding: 0.65rem 1.25rem;
    background: var(--bg-elev);
    border-bottom: 1px solid var(--border);
}
.brand {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    font-size: 1.12rem;
    white-space: nowrap;
}
.brand .name {
    color: var(--fg);
}
.brand-sub {
    margin-left: 0.5rem;
    padding-left: 0.8rem;
    border-left: 1px solid var(--border-strong);
    color: var(--fg-dim);
    font-size: 0.76rem;
    font-weight: 500;
    letter-spacing: 0;
}
.dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--accent-sync);
    box-shadow: 0 0 12px rgba(41, 208, 208, 0.55);
}
.disk {
    flex: 0 1 370px;
    margin-left: auto;
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
    padding: 0.5rem 0.75rem;
    min-height: 38px;
}
.actions .lbl {
    font-size: 0.85rem;
}

@media (max-width: 600px) {
    .topbar {
        min-height: 56px;
        padding: 0.55rem 0.9rem;
        gap: 0.6rem;
    }
    .disk {
        display: none;
    }
    .brand-sub {
        display: none;
    }
    .actions {
        margin-left: auto;
    }
    .actions .lbl {
        display: none;
    }
}
</style>
