<script setup lang="ts">
import { computed, ref, watch } from "vue"
import TitleCard from "./TitleCard.vue"
import { filteredTitles, openDetail } from "../store"

// Progressive render: show first N, expand as the user scrolls near the bottom.
const PAGE = 120
const visible = ref(PAGE)

const list = computed(() => filteredTitles.value.slice(0, visible.value))
const total = computed(() => filteredTitles.value.length)

watch(filteredTitles, () => {
    visible.value = PAGE
})

function onScroll(e: Event): void {
    const el = e.target as HTMLElement
    if (el.scrollTop + el.clientHeight > el.scrollHeight - 400) {
        if (visible.value < total.value) {
            visible.value = Math.min(visible.value + PAGE, total.value)
        }
    }
}
</script>

<template>
    <div class="grid-scroll" @scroll.passive="onScroll">
        <div class="grid" :class="{ empty: total === 0 }">
            <TitleCard
                v-for="t in list"
                :key="t.id"
                :title="t"
                @open="openDetail"
            />
        </div>

        <div v-if="total === 0" class="empty-state">
            <p>No titles match.</p>
            <p class="dim">Try fewer keywords, or clear the filters.</p>
        </div>

        <div v-else-if="visible < total" class="more-hint dim">
            Showing {{ visible }} of {{ total }} — scroll for more
        </div>
    </div>
</template>

<style scoped>
.grid-scroll {
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
}
.grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    gap: 1rem;
}
.grid.empty {
    min-height: 30vh;
}

.empty-state {
    text-align: center;
    padding: 4rem 1rem;
    color: var(--fg-muted);
}
.empty-state p {
    margin: 0.25rem 0;
}

.more-hint {
    text-align: center;
    padding: 2rem 1rem;
    font-size: 0.85rem;
}

@media (min-width: 900px) {
    .grid {
        grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
        gap: 1.1rem;
    }
}
@media (min-width: 1400px) {
    .grid {
        grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    }
}
@media (max-width: 480px) {
    .grid-scroll {
        padding: 0.6rem;
    }
    .grid {
        grid-template-columns: repeat(2, 1fr);
        gap: 0.65rem;
    }
}
</style>
