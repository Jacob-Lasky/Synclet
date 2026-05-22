import { describe, expect, it, beforeEach } from "vitest"
import { mount } from "@vue/test-utils"
import { nextTick } from "vue"
import JobToasts from "./components/JobToasts.vue"
import { store } from "./store"
import type { Job } from "./types"

// JobToasts' progress bar branches on (a) toast.jobId being present, (b) the
// referenced job existing in store.jobs, and (c) job.status === "running". The
// runningJob/jobWidth helpers collapse those three conditions; these tests
// pin the visible behavior for each branch so the helpers can't drift silently.

function makeJob(overrides: Partial<Job> = {}): Job {
    return {
        id: "job-1",
        op: "sync",
        status: "running",
        total_files: 10,
        total_media_files: 8,
        processed_files: 5,
        processed_media_files: 4,
        processed_bytes: 500,
        total_bytes: 1000,
        current_file: "movie.mkv",
        title: "Test",
        started_at: 0,
        ended_at: 0,
        error: "",
        ...overrides,
    }
}

describe("JobToasts progress bar", () => {
    beforeEach(() => {
        // Reset the reactive store between tests so toasts/jobs don't bleed.
        store.toasts = []
        store.jobs = {}
    })

    it("renders the progress bar with width % for a running tracked job", async () => {
        store.jobs["job-1"] = makeJob()
        store.toasts.push({
            id: "t1",
            kind: "info",
            text: "Syncing…",
            jobId: "job-1",
        })

        const wrapper = mount(JobToasts)
        await nextTick()

        const bar = wrapper.find(".progress .bar")
        expect(bar.exists()).toBe(true)
        // 500 / 1000 = 50%
        expect(bar.attributes("style")).toContain("width: 50%")
    })

    it("does not render the progress bar for a done job", async () => {
        store.jobs["job-2"] = makeJob({ id: "job-2", status: "done" })
        store.toasts.push({
            id: "t2",
            kind: "success",
            text: "Done",
            jobId: "job-2",
        })

        const wrapper = mount(JobToasts)
        await nextTick()

        expect(wrapper.find(".progress").exists()).toBe(false)
    })

    it("does not render the progress bar for a toast with no jobId", async () => {
        store.toasts.push({
            id: "t3",
            kind: "error",
            text: "Bad day",
        })

        const wrapper = mount(JobToasts)
        await nextTick()

        expect(wrapper.find(".progress").exists()).toBe(false)
    })

    it("does not render the progress bar when the job is missing from the store", async () => {
        // toast points at a job that was never registered (e.g., the job
        // record got pruned but the toast is still alive).
        store.toasts.push({
            id: "t4",
            kind: "info",
            text: "Stale",
            jobId: "ghost-job",
        })

        const wrapper = mount(JobToasts)
        await nextTick()

        expect(wrapper.find(".progress").exists()).toBe(false)
    })

    it("clamps the width to 100% when processed > total", async () => {
        // Defensive against a backend that briefly reports processed > total
        // (rounding, post-completion job update). Should not produce width:120%.
        store.jobs["job-3"] = makeJob({
            id: "job-3",
            processed_bytes: 1200,
            total_bytes: 1000,
        })
        store.toasts.push({
            id: "t5",
            kind: "info",
            text: "Syncing…",
            jobId: "job-3",
        })

        const wrapper = mount(JobToasts)
        await nextTick()

        const bar = wrapper.find(".progress .bar")
        expect(bar.attributes("style")).toContain("width: 100%")
    })

    it("renders width 0% when total_bytes is zero (avoids NaN)", async () => {
        store.jobs["job-4"] = makeJob({
            id: "job-4",
            processed_bytes: 0,
            total_bytes: 0,
        })
        store.toasts.push({
            id: "t6",
            kind: "info",
            text: "Syncing…",
            jobId: "job-4",
        })

        const wrapper = mount(JobToasts)
        await nextTick()

        const bar = wrapper.find(".progress .bar")
        expect(bar.attributes("style")).toContain("width: 0%")
    })
})
