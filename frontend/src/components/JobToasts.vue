<script setup lang="ts">
import { store, dismissToast } from "../store"

function pct(done: number, total: number): number {
  if (!total) return 0
  return Math.min(100, Math.round((done / total) * 100))
}
</script>

<template>
  <div class="toasts" :class="{ 'drawer-open': !!store.detail }">
    <transition-group name="toast">
      <div
        v-for="t in store.toasts"
        :key="t.id"
        :class="['toast', t.kind]"
      >
        <div class="row">
          <span v-if="t.kind === 'info'" class="spinner"></span>
          <span v-else-if="t.kind === 'success'" class="ico success">✓</span>
          <span v-else-if="t.kind === 'error'" class="ico error">!</span>
          <span class="text">{{ t.text }}</span>
          <button class="dismiss" @click="dismissToast(t.id)">×</button>
        </div>

        <div v-if="t.jobId && store.jobs[t.jobId] && store.jobs[t.jobId].status === 'running'" class="progress">
          <div
            class="bar"
            :style="{ width: pct(store.jobs[t.jobId].processed_bytes, store.jobs[t.jobId].total_bytes) + '%' }"
          ></div>
        </div>
      </div>
    </transition-group>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  right: 1rem;
  bottom: 1rem;
  z-index: 150;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  max-width: 380px;
  pointer-events: none;
  transition: right 200ms ease, bottom 200ms ease;
}
/* Drawer is 540px pinned to the right on desktop — shift toasts left so they
   don't sit on top of the Sync/Unsync buttons in the drawer's action bar. */
.toasts.drawer-open {
  right: calc(540px + 1rem);
}
.toast {
  background: var(--bg-elev-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.7rem 0.9rem 0.6rem;
  box-shadow: var(--shadow);
  pointer-events: auto;
  font-size: 0.88rem;
}
.toast.info    { border-color: var(--accent-action); }
.toast.success { border-color: var(--accent-watched); }
.toast.error   { border-color: var(--danger); }

.row { display: flex; align-items: center; gap: 0.55rem; }
.text { flex: 1; }
.ico { font-weight: 700; width: 18px; height: 18px; display: inline-flex; align-items: center; justify-content: center; }
.ico.success { color: var(--accent-watched); }
.ico.error   { color: var(--danger); }

.dismiss {
  background: transparent;
  border: none;
  padding: 0 0.3rem;
  font-size: 1.1rem;
  color: var(--fg-dim);
  cursor: pointer;
  border-radius: 4px;
}
.dismiss:hover { background: var(--bg-hover); color: var(--fg); }

.spinner {
  width: 14px; height: 14px;
  border: 2px solid var(--border);
  border-top-color: var(--accent-action);
  border-radius: 50%;
  animation: spin 0.9s linear infinite;
}

.progress {
  margin-top: 0.5rem;
  height: 3px;
  background: var(--bg-elev);
  border-radius: 3px;
  overflow: hidden;
}
.progress .bar {
  height: 100%;
  background: var(--accent-action);
  transition: width 200ms ease;
}

.toast-enter-active, .toast-leave-active { transition: opacity 200ms ease, transform 200ms ease; }
.toast-enter-from { opacity: 0; transform: translateY(10px); }
.toast-leave-to { opacity: 0; transform: translateX(20px); }

@media (max-width: 600px) {
  .toasts { left: 0.6rem; right: 0.6rem; max-width: none; bottom: 0.6rem; }
  /* Mobile drawer is full-screen; lift toasts above the bottom action bar
     instead of trying to shift them sideways. */
  .toasts.drawer-open { left: 0.6rem; right: 0.6rem; bottom: 5.5rem; }
}
@media (min-width: 601px) and (max-width: 720px) {
  /* Drawer is still full-width here — shift the toast to the top edge instead. */
  .toasts.drawer-open { right: 1rem; bottom: auto; top: 4.5rem; }
}
</style>
