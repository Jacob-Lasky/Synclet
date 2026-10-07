<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue"
import { api } from "../api"
import { humanSize } from "../store"
import type {
    MediaDeleteBody,
    MediaDeletePreview,
    MediaDeleteResult,
} from "../types"

const props = defineProps<{
    body: MediaDeleteBody
    title: string
    scopeLabel: string
}>()
const emit = defineEmits<{
    (e: "close"): void
    (e: "deleted", result: MediaDeleteResult): void
}>()

const dialog = ref<HTMLDialogElement | null>(null)
const cancelButton = ref<HTMLButtonElement | null>(null)
const typedTitle = ref("")
const deleteKey = ref("")
const preview = ref<MediaDeletePreview | null>(null)
const loading = ref(true)
const deleting = ref(false)
const error = ref("")
const canDelete = computed(
    () =>
        typedTitle.value === props.title &&
        deleteKey.value.length > 0 &&
        preview.value !== null
)

onMounted(async () => {
    dialog.value?.showModal()
    await nextTick()
    cancelButton.value?.focus()
    await loadPreview()
})

async function loadPreview(): Promise<void> {
    loading.value = true
    error.value = ""
    preview.value = null
    try {
        preview.value = await api.mediaDeletePreview(props.body)
    } catch (e) {
        error.value = (e as Error).message
    } finally {
        loading.value = false
    }
}

function onCancel(event: Event): void {
    if (deleting.value) event.preventDefault()
}

async function deleteMedia(): Promise<void> {
    if (!canDelete.value || !preview.value || deleting.value) return
    deleting.value = true
    error.value = ""
    try {
        const result = await api.mediaDelete(
            props.body,
            preview.value.fingerprint,
            deleteKey.value
        )
        emit("deleted", result)
    } catch (e) {
        error.value = (e as Error).message
        // A stale file set needs a new preview; a wrong password can be retried.
        if (error.value.startsWith("409 ")) preview.value = null
    } finally {
        deleting.value = false
    }
}
</script>

<template>
    <dialog
        ref="dialog"
        class="delete-dialog"
        role="alertdialog"
        aria-labelledby="delete-title"
        aria-describedby="delete-description"
        @cancel="onCancel"
        @close="emit('close')"
    >
        <div class="dialog-body">
            <p class="eyebrow">Permanent deletion</p>
            <h2 id="delete-title">Delete {{ scopeLabel }}?</h2>
            <p id="delete-description">
                This removes the source media and any offline copies. Syncthing
                will remove those copies from paired devices. Plex will update
                after its next library scan.
            </p>
            <p v-if="loading" class="preview-state">Checking files…</p>
            <div
                v-else-if="preview"
                class="preview"
                data-testid="delete-preview"
            >
                <div>
                    <strong>{{ humanSize(preview.source_bytes) }}</strong>
                    <span>source · {{ preview.source_files }} files</span>
                </div>
                <div>
                    <strong>{{ humanSize(preview.offline_bytes) }}</strong>
                    <span>offline · {{ preview.offline_files }} files</span>
                </div>
            </div>
            <p v-if="error" class="error" role="alert">{{ error }}</p>
            <button
                v-if="!preview && !loading"
                data-testid="preview-again"
                @click="loadPreview"
            >
                Preview again
            </button>
            <label
                v-if="preview"
                class="confirm-label"
                for="confirm-delete-title"
            >
                Type <strong>{{ title }}</strong> to confirm
            </label>
            <input
                v-if="preview"
                id="confirm-delete-title"
                v-model="typedTitle"
                autocomplete="off"
                spellcheck="false"
                :disabled="deleting"
            />
            <label v-if="preview" class="confirm-label" for="delete-key">
                Deletion password
            </label>
            <input
                v-if="preview"
                id="delete-key"
                v-model="deleteKey"
                type="password"
                autocomplete="current-password"
                :disabled="deleting"
            />
            <div class="dialog-actions">
                <button
                    ref="cancelButton"
                    :disabled="deleting"
                    @click="dialog?.close()"
                >
                    Cancel
                </button>
                <button
                    class="danger"
                    data-testid="confirm-delete-media"
                    :disabled="!canDelete || deleting"
                    @click="deleteMedia"
                >
                    {{ deleting ? "Deleting…" : "Delete media" }}
                </button>
            </div>
        </div>
    </dialog>
</template>

<style scoped>
.delete-dialog {
    width: min(480px, calc(100vw - 24px));
    max-height: calc(100dvh - 24px);
    padding: 0;
    color: var(--fg);
    background: var(--bg-elev);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-lg);
    box-shadow: 0 24px 80px rgba(0, 0, 0, 0.6);
}
.delete-dialog::backdrop {
    background: rgba(0, 0, 0, 0.75);
}
.dialog-body {
    padding: 1.5rem;
}
.eyebrow {
    margin: 0 0 0.35rem;
    color: var(--danger);
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
}
h2 {
    margin: 0 0 0.7rem;
    font-size: 1.45rem;
    line-height: 1.2;
}
p {
    color: var(--fg-muted);
    margin: 0 0 1rem;
}
.preview {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.7rem;
    margin: 1rem 0;
}
.preview > div {
    display: flex;
    flex-direction: column;
    padding: 0.85rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--bg-elev-2);
}
.preview strong {
    font-size: 1.1rem;
    color: var(--fg);
}
.preview span {
    font-size: 0.8rem;
    color: var(--fg-muted);
}
.preview-state {
    padding: 0.7rem 0;
}
.error {
    color: var(--danger);
    overflow-wrap: anywhere;
}
.confirm-label {
    display: block;
    margin: 0.9rem 0 0.4rem;
    color: var(--fg-muted);
    font-size: 0.87rem;
}
.confirm-label strong {
    color: var(--fg);
}
.dialog-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.6rem;
    margin-top: 1.3rem;
}
.dialog-actions button {
    min-height: 42px;
}
@media (max-width: 420px) {
    .preview {
        grid-template-columns: 1fr;
    }
    .dialog-actions button {
        flex: 1;
    }
}
</style>
