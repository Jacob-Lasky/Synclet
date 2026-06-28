import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import SyncedView from "./components/SyncedView.vue"

vi.mock("./api", () => ({
    api: {
        thumbUrl: () => "",
        synced: vi.fn().mockResolvedValue({
            enriched: true,
            items: [
                {
                    title: "After Life",
                    folder: "After Life (2019) {tvdb-347507}",
                    lib: "tv",
                    kind: "show",
                    size_bytes: 4601871517,
                    new_unwatched: [],
                },
            ],
        }),
        unsync: vi.fn().mockResolvedValue({
            job_id: "job1",
            total_files: 18,
            total_media_files: 6,
            total_bytes: 4601871517,
        }),
    },
}))

vi.mock("./store", () => ({
    humanSize: (n: number) => `${n}B`,
    openDetail: vi.fn(),
    trackJob: vi.fn(),
}))

describe("SyncedView unsync button", () => {
    beforeEach(async () => {
        const { api } = await import("./api")
        vi.mocked(api.unsync).mockClear()
    })

    it("renders an Unsync button per synced title", async () => {
        const w = mount(SyncedView)
        await flushPromises()
        const btn = w.find('[data-testid="unsync-title"]')
        expect(btn.exists()).toBe(true)
        expect(btn.text()).toContain("Unsync")
    })

    it("unsyncs the whole title (selection_type=all) after confirm", async () => {
        window.confirm = () => true
        const { api } = await import("./api")
        const w = mount(SyncedView)
        await flushPromises()
        await w.find('[data-testid="unsync-title"]').trigger("click")
        await flushPromises()
        expect(api.unsync).toHaveBeenCalledWith({
            lib: "tv",
            folder: "After Life (2019) {tvdb-347507}",
            selection_type: "all",
        })
    })

    it("hides the Unsync button for stray folders with no library (lib=null)", async () => {
        const { api } = await import("./api")
        vi.mocked(api.synced).mockResolvedValueOnce({
            enriched: true,
            items: [
                {
                    title: "tailscale-cert",
                    folder: "tailscale-cert",
                    lib: null,
                    kind: "unknown",
                    size_bytes: 100,
                    new_unwatched: [],
                },
            ],
        })
        const w = mount(SyncedView)
        await flushPromises()
        expect(w.find('[data-testid="unsync-title"]').exists()).toBe(false)
    })

    it("does nothing when the user cancels the confirm", async () => {
        window.confirm = () => false
        const { api } = await import("./api")
        const w = mount(SyncedView)
        await flushPromises()
        await w.find('[data-testid="unsync-title"]').trigger("click")
        await flushPromises()
        expect(api.unsync).not.toHaveBeenCalled()
    })
})

describe("SyncedView two-phase enrichment", () => {
    afterEach(() => {
        vi.useRealTimers()
    })

    it("shows the list immediately, then fills badges after a silent re-poll", async () => {
        const { api } = await import("./api")
        const base = {
            title: "After Life",
            folder: "After Life (2019) {tvdb-347507}",
            lib: "tv",
            kind: "show" as const,
            size_bytes: 100,
        }
        vi.mocked(api.synced)
            .mockReset()
            // Phase 1: list present, enrichment not yet built.
            .mockResolvedValueOnce({
                enriched: false,
                items: [{ ...base, new_unwatched: [] }],
            })
            // Phase 2 (after the re-poll): badges land.
            .mockResolvedValueOnce({
                enriched: true,
                items: [
                    {
                        ...base,
                        new_unwatched: [
                            {
                                season: 1,
                                episode: 2,
                                title: "Ep2",
                                size_bytes: 50,
                            },
                        ],
                    },
                ],
            })

        vi.useFakeTimers()
        const w = mount(SyncedView)
        await flushPromises()

        // The list paints immediately; the enrichment indicator shows; no badge.
        expect(w.find('[data-testid="unsync-title"]').exists()).toBe(true)
        expect(w.find('[data-testid="synced-enriching"]').exists()).toBe(true)
        expect(w.find(".new").exists()).toBe(false)

        // The silent re-poll fires after the enrichment poll interval.
        await vi.advanceTimersByTimeAsync(4000)
        await flushPromises()

        expect(api.synced).toHaveBeenCalledTimes(2)
        expect(w.find('[data-testid="synced-enriching"]').exists()).toBe(false)
        expect(w.find(".new").exists()).toBe(true)
        expect(w.text()).toContain("+1 new")
    })

    it("stops polling once enrichment has landed", async () => {
        const { api } = await import("./api")
        vi.mocked(api.synced)
            .mockReset()
            .mockResolvedValue({ enriched: true, items: [] })

        vi.useFakeTimers()
        mount(SyncedView)
        await flushPromises()
        expect(api.synced).toHaveBeenCalledTimes(1)

        // No further polls scheduled when already enriched.
        await vi.advanceTimersByTimeAsync(20000)
        await flushPromises()
        expect(api.synced).toHaveBeenCalledTimes(1)
    })
})
