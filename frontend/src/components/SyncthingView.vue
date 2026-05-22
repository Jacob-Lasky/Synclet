<script setup lang="ts">
import { onActivated, onDeactivated, onMounted, onUnmounted, ref } from "vue"
import { api } from "../api"
import type { SyncthingFolder } from "../types"
import { humanSize } from "../store"

// Poll cadence matches the upstream guidance for /rest/db/status:
// "expensive call, use sparingly". Bumping below 8s without an upstream
// changelog update is the bug; bumping above is fine UX-wise.
const POLL_INTERVAL_MS = 8000

const configured = ref(true)
const folders = ref<SyncthingFolder[]>([])
const loading = ref(true)
const error = ref("")
let pollTimer: ReturnType<typeof setInterval> | null = null

async function load(): Promise<void> {
    error.value = ""
    try {
        const r = await api.syncthingOverview()
        configured.value = r.configured
        folders.value = r.folders
    } catch (e) {
        error.value = (e as Error).message
    } finally {
        loading.value = false
    }
}

function startPolling(): void {
    if (pollTimer != null) return
    load()
    pollTimer = setInterval(load, POLL_INTERVAL_MS)
}

function stopPolling(): void {
    if (pollTimer != null) {
        clearInterval(pollTimer)
        pollTimer = null
    }
}

// onActivated handles tab returns under KeepAlive (App.vue wraps non-library
// views in KeepAlive). onMounted is the fallback for direct mounting (vitest
// mounts components without KeepAlive, so onActivated never fires there).
// Both routes funnel through startPolling, which is idempotent against
// double-fire.
onActivated(startPolling)
onMounted(startPolling)
onDeactivated(stopPolling)
onUnmounted(stopPolling)
</script>

<template>
    <div class="view fade-in">
        <div v-if="!configured" class="info">
            <p><strong>Syncthing not configured.</strong></p>
            <p class="dim">
                Set <code>SYNCTHING_URL</code> and
                <code>SYNCTHING_API_KEY</code> in your <code>.env</code> file to
                enable this panel. See <code>.env.example</code> for the
                template.
            </p>
        </div>
        <div v-else-if="loading && folders.length === 0" class="info">
            Loading Syncthing state…
        </div>
        <div v-else-if="error" class="info err">{{ error }}</div>
        <template v-else>
            <div v-if="folders.length === 0" class="info">
                <p>Connected to Syncthing, but no folders are configured.</p>
            </div>
            <div v-else class="list">
                <div
                    v-for="folder in folders"
                    :key="folder.folder_id"
                    class="folder"
                >
                    <div class="folder-header">
                        <span class="folder-label">{{ folder.label }}</span>
                        <span
                            class="folder-state"
                            :class="`state-${folder.state}`"
                        >
                            {{ folder.state }}
                        </span>
                        <span class="folder-percent"
                            >{{ folder.percent }}%</span
                        >
                    </div>
                    <div class="folder-bytes dim">
                        {{ humanSize(folder.in_sync_bytes) }} of
                        {{ humanSize(folder.global_bytes) }} in sync
                        <span v-if="folder.need_bytes > 0">
                            · {{ humanSize(folder.need_bytes) }} pending
                        </span>
                    </div>
                    <div class="bar">
                        <div
                            class="bar-fill"
                            :style="{ width: folder.percent + '%' }"
                        />
                    </div>

                    <div v-if="folder.devices.length > 0" class="devices">
                        <div
                            v-for="dev in folder.devices"
                            :key="dev.device_id"
                            class="device"
                            :class="{
                                offline: !dev.connected && !dev.paused,
                                paused: dev.paused,
                                'in-sync':
                                    dev.connected &&
                                    !dev.paused &&
                                    dev.completion >= 100,
                            }"
                        >
                            <span class="device-name">{{ dev.name }}</span>
                            <span class="device-completion"
                                >{{ dev.completion }}%</span
                            >
                            <span v-if="dev.paused" class="device-tag"
                                >paused</span
                            >
                            <span v-else-if="!dev.connected" class="device-tag"
                                >offline</span
                            >
                            <span
                                v-else-if="dev.need_bytes > 0"
                                class="device-tag"
                            >
                                {{ humanSize(dev.need_bytes) }} behind
                            </span>
                            <span v-else class="device-tag ok">in sync</span>
                        </div>
                    </div>
                    <div v-else class="info dim no-devices">
                        No remote devices share this folder.
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
}
.info {
    padding: 1.5rem 1rem;
    text-align: center;
    color: var(--fg-muted);
}
.info.err {
    color: var(--accent-danger, #b04040);
}
.info code {
    background: var(--bg-elev-2);
    padding: 0.1em 0.4em;
    border-radius: 3px;
    font-size: 0.9em;
}
.dim {
    color: var(--fg-muted);
}

.list {
    display: flex;
    flex-direction: column;
    gap: 1rem;
}
.folder {
    background: var(--bg-elev-1);
    border-radius: 8px;
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}
.folder-header {
    display: flex;
    align-items: baseline;
    gap: 0.75rem;
}
.folder-label {
    font-weight: 600;
    font-size: 1.05rem;
}
.folder-state {
    padding: 1px 8px;
    border-radius: 10px;
    background: var(--bg-elev-2);
    color: var(--fg-muted);
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
.folder-state.state-syncing {
    background: var(--accent-action, #4080c0);
    color: #fff;
}
.folder-state.state-idle {
    background: var(--bg-elev-2);
}
.folder-state.state-error,
.folder-state.state-unknown {
    background: var(--accent-danger, #b04040);
    color: #fff;
}
.folder-percent {
    margin-left: auto;
    font-variant-numeric: tabular-nums;
    font-weight: 600;
}

.bar {
    height: 6px;
    background: var(--bg-elev-2);
    border-radius: 3px;
    overflow: hidden;
}
.bar-fill {
    height: 100%;
    background: var(--accent-action, #4080c0);
    transition: width 0.4s ease;
}

.devices {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    margin-top: 0.25rem;
}
.device {
    display: flex;
    align-items: baseline;
    gap: 0.65rem;
    padding: 0.35rem 0.6rem;
    background: var(--bg-elev-2);
    border-radius: 6px;
    font-size: 0.92rem;
}
.device.offline {
    opacity: 0.5;
}
.device.paused {
    opacity: 0.65;
    font-style: italic;
}
.device-name {
    font-weight: 500;
    min-width: 5rem;
}
.device-completion {
    font-variant-numeric: tabular-nums;
    color: var(--fg-muted);
    min-width: 3.5rem;
}
.device-tag {
    margin-left: auto;
    font-size: 0.78rem;
    color: var(--fg-muted);
}
.device-tag.ok {
    color: var(--accent-success, #4ca44c);
}
.no-devices {
    font-size: 0.88rem;
    padding: 0.5rem;
}
</style>
