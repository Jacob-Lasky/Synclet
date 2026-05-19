<script setup lang="ts">
import type { Episode } from "../types"
import { epCode, humanSize } from "../store"

defineProps<{ ep: Episode; selected: boolean }>()
defineEmits<{ (e: "toggle", season: number, episode: number, shift: boolean): void }>()
</script>

<template>
  <button
    :class="[
      'tile',
      { selected, watched: ep.watch_state === 'watched', synced: ep.is_synced },
    ]"
    @click="$emit('toggle', ep.season, ep.episode, ($event as MouseEvent).shiftKey)"
  >
    <div class="row1">
      <span class="code mono">{{ epCode(ep.season, ep.episode) }}</span>
      <span class="state-icons">
        <span v-if="ep.watch_state === 'watched'" class="ico watched" title="Watched">●</span>
        <span v-else-if="ep.watch_state === 'progress'" class="ico progress" title="In progress">◐</span>
        <span v-else class="ico unw" title="Unwatched">○</span>
        <span v-if="ep.is_synced" class="ico synced" title="Synced">⬇</span>
      </span>
    </div>
    <div class="title" :title="ep.title">{{ ep.title || "—" }}</div>
    <div class="meta dim">{{ humanSize(ep.size_bytes) }}</div>
  </button>
</template>

<style scoped>
.tile {
  text-align: left;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.55rem 0.7rem;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
  transition: background 100ms ease, border-color 100ms ease;
  position: relative;
}
.tile:hover { background: var(--bg-hover); border-color: var(--border-strong); }
.tile.selected {
  border-color: var(--accent-action);
  background: rgba(108, 142, 239, 0.10);
  box-shadow: inset 0 0 0 1px var(--accent-action);
}
.tile.watched { opacity: 0.65; }
.tile.synced::after {
  content: "";
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 3px;
  background: var(--accent-sync);
  border-radius: var(--radius) 0 0 var(--radius);
}

.row1 {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.code {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--fg-muted);
  letter-spacing: 0.02em;
}
.state-icons { display: inline-flex; gap: 6px; font-size: 0.8rem; line-height: 1; }
.ico.watched { color: var(--accent-watched); }
.ico.progress { color: var(--accent-progress); }
.ico.unw { color: var(--fg-dim); }
.ico.synced { color: var(--accent-sync); font-weight: 700; }

.title {
  font-size: 0.88rem;
  color: var(--fg);
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.meta { font-size: 0.72rem; }
</style>
