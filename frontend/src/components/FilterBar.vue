<script setup lang="ts">
import { computed, ref, watch } from "vue"
import { store, filteredTitles } from "../store"

const localQuery = ref(store.query)
let t: number | undefined
watch(localQuery, (v) => {
    clearTimeout(t)
    t = setTimeout(() => {
        store.query = v
    }, 120)
})

// Library chips come from the API so backend + frontend agree on the canonical
// label and order; no hardcoded map to drift.
const LIBS = computed(() =>
    store.libraries.map((l) => ({ id: l.id, label: l.label }))
)

const STATES = [
    { id: "unwatched", label: "Unwatched", color: "var(--fg-muted)" },
    { id: "watching", label: "Watching", color: "var(--accent-progress)" },
    { id: "synced", label: "Synced", color: "var(--accent-sync)" },
    { id: "new", label: "Has new", color: "var(--accent-progress)" },
]

function toggle(set: Set<string>, id: string): void {
    if (set.has(id)) set.delete(id)
    else set.add(id)
}
</script>

<template>
    <div class="filter-bar">
        <div class="search">
            <svg
                class="ico"
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
            >
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
                v-model="localQuery"
                type="search"
                placeholder="Search library… (fa = Fallout)"
                autocomplete="off"
                spellcheck="false"
            />
            <span v-if="store.loaded" class="count">{{
                filteredTitles.length
            }}</span>
        </div>

        <div class="chips">
            <button
                v-for="l in LIBS"
                :key="l.id"
                :class="['chip', { on: store.libFilter.has(l.id) }]"
                @click="toggle(store.libFilter, l.id)"
            >
                {{ l.label }}
            </button>

            <span class="div"></span>

            <button
                v-for="s in STATES"
                :key="s.id"
                :class="['chip', 'state', { on: store.stateFilter.has(s.id) }]"
                :style="{ '--state-color': s.color } as any"
                @click="toggle(store.stateFilter, s.id)"
            >
                <span class="dot" :style="{ background: s.color }"></span>
                {{ s.label }}
            </button>
        </div>
    </div>
</template>

<style scoped>
.filter-bar {
    position: sticky;
    top: 49px;
    z-index: 40;
    background: rgba(12, 14, 18, 0.92);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
    padding: 0.6rem 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
}

.search {
    position: relative;
    display: flex;
    align-items: center;
}
.search .ico {
    position: absolute;
    left: 0.7rem;
    color: var(--fg-dim);
    pointer-events: none;
}
.search input {
    padding-left: 2.1rem;
    padding-right: 3.3rem;
    font-size: 0.95rem;
}
.search .count {
    position: absolute;
    right: 0.6rem;
    font-size: 0.78rem;
    color: var(--fg-dim);
    background: var(--bg-elev);
    padding: 2px 8px;
    border-radius: 10px;
}

.chips {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.35rem;
}
.chip {
    padding: 0.32rem 0.7rem;
    font-size: 0.82rem;
    border-radius: 999px;
    background: var(--bg-elev);
    border: 1px solid var(--border);
    color: var(--fg-muted);
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
}
.chip:hover {
    color: var(--fg);
    border-color: var(--border-strong);
    background: var(--bg-elev-2);
}
.chip.on {
    background: var(--bg-elev-2);
    border-color: var(--accent-action);
    color: var(--fg);
}
.chip.state.on {
    border-color: var(--state-color);
}
.chip .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
}

.div {
    width: 1px;
    height: 18px;
    background: var(--border);
    margin: 0 0.25rem;
}

@media (max-width: 600px) {
    .filter-bar {
        top: 47px;
        padding: 0.5rem 0.7rem;
    }
}
</style>
