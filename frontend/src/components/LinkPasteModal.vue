<script setup lang="ts">
import { onMounted, ref } from "vue"
import { api } from "../api"
import { openDetail, pushToast } from "../store"

const emit = defineEmits<{ (e: "close"): void }>()
const url = ref("")
const submitting = ref(false)
const lastError = ref("")
const input = ref<HTMLInputElement>()

onMounted(() => {
  setTimeout(() => input.value?.focus(), 30)
})

async function submit(): Promise<void> {
  if (!url.value.trim()) return
  submitting.value = true
  lastError.value = ""
  try {
    const r = await api.resolve(url.value.trim())
    if (r.found && r.lib && r.folder) {
      openDetail(r.lib, r.folder)
      emit("close")
    } else {
      lastError.value = reasonLabel(r.reason ?? "no_match")
    }
  } catch (e) {
    lastError.value = (e as Error).message
  } finally {
    submitting.value = false
  }
}

function reasonLabel(r: string): string {
  switch (r) {
    case "empty":                       return "Type or paste something first."
    case "imdb_url_unsupported":        return "IMDb URLs aren't supported. Paste the title instead."
    case "jellyfin_url_unsupported":    return "Jellyfin IDs aren't resolvable. Paste the title instead."
    case "plex_metadata_lookup_failed": return "Plex couldn't find that metadata key."
    case "no_match":                    return "No match in your library."
    default:                            return r
  }
}

function paste(): void {
  navigator.clipboard?.readText().then(t => {
    if (t) {
      url.value = t
      submit()
    }
  }).catch(() => {
    pushToast({ kind: "info", text: "Clipboard not accessible — type or paste manually." })
  })
}
</script>

<template>
  <div class="backdrop" @click.self="emit('close')">
    <div class="modal fade-in">
      <header>
        <h2>Paste a link</h2>
        <button class="ghost mini" @click="emit('close')">×</button>
      </header>
      <p class="help dim">
        Plex web link (best), or just type a title.
        <br/>
        IMDb / Jellyfin URLs not yet resolved — paste the title instead.
      </p>
      <form @submit.prevent="submit">
        <input
          ref="input"
          v-model="url"
          placeholder="https://app.plex.tv/… or “better call saul”"
          autocomplete="off"
          spellcheck="false"
        />
        <div class="actions">
          <button type="button" class="ghost" @click="paste">From clipboard</button>
          <span class="spacer"></span>
          <button type="submit" class="primary" :disabled="submitting || !url.trim()">
            {{ submitting ? "Looking up…" : "Find" }}
          </button>
        </div>
      </form>
      <p v-if="lastError" class="err">{{ lastError }}</p>
    </div>
  </div>
</template>

<style scoped>
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 110;
  background: rgba(0,0,0,0.55);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 8vh 1rem 1rem;
}
.modal {
  width: 520px;
  max-width: 100%;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 1rem 1.2rem 1.2rem;
  box-shadow: var(--shadow);
}
header {
  display: flex; align-items: center; gap: 0.5rem;
}
h2 { margin: 0; font-size: 1.05rem; font-weight: 600; flex: 1; }

.mini { padding: 0.2rem 0.55rem; font-size: 1.2rem; line-height: 1; }
.help { font-size: 0.83rem; margin: 0.4rem 0 0.9rem; line-height: 1.45; }

form input { font-size: 0.95rem; }
.actions { display: flex; gap: 0.5rem; margin-top: 0.75rem; }
.err { margin: 0.75rem 0 0; color: var(--danger); font-size: 0.85rem; }
.spacer { flex: 1; }
</style>
