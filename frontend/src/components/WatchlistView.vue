<script setup lang="ts">
import { onMounted, ref } from "vue"
import { api } from "../api"
import type { WatchlistEntry } from "../types"
import { openDetail } from "../store"

const items = ref<WatchlistEntry[]>([])
const loading = ref(true)
const error = ref("")

async function load(): Promise<void> {
  loading.value = true
  error.value = ""
  try {
    const r = await api.watchlist()
    items.value = r.items
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="view fade-in">
    <div v-if="loading" class="info">Fetching watchlist…</div>
    <div v-else-if="error" class="info err">{{ error }}</div>
    <template v-else>
      <div v-if="items.length === 0" class="info">
        <p>Watchlist is empty.</p>
      </div>

      <div v-else class="list">
        <div
          v-for="item in items"
          :key="item.guid || item.title"
          class="row"
          :class="{ unavail: !item.matched }"
          @click="item.matched && item.lib && item.folder && openDetail(item.lib, item.folder)"
        >
          <div class="thumb-wrap">
            <img
              v-if="item.matched && item.lib && item.folder"
              :src="api.thumbUrl(item.lib, item.folder)"
              :alt="item.title"
              loading="lazy"
              @error="($event.target as HTMLImageElement).style.display='none'"
            />
          </div>
          <div class="meta-col">
            <div class="title">{{ item.title }}</div>
            <div class="meta dim">
              <span v-if="item.category">{{ item.category }}</span>
              <span v-if="item.matched && item.lib">
                <span v-if="item.category"> · </span>
                {{ item.lib }}
              </span>
              <span v-else class="unavail-tag">not in library</span>
            </div>
          </div>
          <div v-if="item.matched" class="status">
            <span v-if="(item.synced_pct ?? 0) > 0" class="dot synced" title="Synced">⬇</span>
            <span v-if="(item.watched_pct ?? 0) >= 100" class="dot watched" title="Watched">●</span>
            <span v-else-if="(item.watched_pct ?? 0) > 0" class="dot prog">{{ item.watched_pct }}%</span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.view { padding: 1rem; overflow-y: auto; flex: 1; }
.info { padding: 3rem 1rem; text-align: center; color: var(--fg-muted); }
.info.err { color: var(--danger); }

.list { display: flex; flex-direction: column; gap: 0.5rem; max-width: 900px; margin: 0 auto; }
.row {
  display: flex;
  gap: 0.8rem;
  align-items: center;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.6rem 0.7rem;
  cursor: pointer;
  transition: background 100ms ease;
}
.row:hover { background: var(--bg-hover); }
.row.unavail { opacity: 0.55; cursor: default; }
.row.unavail:hover { background: var(--bg-elev); }

.thumb-wrap {
  width: 42px;
  aspect-ratio: 2 / 3;
  background: var(--bg-elev-2);
  border-radius: 4px;
  overflow: hidden;
  flex-shrink: 0;
}
.thumb-wrap img { width: 100%; height: 100%; object-fit: cover; display: block; }

.meta-col { flex: 1; min-width: 0; }
.title { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { font-size: 0.78rem; }
.unavail-tag { color: var(--fg-dim); font-style: italic; }

.status {
  display: flex; gap: 0.5rem; align-items: center; font-size: 0.95rem;
}
.dot.synced { color: var(--accent-sync); }
.dot.watched { color: var(--accent-watched); }
.dot.prog { color: var(--accent-progress); font-size: 0.78rem; font-weight: 600; }
</style>
