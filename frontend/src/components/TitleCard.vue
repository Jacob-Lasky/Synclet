<script setup lang="ts">
import { ref } from "vue"
import { api } from "../api"
import { libraryShort } from "../store"
import type { Title } from "../types"

const props = defineProps<{ title: Title }>()
defineEmits<{ (e: "open", lib: string, folder: string): void }>()

const thumbErrored = ref(false)
const thumbUrl = api.thumbUrl(props.title.lib, props.title.folder)
</script>

<template>
    <button class="card" @click="$emit('open', title.lib, title.folder)">
        <div class="poster">
            <img
                v-if="!thumbErrored"
                :src="thumbUrl"
                :alt="title.name"
                loading="lazy"
                decoding="async"
                @error="thumbErrored = true"
            />
            <div v-else class="poster-fallback">
                <span>{{ title.name.slice(0, 2).toUpperCase() }}</span>
            </div>

            <div class="lib-badge">{{ libraryShort(title.lib) }}</div>

            <div class="badges">
                <div
                    v-if="title.synced_pct > 0"
                    class="badge sync"
                    :title="`Synced ${title.synced_pct}%`"
                >
                    <svg
                        width="11"
                        height="11"
                        viewBox="0 0 24 24"
                        fill="currentColor"
                    >
                        <path d="M12 16l-6-6h12z" />
                    </svg>
                    <span v-if="title.kind !== 'movie'"
                        >{{ title.synced_pct }}%</span
                    >
                </div>
                <div
                    v-if="title.watched_pct >= 100"
                    class="badge watched"
                    title="Watched"
                >
                    <svg
                        width="11"
                        height="11"
                        viewBox="0 0 24 24"
                        fill="currentColor"
                    >
                        <path d="M9 16.2L4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z" />
                    </svg>
                </div>
                <div
                    v-else-if="title.watched_pct > 0"
                    class="badge watching"
                    :title="`Watching · ${title.watched_pct}%`"
                >
                    {{ title.watched_pct }}%
                </div>
            </div>

            <div class="gradient"></div>
        </div>

        <div class="info">
            <div class="name" :title="title.name">{{ title.name }}</div>
            <div class="meta">
                <span v-if="title.year">{{ title.year }}</span>
                <span v-if="title.ep_count" class="dim"
                    >{{ title.ep_count }} ep</span
                >
            </div>
        </div>
    </button>
</template>

<style scoped>
.card {
    text-align: left;
    background: transparent;
    border: none;
    padding: 0;
    border-radius: var(--radius);
    cursor: pointer;
    transition:
        transform 140ms ease,
        box-shadow 140ms ease;
}
.card:hover {
    transform: translateY(-3px);
}
.card:hover .poster {
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
}

.poster {
    position: relative;
    width: 100%;
    aspect-ratio: 2 / 3;
    background: var(--bg-elev);
    border-radius: var(--radius);
    overflow: hidden;
    border: 1px solid var(--border);
    transition:
        box-shadow 140ms ease,
        border-color 140ms ease;
}
.poster img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    background: var(--bg-elev);
}
.poster-fallback {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, #1a2030, #2a3349);
    color: var(--fg-dim);
    font-size: 2rem;
    font-weight: 700;
    letter-spacing: 0.02em;
}

.lib-badge {
    position: absolute;
    top: 6px;
    left: 6px;
    padding: 2px 6px;
    background: rgba(0, 0, 0, 0.6);
    color: var(--fg);
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    border-radius: 4px;
    backdrop-filter: blur(8px);
}

.badges {
    position: absolute;
    top: 6px;
    right: 6px;
    display: flex;
    gap: 4px;
}
.badge {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    background: rgba(0, 0, 0, 0.7);
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 700;
    backdrop-filter: blur(8px);
}
.badge.sync {
    color: var(--accent-sync);
}
.badge.watched {
    color: var(--accent-watched);
}
.badge.watching {
    color: var(--accent-progress);
}

.gradient {
    position: absolute;
    inset: auto 0 0 0;
    height: 50%;
    background: linear-gradient(to bottom, transparent, rgba(0, 0, 0, 0.75));
    pointer-events: none;
}

.info {
    padding: 8px 4px 4px;
}
.name {
    font-size: 0.92rem;
    font-weight: 500;
    line-height: 1.25;
    overflow: hidden;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    margin-bottom: 2px;
    /* Reserve two lines of space even for single-line names so the .info
     section has a constant height. Without this, a one-line name shrinks
     the card and the grid row's align-items: stretch behavior pushes
     short-name posters down a few pixels relative to two-line neighbours. */
    min-height: calc(2 * 1.25em);
}
.meta {
    font-size: 0.75rem;
    color: var(--fg-muted);
    display: flex;
    gap: 0.5rem;
}
</style>
