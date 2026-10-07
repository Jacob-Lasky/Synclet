<script setup lang="ts">
import type { Tab } from "../types"

defineProps<{ tab: Tab; counts?: Partial<Record<Tab, number>> }>()
defineEmits<{ (e: "change", t: Tab): void }>()

const tabs: { id: Tab; label: string; icon: string }[] = [
    {
        id: "library",
        label: "Library",
        icon: "M4 5h16v14H4z M4 10h16 M10 10v9",
    },
    {
        id: "synced",
        label: "Synced",
        icon: "M12 3v12 m-4-4 4 4 4-4 M4 18h16v3H4z",
    },
    {
        id: "watchlist",
        label: "Watchlist",
        icon: "M20.8 8.2c0 4.6-8.8 10.6-8.8 10.6S3.2 12.8 3.2 8.2a4.5 4.5 0 0 1 8.8-1.3 4.5 4.5 0 0 1 8.8 1.3z",
    },
    {
        id: "maintenance",
        label: "Maintenance",
        icon: "M12 3v2 M12 19v2 M3 12h2 M19 12h2 M5.6 5.6l1.4 1.4 M17 17l1.4 1.4 M18.4 5.6 17 7 M7 17l-1.4 1.4 M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
    },
    {
        id: "syncthing",
        label: "Syncthing",
        icon: "M7 7h11l-3-3 M18 7l-3 3 M17 17H6l3 3 M6 17l3-3 M4 12a8 8 0 0 1 2-5 M20 12a8 8 0 0 1-2 5",
    },
]
</script>

<template>
    <nav class="tabs" aria-label="Primary navigation">
        <div class="nav-heading">Workspace</div>
        <button
            v-for="t in tabs"
            :key="t.id"
            :class="['tab', { active: tab === t.id }]"
            :aria-current="tab === t.id ? 'page' : undefined"
            @click="$emit('change', t.id)"
        >
            <svg
                viewBox="0 0 24 24"
                aria-hidden="true"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
            >
                <path :d="t.icon" />
            </svg>
            <span class="tab-label">{{ t.label }}</span>
            <span v-if="counts?.[t.id] != null" class="count">{{
                counts[t.id]
            }}</span>
        </button>
    </nav>
</template>

<style scoped>
.tabs {
    display: flex;
    gap: 3px;
    padding: 0.4rem 1rem;
    border-bottom: 1px solid var(--border);
    background: var(--bg-elev);
    overflow-x: auto;
    scrollbar-width: none;
}
.nav-heading {
    display: none;
}
.tabs::-webkit-scrollbar {
    display: none;
}

.tab {
    position: relative;
    padding: 0.65rem 0.85rem;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    color: var(--fg-muted);
    font-weight: 500;
    display: inline-flex;
    align-items: center;
    gap: 0.55rem;
    white-space: nowrap;
}
.tab svg {
    width: 18px;
    height: 18px;
    flex: none;
}
.tab:hover {
    color: var(--fg);
    background: transparent;
}
.tab.active {
    color: var(--fg);
    border-bottom-color: var(--accent-sync);
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
@media (min-width: 1100px) {
    .tabs {
        flex-direction: column;
        align-items: stretch;
        overflow: visible;
        padding: 1.35rem 0.75rem;
        border-right: 1px solid var(--border);
        border-bottom: 0;
        gap: 0.3rem;
    }
    .nav-heading {
        display: block;
        padding: 0 0.85rem 0.6rem;
        color: var(--fg-dim);
        text-transform: uppercase;
        letter-spacing: 0.11em;
        font-size: 0.67rem;
        font-weight: 700;
    }
    .tab {
        border: 0;
        border-radius: var(--radius);
        justify-content: flex-start;
        text-align: left;
        min-height: 44px;
    }
    .tab.active {
        background: var(--bg-elev-2);
        color: var(--accent-sync);
    }
    .count {
        margin-left: auto;
    }
}

@media (max-width: 700px) {
    .tabs {
        position: fixed;
        z-index: 80;
        left: 0;
        right: 0;
        bottom: 0;
        height: 68px;
        padding: 4px 4px max(4px, env(safe-area-inset-bottom));
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 0;
        border-top: 1px solid var(--border);
        border-bottom: 0;
        box-shadow: 0 -8px 24px rgba(0, 0, 0, 0.25);
    }
    .tab {
        min-width: 0;
        padding: 0.35rem 0.1rem;
        border: 0;
        border-radius: var(--radius);
        display: flex;
        flex-direction: column;
        justify-content: center;
        gap: 2px;
        font-size: 0.62rem;
        line-height: 1.15;
    }
    .tab svg {
        width: 20px;
        height: 20px;
    }
    .tab-label {
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 100%;
    }
    .tab.active {
        color: var(--accent-sync);
        background: var(--bg-elev-2);
    }
    .count {
        display: none;
    }
    .tab:nth-of-type(4) .count {
        display: block;
        position: absolute;
        top: 3px;
        right: 6px;
        min-width: 16px;
        text-align: center;
        padding: 0 3px;
        font-size: 0.6rem;
    }
}
</style>
