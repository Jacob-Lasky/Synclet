<script setup lang="ts">
import type { SubtitleCheck } from "../types"

defineProps<{ check: SubtitleCheck; required: boolean }>()
defineEmits<{ required: [value: boolean] }>()

const labels: Record<string, string> = {
    required_forced_missing: "English forced subtitles expected but missing",
    forced_sidecar_available: "English forced subtitle file found",
    embedded_forced_reported: "Plex reports embedded English forced subtitles",
    no_forced_track_reported: "No forced subtitle track reported; need unknown",
    tracks_unknown: "Subtitle track information unavailable; need unknown",
}
</script>

<template>
    <section aria-label="Subtitle checks" class="subtitle-check">
        <h3>Subtitles</h3>
        <div v-for="video in check.videos" :key="video.video">
            <p
                :role="
                    video.status === 'required_forced_missing'
                        ? 'alert'
                        : undefined
                "
            >
                {{ labels[video.status] }}
            </p>
            <p
                v-for="sidecar in video.sidecars"
                :key="sidecar.name"
                class="dim"
            >
                {{ sidecar.name }}:
                {{
                    !sidecar.source_exists
                        ? "source file missing"
                        : sidecar.offline === "verified"
                          ? "offline copy verified"
                          : "offline copy not verified"
                }}
            </p>
        </div>
        <p class="dim">
            Track information can be stale. Forced dialogue may be burned into
            the video; whether subtitles are needed is unknown.
        </p>
        <label>
            <input
                type="checkbox"
                :checked="required"
                @change="
                    $emit(
                        'required',
                        ($event.target as HTMLInputElement).checked
                    )
                "
            />
            Require English forced subtitles for this sync
        </label>
    </section>
</template>

<style scoped>
.subtitle-check {
    padding: 0.75rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow-wrap: anywhere;
}
h3,
p {
    margin: 0 0 0.5rem;
}
p,
label {
    font-size: 0.85rem;
}
[role="alert"] {
    color: var(--danger);
}
</style>
