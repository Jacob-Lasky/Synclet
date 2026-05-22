<script setup lang="ts">
import { onMounted, ref } from "vue"
import { api } from "../api"
import type { SyncedEntry } from "../types"
import { humanSize, openDetail, trackJob } from "../store"

const items = ref<SyncedEntry[]>([])
const loading = ref(true)
const error = ref("")
const submitting = ref<Record<string, boolean>>({})

async function load(): Promise<void> {
    loading.value = true
    error.value = ""
    try {
        const r = await api.synced()
        items.value = r.items
    } catch (e) {
        error.value = (e as Error).message
    } finally {
        loading.value = false
    }
}

onMounted(load)

async function syncNew(entry: SyncedEntry, n: number): Promise<void> {
    if (!entry.lib) return
    const eps = entry.new_unwatched
        .slice(0, n)
        .map<[number, number]>((e) => [e.season, e.episode])
    submitting.value[entry.folder] = true
    try {
        const r = await api.sync({
            lib: entry.lib,
            folder: entry.folder,
            selection_type: "episodes",
            episodes: eps,
        })
        if (r.job_id) {
            trackJob(r.job_id, {
                action: "Sync",
                name: entry.title,
                totalMediaFiles: r.total_media_files,
            })
            setTimeout(load, 1500)
        }
    } finally {
        submitting.value[entry.folder] = false
    }
}

function newBytes(entry: SyncedEntry, n: number): number {
    return entry.new_unwatched.slice(0, n).reduce((s, e) => s + e.size_bytes, 0)
}
</script>

<template>
    <div class="view fade-in">
        <div v-if="loading" class="info">Loading synced library…</div>
        <div v-else-if="error" class="info err">{{ error }}</div>
        <template v-else>
            <div v-if="items.length === 0" class="info">
                <p>Nothing synced yet.</p>
                <p class="dim">Pick a title in the Library tab to start.</p>
            </div>

            <div v-else class="list">
                <div v-for="item in items" :key="item.folder" class="row">
                    <div class="thumb-wrap">
                        <img
                            v-if="item.lib"
                            :src="api.thumbUrl(item.lib, item.folder)"
                            :alt="item.title"
                            loading="lazy"
                            @error="
                                (
                                    $event.target as HTMLImageElement
                                ).style.display = 'none'
                            "
                        />
                    </div>
                    <div
                        class="meta-col"
                        @click="item.lib && openDetail(item.lib, item.folder)"
                    >
                        <div class="title-line">
                            <span class="title">{{ item.title }}</span>
                            <span class="lib-tag dim">{{ item.lib }}</span>
                        </div>
                        <div class="size-line">
                            <span>{{ humanSize(item.size_bytes) }}</span>
                            <span
                                v-if="item.new_unwatched.length > 0"
                                class="new"
                            >
                                +{{ item.new_unwatched.length }} new
                            </span>
                        </div>
                    </div>
                    <div class="actions">
                        <button
                            v-if="item.new_unwatched.length > 0"
                            class="primary"
                            :disabled="submitting[item.folder]"
                            @click="
                                syncNew(
                                    item,
                                    Math.min(5, item.new_unwatched.length)
                                )
                            "
                        >
                            Sync next
                            {{ Math.min(5, item.new_unwatched.length) }}
                            <span class="dim small"
                                >({{ humanSize(newBytes(item, 5)) }})</span
                            >
                        </button>
                        <button
                            v-if="item.new_unwatched.length > 5"
                            :disabled="submitting[item.folder]"
                            @click="syncNew(item, item.new_unwatched.length)"
                        >
                            Sync all {{ item.new_unwatched.length }}
                        </button>
                    </div>
                </div>
            </div>
        </template>
    </div>
</template>

<style scoped>
.view {
    padding: 1rem;
    overflow-y: auto;
    flex: 1;
}
.info {
    padding: 3rem 1rem;
    text-align: center;
    color: var(--fg-muted);
}
.info.err {
    color: var(--danger);
}

.list {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    max-width: 900px;
    margin: 0 auto;
}
.row {
    display: flex;
    gap: 0.8rem;
    align-items: center;
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.7rem;
}
.thumb-wrap {
    width: 50px;
    aspect-ratio: 2 / 3;
    background: var(--bg-elev-2);
    border-radius: 4px;
    overflow: hidden;
    flex-shrink: 0;
}
.thumb-wrap img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}
.meta-col {
    flex: 1;
    min-width: 0;
    cursor: pointer;
}
.title-line {
    display: flex;
    gap: 0.5rem;
    align-items: baseline;
    overflow: hidden;
    white-space: nowrap;
}
.title {
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
}
.lib-tag {
    font-size: 0.75rem;
}
.size-line {
    display: flex;
    gap: 0.7rem;
    align-items: center;
    font-size: 0.83rem;
    color: var(--fg-muted);
}
.new {
    color: var(--accent-progress);
    font-weight: 600;
}
.actions {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
    justify-content: flex-end;
}
.actions .small {
    font-size: 0.75rem;
    font-weight: 400;
    margin-left: 4px;
}

@media (max-width: 600px) {
    .row {
        flex-wrap: wrap;
    }
    .actions {
        flex: 1 1 100%;
    }
    .actions button {
        flex: 1;
    }
}
</style>
