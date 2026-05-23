<script setup lang="ts">
import { uncoveredLibraries } from "../store"
</script>

<template>
    <!-- Renders only when at least one library has zero WatchState rows.
         Synclet still works for those libraries via Plex direct read, but the
         user benefits from knowing the round trip differs: no Jellyfin
         aggregation, only Plex-side viewCount. See backend/synclet/watchstate.py
         for the read-source priority. -->
    <div
        v-if="uncoveredLibraries && uncoveredLibraries.length > 0"
        class="banner"
        role="status"
        data-testid="coverage-banner"
    >
        <span class="dot" aria-hidden="true"></span>
        <span class="msg">
            WatchState is not indexing
            <strong>
                <template
                    v-for="(lib, i) in uncoveredLibraries"
                    :key="lib.id"
                >
                    {{ lib.label }}<span
                        v-if="i < uncoveredLibraries.length - 1"
                        >, </span
                    >
                </template>
            </strong>
            , Synclet reads watch state for these from Plex directly.
        </span>
    </div>
</template>

<style scoped>
.banner {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.5rem 1rem;
    background: rgba(255, 196, 0, 0.08);
    border-bottom: 1px solid rgba(255, 196, 0, 0.25);
    color: var(--fg-muted);
    font-size: 0.82rem;
}
.banner strong {
    color: var(--fg);
    font-weight: 600;
}
.dot {
    flex: 0 0 auto;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #ffc400;
    box-shadow: 0 0 8px rgba(255, 196, 0, 0.5);
}
</style>
