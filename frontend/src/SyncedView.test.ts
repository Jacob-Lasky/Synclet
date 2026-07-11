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
                    synced_episodes: 8,
                    mtime: 1000,
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
    libraryLabel: (id: string) =>
        ({ tv: "TV", movies: "Movies", youtube: "YouTube" })[id] ?? id,
    openDetail: vi.fn(),
    trackJob: vi.fn(),
    store: { libraries: [{ id: "tv" }, { id: "movies" }, { id: "youtube" }] },
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
                    synced_episodes: 0,
                    mtime: 100,
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

describe("SyncedView episode count label", () => {
    it("leads an episodic title with its downloaded-episode count", async () => {
        const w = mount(SyncedView)
        await flushPromises()
        // Mock humanSize renders bytes as `${n}B`; the label pairs the count.
        expect(w.find(".size-line").text()).toContain(
            "8 episodes (4601871517B)"
        )
    })

    it("uses the singular for a one-episode title", async () => {
        const { api } = await import("./api")
        vi.mocked(api.synced).mockResolvedValueOnce({
            enriched: true,
            items: [
                {
                    title: "After Life",
                    folder: "After Life (2019) {tvdb-347507}",
                    lib: "tv",
                    kind: "show",
                    size_bytes: 500,
                    synced_episodes: 1,
                    mtime: 100,
                    new_unwatched: [],
                },
            ],
        })
        const w = mount(SyncedView)
        await flushPromises()
        expect(w.find(".size-line").text()).toContain("1 episode (500B)")
        expect(w.find(".size-line").text()).not.toContain("1 episodes")
    })

    it("shows only the size for a movie (no episode count)", async () => {
        const { api } = await import("./api")
        vi.mocked(api.synced).mockResolvedValueOnce({
            enriched: true,
            items: [
                {
                    title: "1917",
                    folder: "1917 (2019) {tmdb-3}",
                    lib: "movies",
                    kind: "movie",
                    size_bytes: 700,
                    synced_episodes: 1,
                    mtime: 100,
                    new_unwatched: [],
                },
            ],
        })
        const w = mount(SyncedView)
        await flushPromises()
        const text = w.find(".size-line").text()
        expect(text).toContain("700B")
        expect(text).not.toContain("episode")
    })
})

describe("SyncedView sort + library filter", () => {
    // Three titles across two libraries with intentionally opposed name /
    // recency / size orderings so each sort key produces a distinct sequence.
    const mixed = {
        enriched: true,
        items: [
            {
                title: "Zebra",
                folder: "Zebra",
                lib: "tv",
                kind: "show",
                size_bytes: 100,
                synced_episodes: 2,
                mtime: 300,
                new_unwatched: [],
            },
            {
                title: "Apple",
                folder: "Apple",
                lib: "movies",
                kind: "movie",
                size_bytes: 300,
                synced_episodes: 1,
                mtime: 100,
                new_unwatched: [],
            },
            {
                title: "Mango",
                folder: "Mango",
                lib: "tv",
                kind: "show",
                size_bytes: 200,
                synced_episodes: 5,
                mtime: 200,
                new_unwatched: [],
            },
        ],
    }

    function titlesInOrder(w: ReturnType<typeof mount>): string[] {
        return w.findAll(".row .title").map((n) => n.text())
    }

    it("defaults to alphabetical (Name A-Z)", async () => {
        const { api } = await import("./api")
        vi.mocked(api.synced).mockResolvedValueOnce(mixed)
        const w = mount(SyncedView)
        await flushPromises()
        expect(titlesInOrder(w)).toEqual(["Apple", "Mango", "Zebra"])
    })

    it("sorts by size (largest first) when Size is selected", async () => {
        const { api } = await import("./api")
        vi.mocked(api.synced).mockResolvedValueOnce(mixed)
        const w = mount(SyncedView)
        await flushPromises()
        await w.find('[data-testid="sort-size"]').trigger("click")
        expect(titlesInOrder(w)).toEqual(["Apple", "Mango", "Zebra"])
        // Apple 300 > Mango 200 > Zebra 100.
    })

    it("sorts by recency (newest synced first) when Recent is selected", async () => {
        const { api } = await import("./api")
        vi.mocked(api.synced).mockResolvedValueOnce(mixed)
        const w = mount(SyncedView)
        await flushPromises()
        await w.find('[data-testid="sort-recent"]').trigger("click")
        expect(titlesInOrder(w)).toEqual(["Zebra", "Mango", "Apple"])
        // mtime 300 > 200 > 100.
    })

    it("shows one library pill per present library with a count", async () => {
        const { api } = await import("./api")
        vi.mocked(api.synced).mockResolvedValueOnce(mixed)
        const w = mount(SyncedView)
        await flushPromises()
        expect(w.find('[data-testid="lib-tv"]').text()).toContain("TV")
        expect(w.find('[data-testid="lib-tv"]').text()).toContain("2")
        expect(w.find('[data-testid="lib-movies"]').text()).toContain("Movies")
        expect(w.find('[data-testid="lib-movies"]').text()).toContain("1")
    })

    it("narrows the list to the selected library pill", async () => {
        const { api } = await import("./api")
        vi.mocked(api.synced).mockResolvedValueOnce(mixed)
        const w = mount(SyncedView)
        await flushPromises()
        await w.find('[data-testid="lib-tv"]').trigger("click")
        expect(titlesInOrder(w)).toEqual(["Mango", "Zebra"])
        // Re-toggling clears the filter (empty selection = all).
        await w.find('[data-testid="lib-tv"]').trigger("click")
        expect(titlesInOrder(w)).toEqual(["Apple", "Mango", "Zebra"])
    })

    it("hides the pill row when only one library is present", async () => {
        const { api } = await import("./api")
        const tvOnly = mixed.items
            .filter((it) => it.lib === "tv")
            .map((it, i) => ({ ...it, folder: `tv-${i}` }))
        vi.mocked(api.synced).mockResolvedValueOnce({
            enriched: true,
            items: tvOnly,
        })
        const w = mount(SyncedView)
        await flushPromises()
        // Both are lib=tv, so no pills; the sort control still renders.
        expect(w.find('[data-testid="lib-tv"]').exists()).toBe(false)
        expect(w.find('[data-testid="sort-name"]').exists()).toBe(true)
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
            synced_episodes: 3,
            mtime: 100,
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
