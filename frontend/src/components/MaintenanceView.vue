<script setup lang="ts">
import { onMounted, ref } from "vue"
import { api } from "../api"
import type { HangingFile, WatchedFiles } from "../types"
import { humanSize, loadState, pushToast } from "../store"

const watched = ref<WatchedFiles[]>([])
const hanging = ref<HangingFile[]>([])
const loading = ref(true)
const error = ref("")
const removing = ref(false)

async function load(): Promise<void> {
  loading.value = true
  error.value = ""
  try {
    const [w, h] = await Promise.all([api.maintWatched(), api.maintHanging()])
    watched.value = w.items
    hanging.value = h.items
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function removePaths(paths: string[], label: string): Promise<void> {
  if (paths.length === 0) return
  if (!confirm(`Remove ${paths.length} file${paths.length === 1 ? "" : "s"}?\n\n${label}`)) return
  removing.value = true
  try {
    const r = await api.maintRemove(paths)
    pushToast({
      kind: "success",
      text: `Removed ${r.removed} files (${humanSize(r.bytes_freed)})`,
    })
    await load()
    await loadState(true)
  } catch (e) {
    pushToast({ kind: "error", text: (e as Error).message })
  } finally {
    removing.value = false
  }
}

function removeAllWatched(): void {
  const paths = watched.value.flatMap(w => w.files)
  const total = watched.value.reduce((s, w) => s + w.size_bytes, 0)
  removePaths(paths, `${watched.value.length} title${watched.value.length === 1 ? "" : "s"} · ${humanSize(total)}`)
}

function removeAllHanging(): void {
  const paths = hanging.value.map(h => h.path)
  const total = hanging.value.reduce((s, h) => s + h.size_bytes, 0)
  removePaths(paths, `${hanging.value.length} hanging files · ${humanSize(total)}`)
}

function totalWatchedBytes(): number {
  return watched.value.reduce((s, w) => s + w.size_bytes, 0)
}
function totalHangingBytes(): number {
  return hanging.value.reduce((s, h) => s + h.size_bytes, 0)
}
</script>

<template>
  <div class="view fade-in">
    <div v-if="loading" class="info">Scanning…</div>
    <div v-else-if="error" class="info err">{{ error }}</div>

    <template v-else>
      <section class="panel">
        <header>
          <h2>Watched in synced-media</h2>
          <span class="dim">{{ watched.length }} title{{ watched.length === 1 ? "" : "s" }} · {{ humanSize(totalWatchedBytes()) }}</span>
          <span class="spacer"></span>
          <button v-if="watched.length > 0" class="danger" :disabled="removing" @click="removeAllWatched">
            Remove all
          </button>
        </header>
        <p v-if="watched.length === 0" class="empty">Nothing watched in synced-media. Tidy!</p>
        <ul v-else>
          <li v-for="w in watched" :key="w.lib + '/' + w.folder">
            <span class="name">{{ w.title }}</span>
            <span class="lib dim">{{ w.lib }}</span>
            <span class="count dim">{{ w.file_count }} files</span>
            <span class="size mono dim">{{ humanSize(w.size_bytes) }}</span>
            <button class="danger mini" :disabled="removing" @click="removePaths(w.files, w.title)">
              Remove
            </button>
          </li>
        </ul>
      </section>

      <section class="panel">
        <header>
          <h2>Hanging files</h2>
          <span class="dim">{{ hanging.length }} file{{ hanging.length === 1 ? "" : "s" }} · {{ humanSize(totalHangingBytes()) }}</span>
          <span class="spacer"></span>
          <button v-if="hanging.length > 0" class="danger" :disabled="removing" @click="removeAllHanging">
            Remove all
          </button>
        </header>
        <p v-if="hanging.length === 0" class="empty">No hanging files — every synced item has a source.</p>
        <ul v-else>
          <li v-for="h in hanging" :key="h.path">
            <span class="name mono">{{ h.rel }}</span>
            <span class="spacer"></span>
            <span class="size dim mono">{{ humanSize(h.size_bytes) }}</span>
            <button class="danger mini" :disabled="removing" @click="removePaths([h.path], h.rel)">
              Remove
            </button>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>

<style scoped>
.view { padding: 1rem; overflow-y: auto; flex: 1; max-width: 1100px; margin: 0 auto; width: 100%; }
.info { padding: 3rem 1rem; text-align: center; color: var(--fg-muted); }
.info.err { color: var(--danger); }

.panel {
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: 1rem;
}
.panel header {
  display: flex; align-items: center; gap: 0.7rem;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--border);
}
.panel h2 { margin: 0; font-size: 1rem; font-weight: 600; }

.empty { padding: 1.2rem 1rem; color: var(--fg-muted); margin: 0; }
ul { list-style: none; padding: 0; margin: 0; }
li {
  display: flex; align-items: center; gap: 0.7rem;
  padding: 0.55rem 1rem;
  border-bottom: 1px solid var(--border);
  font-size: 0.88rem;
}
li:last-child { border-bottom: none; }
.name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.size, .count { font-size: 0.78rem; }
.mini { padding: 0.25rem 0.55rem; font-size: 0.78rem; }
.spacer { flex: 1; }

@media (max-width: 600px) {
  li { flex-wrap: wrap; }
  .name { flex: 1 1 100%; }
}
</style>
