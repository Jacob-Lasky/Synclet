import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import SyncedView from "./components/SyncedView.vue"

vi.mock("./api", () => ({
    api: {
        thumbUrl: () => "",
        synced: vi.fn().mockResolvedValue({
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
