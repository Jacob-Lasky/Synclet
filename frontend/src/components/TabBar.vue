<script setup lang="ts">
import type { Tab } from "../types"

defineProps<{ tab: Tab; counts?: Partial<Record<Tab, number>> }>()
defineEmits<{ (e: "change", t: Tab): void }>()

const tabs: { id: Tab; label: string }[] = [
    { id: "library", label: "Library" },
    { id: "synced", label: "Synced" },
    { id: "watchlist", label: "Watchlist" },
    { id: "maintenance", label: "Maintenance" },
    { id: "syncthing", label: "Syncthing" },
]
</script>

<template>
    <nav class="tabs">
        <button
            v-for="t in tabs"
            :key="t.id"
            :class="['tab', { active: tab === t.id }]"
            @click="$emit('change', t.id)"
        >
            {{ t.label }}
            <span v-if="counts?.[t.id] != null" class="count">{{
                counts[t.id]
            }}</span>
        </button>
    </nav>
</template>

<style scoped>
.tabs {
    display: flex;
    gap: 2px;
    padding: 0 1rem;
    border-bottom: 1px solid var(--border);
    background: var(--bg);
    overflow-x: auto;
    scrollbar-width: none;
}
.tabs::-webkit-scrollbar {
    display: none;
}

.tab {
    padding: 0.65rem 0.95rem;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    color: var(--fg-muted);
    font-weight: 500;
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    white-space: nowrap;
}
.tab:hover {
    color: var(--fg);
    background: transparent;
}
.tab.active {
    color: var(--fg);
    border-bottom-color: var(--accent-action);
}
.count {
    background: var(--bg-elev-2);
    color: var(--fg-muted);
    padding: 1px 7px;
    border-radius: 10px;
    font-size: 0.72rem;
    font-weight: 600;
}
.tab.active .count {
    background: var(--accent-action);
    color: #fff;
}

@media (max-width: 600px) {
    .tabs {
        padding: 0 0.5rem;
    }
    .tab {
        padding: 0.55rem 0.7rem;
        font-size: 0.92rem;
    }
}
</style>
