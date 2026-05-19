<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { api } from "../api"
import type {
  CleanupSummary,
  HangingFile,
  IgnoredGrouped,
  IgnoreKind,
  PendingEpisode,
  PendingGroup,
  PendingItemRef,
  ResolveAction,
  ResolveItemResult,
  WatchedFiles,
} from "../types"
import { humanSize, loadMaintenanceCount, loadState, pushToast, store } from "../store"

const watched = ref<WatchedFiles[]>([])
const hanging = ref<HangingFile[]>([])
const pending = ref<PendingGroup[]>([])
const ignoredList = ref<IgnoredGrouped>({ version: 1, pending: [], watched: [], hanging: [] })
const ignoredOpen = ref(false)
const loading = ref(true)
const error = ref("")
const removing = ref(false)
const resolving = ref(false)
// Tracks which show / season cards are expanded. Keys: `${folder}` for shows,
// `${folder}|${season}` for seasons.
const expanded = ref<Set<string>>(new Set())

async function load(): Promise<void> {
  loading.value = true
  error.value = ""
  try {
    const [w, h, p, ig] = await Promise.all([
      api.maintWatched(),
      api.maintHanging(),
      api.maintPending(),
      api.maintIgnored(),
    ])
    watched.value = w.items
    hanging.value = h.items
    pending.value = p.items
    ignoredList.value = ig
    // Surface the same counts to the tab badge. Cheap: load() already did
    // the FS walks the counts endpoint would do; this round-trip just sums
    // them server-side.
    loadMaintenanceCount()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

const ignoredCount = computed(() =>
  ignoredList.value.pending.length
  + ignoredList.value.watched.length
  + ignoredList.value.hanging.length
)

// Mutate the local lists without re-fetching from the server. The
// server-side cache TTL (30s) means a reload would still be slow even after
// the invalidation triggered by the ignore call. Optimistic UI keeps the
// click snappy; on error we restore the entry and surface the error.

function removePendingLocal(kind: IgnoreKind, ref: any): void {
  if (kind === "watched") {
    watched.value = watched.value.filter(
      w => !(w.lib === ref.lib && w.folder === ref.folder),
    )
  } else if (kind === "hanging") {
    hanging.value = hanging.value.filter(h => h.path !== ref.path)
  } else if (kind === "pending") {
    if (ref.season === null || ref.season === undefined) {
      // Movie-level pending: drop the entire group.
      pending.value = pending.value.filter(
        g => !(g.sync_sub === ref.sync_sub && g.folder === ref.folder),
      )
    } else {
      // Episode-level pending: drop the episode; if the season becomes
      // empty, drop the season; if the group becomes empty, drop the group.
      pending.value = pending.value
        .map(g => {
          if (g.sync_sub !== ref.sync_sub || g.folder !== ref.folder) return g
          const seasons = (g.seasons ?? [])
            .map(s => {
              if (s.season !== ref.season) return s
              return { ...s, episodes: s.episodes.filter(e => e.episode !== ref.episode) }
            })
            .filter(s => s.episodes.length > 0)
          return { ...g, seasons }
        })
        .filter(g => g.kind === "movie" || (g.seasons?.length ?? 0) > 0)
    }
  }
}

async function ignoreItem(kind: IgnoreKind, ref: any, label: string): Promise<void> {
  // Snapshot for rollback on error.
  const snapshot = {
    watched: watched.value,
    hanging: hanging.value,
    pending: pending.value,
    ignoredList: ignoredList.value,
  }
  // Optimistic: drop from source list immediately.
  removePendingLocal(kind, ref)
  // Add to the ignored list so the Ignored section reflects it without a refetch.
  if (kind === "watched") {
    ignoredList.value = {
      ...ignoredList.value,
      watched: [...ignoredList.value.watched, ref],
    }
  } else if (kind === "hanging") {
    ignoredList.value = {
      ...ignoredList.value,
      hanging: [...ignoredList.value.hanging, ref],
    }
  } else {
    ignoredList.value = {
      ...ignoredList.value,
      pending: [...ignoredList.value.pending, {
        sync_sub: ref.sync_sub,
        folder: ref.folder,
        season: ref.season ?? null,
        episode: ref.episode ?? null,
      }],
    }
  }
  // Decrement the tab badge locally so it updates instantly.
  if (typeof store.maintenanceCount === "number") {
    store.maintenanceCount = Math.max(0, store.maintenanceCount - 1)
  }

  try {
    const r = await api.maintIgnore(kind, ref)
    if (!r.ok) {
      // Rollback
      watched.value = snapshot.watched
      hanging.value = snapshot.hanging
      pending.value = snapshot.pending
      ignoredList.value = snapshot.ignoredList
      loadMaintenanceCount()
      pushToast({ kind: "error", text: `Could not ignore ${label}` })
      return
    }
    pushToast({ kind: "success", text: `Ignored ${label}` })
  } catch (e) {
    watched.value = snapshot.watched
    hanging.value = snapshot.hanging
    pending.value = snapshot.pending
    ignoredList.value = snapshot.ignoredList
    loadMaintenanceCount()
    pushToast({ kind: "error", text: (e as Error).message })
  }
}

async function unignoreItem(kind: IgnoreKind, ref: any, label: string): Promise<void> {
  // Unignore needs a refetch because the entry has to reappear in its
  // original source list, and we don't have all the fields (size, files)
  // locally to reconstruct it.
  try {
    const r = await api.maintUnignore(kind, ref)
    if (!r.ok) {
      pushToast({ kind: "error", text: `Could not unignore ${label}` })
      return
    }
    pushToast({ kind: "success", text: `Unignored ${label}` })
    await load()
  } catch (e) {
    pushToast({ kind: "error", text: (e as Error).message })
  }
}

onMounted(load)

function toggleShow(folder: string): void {
  if (expanded.value.has(folder)) {
    expanded.value.delete(folder)
  } else {
    expanded.value.add(folder)
  }
  // reactive Set: reassign to trigger re-render
  expanded.value = new Set(expanded.value)
}

function toggleSeason(folder: string, season: number): void {
  const key = `${folder}|${season}`
  if (expanded.value.has(key)) {
    expanded.value.delete(key)
  } else {
    expanded.value.add(key)
  }
  expanded.value = new Set(expanded.value)
}

function totalEpisodes(g: PendingGroup): number {
  return (g.seasons ?? []).reduce((n, s) => n + s.episodes.length, 0)
}

function refsForGroup(g: PendingGroup): PendingItemRef[] {
  if (g.kind === "movie") {
    return [{ sync_sub: g.sync_sub, folder: g.folder }]
  }
  return (g.seasons ?? []).flatMap(s =>
    s.episodes.map(e => ({
      sync_sub: g.sync_sub,
      folder: g.folder,
      season: e.season,
      episode: e.episode,
    }))
  )
}

function refsForSeason(g: PendingGroup, season: number): PendingItemRef[] {
  const s = (g.seasons ?? []).find(s => s.season === season)
  if (!s) return []
  return s.episodes.map(e => ({
    sync_sub: g.sync_sub,
    folder: g.folder,
    season: e.season,
    episode: e.episode,
  }))
}

function refForEpisode(g: PendingGroup, ep: PendingEpisode): PendingItemRef {
  return { sync_sub: g.sync_sub, folder: g.folder, season: ep.season, episode: ep.episode }
}

function summarizeResolve(results: ResolveItemResult[]): string {
  const counts = { ok: 0, scrobble_failed: 0, no_rating_key: 0, rejected: 0 }
  for (const r of results) counts[r.status] += 1
  const parts: string[] = []
  if (counts.ok) parts.push(`${counts.ok} scrobbled`)
  if (counts.rejected) parts.push(`${counts.rejected} rejected`)
  if (counts.scrobble_failed) parts.push(`${counts.scrobble_failed} scrobble failed`)
  if (counts.no_rating_key) parts.push(`${counts.no_rating_key} not found in Plex`)
  return parts.join(", ") || "no items"
}

function summarizeCleanup(c: CleanupSummary | undefined): string {
  if (!c || (c.removed_files === 0 && c.removed_dirs === 0)) return ""
  const parts: string[] = []
  if (c.removed_files) parts.push(`${c.removed_files} sidecar${c.removed_files === 1 ? "" : "s"}`)
  if (c.removed_dirs) parts.push(`${c.removed_dirs} folder${c.removed_dirs === 1 ? "" : "s"}`)
  return ` (cleaned ${parts.join(", ")})`
}

async function resolveItems(
  items: PendingItemRef[],
  action: ResolveAction,
  label: string,
): Promise<void> {
  if (items.length === 0) return
  const verb = action === "confirm" ? "Mark watched" : "Reject"
  if (!confirm(`${verb} ${items.length} item${items.length === 1 ? "" : "s"}?\n\n${label}`)) {
    return
  }
  resolving.value = true
  try {
    const res = await api.maintResolve(items, action)
    if (res.error) {
      pushToast({ kind: "error", text: res.error })
    } else {
      const anyFailed = res.results.some(
        r => r.status === "scrobble_failed" || r.status === "no_rating_key"
      )
      pushToast({
        kind: anyFailed ? "error" : "success",
        text: `${verb}: ${summarizeResolve(res.results)}${summarizeCleanup(res.cleanup)}`,
      })
    }
    await load()
    await loadState(true)
  } catch (e) {
    pushToast({ kind: "error", text: (e as Error).message })
  } finally {
    resolving.value = false
  }
}

const totalPendingEpisodes = computed(() =>
  pending.value.reduce((n, g) => n + (g.kind === "movie" ? 1 : totalEpisodes(g)), 0)
)

async function removePaths(paths: string[], label: string): Promise<void> {
  if (paths.length === 0) return
  if (!confirm(`Remove ${paths.length} file${paths.length === 1 ? "" : "s"}?\n\n${label}`)) return
  // Optimistic: drop the matching rows from local state immediately so the
  // UI updates without waiting for the (cold) re-walk that follows the
  // server-side cache invalidation. Snapshot for rollback.
  const pathSet = new Set(paths)
  const snapshotW = watched.value
  const snapshotH = hanging.value
  watched.value = watched.value
    .map(w => ({ ...w, files: w.files.filter(p => !pathSet.has(p)) }))
    .filter(w => w.files.length > 0)
  hanging.value = hanging.value.filter(h => !pathSet.has(h.path))
  // Approximate badge decrement: number of titles+files dropped from view.
  const dropped =
    (snapshotW.length - watched.value.length)
    + (snapshotH.length - hanging.value.length)
  if (typeof store.maintenanceCount === "number" && dropped > 0) {
    store.maintenanceCount = Math.max(0, store.maintenanceCount - dropped)
  }
  removing.value = true
  try {
    const r = await api.maintRemove(paths)
    pushToast({
      kind: "success",
      text: `Removed ${r.removed} files (${humanSize(r.bytes_freed)})${summarizeCleanup(r.cleanup)}`,
    })
    // Refresh state in the background (for the library grid's synced/watched
    // percentages); not awaited so the maintenance view stays snappy.
    loadState(true)
  } catch (e) {
    // Rollback the optimistic state and surface the error.
    watched.value = snapshotW
    hanging.value = snapshotH
    loadMaintenanceCount()
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
            <button
              class="ghost mini"
              title="Mute this entry"
              @click="ignoreItem('watched', { lib: w.lib, folder: w.folder }, w.title)"
            >
              Ignore
            </button>
          </li>
        </ul>
      </section>

      <section class="panel" data-testid="pending-deletions">
        <header>
          <h2>Deleted, awaiting Plex sync</h2>
          <span class="dim">
            {{ pending.length }} title{{ pending.length === 1 ? "" : "s" }}
            <template v-if="totalPendingEpisodes !== pending.length">
              · {{ totalPendingEpisodes }} item{{ totalPendingEpisodes === 1 ? "" : "s" }}
            </template>
          </span>
        </header>
        <p v-if="pending.length === 0" class="empty">
          No pending deletions. Synced media matches the snapshot.
        </p>
        <ul v-else class="pending-list">
          <li v-for="g in pending" :key="g.sync_sub + '/' + g.folder" class="group">
            <!-- Movie: single row, no nesting -->
            <template v-if="g.kind === 'movie'">
              <div class="row movie">
                <span class="name">{{ g.title }}</span>
                <span v-if="g.already_watched_in_plex" class="badge dim">already watched in Plex</span>
                <span class="lib dim">{{ g.lib ?? '—' }}</span>
                <span class="spacer"></span>
                <button
                  class="primary mini"
                  :disabled="resolving"
                  @click="resolveItems(refsForGroup(g), 'confirm', g.title)"
                >
                  Mark watched
                </button>
                <button
                  class="danger mini"
                  :disabled="resolving"
                  @click="resolveItems(refsForGroup(g), 'reject', g.title)"
                >
                  Reject
                </button>
                <button
                  class="ghost mini"
                  title="Mute this entry"
                  @click="ignoreItem('pending', { sync_sub: g.sync_sub, folder: g.folder, season: null, episode: null }, g.title)"
                >
                  Ignore
                </button>
              </div>
            </template>

            <!-- Show / YouTube: expandable header + nested seasons -->
            <template v-else>
              <div class="row show-header">
                <button
                  class="toggle"
                  :aria-expanded="expanded.has(g.folder)"
                  @click="toggleShow(g.folder)"
                >
                  <span class="caret">{{ expanded.has(g.folder) ? "▾" : "▸" }}</span>
                  <span class="name">{{ g.title }}</span>
                </button>
                <span class="dim">
                  {{ totalEpisodes(g) }} episode{{ totalEpisodes(g) === 1 ? "" : "s" }}
                </span>
                <span class="spacer"></span>
                <button
                  class="primary mini"
                  :disabled="resolving"
                  @click="resolveItems(refsForGroup(g), 'confirm', `${g.title} (all)`)"
                >
                  Mark all watched
                </button>
                <button
                  class="danger mini"
                  :disabled="resolving"
                  @click="resolveItems(refsForGroup(g), 'reject', `${g.title} (all)`)"
                >
                  Reject all
                </button>
              </div>
              <ul v-if="expanded.has(g.folder)" class="seasons">
                <li v-for="s in g.seasons ?? []" :key="g.folder + '|' + s.season" class="season">
                  <div class="row season-header">
                    <button
                      class="toggle"
                      :aria-expanded="expanded.has(`${g.folder}|${s.season}`)"
                      @click="toggleSeason(g.folder, s.season)"
                    >
                      <span class="caret">
                        {{ expanded.has(`${g.folder}|${s.season}`) ? "▾" : "▸" }}
                      </span>
                      <span class="season-label">Season {{ s.season }}</span>
                    </button>
                    <span class="dim">
                      {{ s.episodes.length }} ep{{ s.episodes.length === 1 ? "" : "s" }}
                    </span>
                    <span class="spacer"></span>
                    <button
                      class="primary mini"
                      :disabled="resolving"
                      @click="resolveItems(refsForSeason(g, s.season), 'confirm', `${g.title} S${s.season}`)"
                    >
                      Mark season watched
                    </button>
                    <button
                      class="danger mini"
                      :disabled="resolving"
                      @click="resolveItems(refsForSeason(g, s.season), 'reject', `${g.title} S${s.season}`)"
                    >
                      Reject season
                    </button>
                  </div>
                  <ul v-if="expanded.has(`${g.folder}|${s.season}`)" class="episodes">
                    <li
                      v-for="e in s.episodes"
                      :key="`${g.folder}|${e.season}|${e.episode}`"
                      class="row episode"
                    >
                      <span class="ep-code mono">
                        S{{ String(e.season).padStart(2, "0") }}E{{ String(e.episode).padStart(2, "0") }}
                      </span>
                      <span v-if="e.already_watched_in_plex" class="badge dim">
                        already watched in Plex
                      </span>
                      <span class="spacer"></span>
                      <button
                        class="primary mini"
                        :disabled="resolving"
                        @click="resolveItems([refForEpisode(g, e)], 'confirm', `${g.title} S${e.season}E${e.episode}`)"
                      >
                        Mark watched
                      </button>
                      <button
                        class="danger mini"
                        :disabled="resolving"
                        @click="resolveItems([refForEpisode(g, e)], 'reject', `${g.title} S${e.season}E${e.episode}`)"
                      >
                        Reject
                      </button>
                      <button
                        class="ghost mini"
                        title="Mute this entry"
                        @click="ignoreItem('pending', { sync_sub: g.sync_sub, folder: g.folder, season: e.season, episode: e.episode }, `${g.title} S${e.season}E${e.episode}`)"
                      >
                        Ignore
                      </button>
                    </li>
                  </ul>
                </li>
              </ul>
            </template>
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
            <button
              class="ghost mini"
              title="Mute this entry"
              @click="ignoreItem('hanging', { path: h.path }, h.rel)"
            >
              Ignore
            </button>
          </li>
        </ul>
      </section>

      <section v-if="ignoredCount > 0" class="panel" data-testid="ignored-section">
        <header>
          <h2>
            <button class="toggle" :aria-expanded="ignoredOpen" @click="ignoredOpen = !ignoredOpen">
              <span class="caret">{{ ignoredOpen ? "▾" : "▸" }}</span>
              Ignored
            </button>
          </h2>
          <span class="dim">{{ ignoredCount }} muted</span>
        </header>
        <div v-if="ignoredOpen">
          <ul v-if="ignoredList.pending.length">
            <li v-for="p in ignoredList.pending" :key="`p-${p.sync_sub}-${p.folder}-${p.season}-${p.episode}`">
              <span class="kind-tag dim">pending</span>
              <span class="name">
                {{ p.folder }}
                <span v-if="p.season !== null && p.episode !== null" class="dim mono">
                  S{{ String(p.season).padStart(2, "0") }}E{{ String(p.episode).padStart(2, "0") }}
                </span>
              </span>
              <span class="spacer"></span>
              <button
                class="ghost mini"
                @click="unignoreItem('pending', p, p.folder)"
              >
                Unignore
              </button>
            </li>
          </ul>
          <ul v-if="ignoredList.watched.length">
            <li v-for="w in ignoredList.watched" :key="`w-${w.lib}-${w.folder}`">
              <span class="kind-tag dim">watched</span>
              <span class="name">{{ w.folder }}</span>
              <span class="lib dim">{{ w.lib }}</span>
              <span class="spacer"></span>
              <button
                class="ghost mini"
                @click="unignoreItem('watched', w, w.folder)"
              >
                Unignore
              </button>
            </li>
          </ul>
          <ul v-if="ignoredList.hanging.length">
            <li v-for="h in ignoredList.hanging" :key="`h-${h.path}`">
              <span class="kind-tag dim">hanging</span>
              <span class="name mono">{{ h.path }}</span>
              <span class="spacer"></span>
              <button
                class="ghost mini"
                @click="unignoreItem('hanging', h, h.path)"
              >
                Unignore
              </button>
            </li>
          </ul>
        </div>
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

/* ── Pending-deletions pane ──────────────────────────────────────────── */

.pending-list { display: flex; flex-direction: column; }
.pending-list > li.group {
  display: block;
  padding: 0;
  border-bottom: 1px solid var(--border);
}
.pending-list > li.group:last-child { border-bottom: none; }

.row {
  display: flex; align-items: center; gap: 0.7rem;
  padding: 0.55rem 1rem;
  font-size: 0.88rem;
}
.row.show-header { font-weight: 500; }
.row.season-header { padding-left: 2rem; background: var(--bg-elev); }
.row.episode { padding-left: 3rem; font-size: 0.83rem; }

.toggle {
  display: inline-flex; align-items: center; gap: 0.4rem;
  background: none; border: none; color: inherit; cursor: pointer;
  padding: 0; font: inherit; text-align: left;
}
.caret { width: 0.9rem; display: inline-block; color: var(--fg-muted); }

.seasons, .episodes { list-style: none; padding: 0; margin: 0; }
.seasons > li.season { border-top: 1px solid var(--border); }

.badge {
  font-size: 0.72rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: var(--bg);
  border: 1px solid var(--border);
}

.ep-code { font-size: 0.78rem; }
.lib { font-size: 0.78rem; }
.season-label { font-weight: 500; }

button.primary {
  background: var(--accent, #2563eb);
  color: white;
  border: 1px solid var(--accent, #2563eb);
  border-radius: 4px;
  cursor: pointer;
}
button.primary:disabled { opacity: 0.5; cursor: not-allowed; }

.kind-tag {
  font-size: 0.7rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  padding: 1px 6px;
  border: 1px solid var(--border);
  border-radius: 4px;
  font-weight: 600;
}
</style>
