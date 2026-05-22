import { describe, expect, it, vi } from "vitest"
import { nextTick } from "vue"
import { mount } from "@vue/test-utils"
import MaintenanceView from "./components/MaintenanceView.vue"

// Each describe gets a fresh mock module so we can inspect the wire shape the
// component sends to api.maintIgnore. Hoisted so vi.mock() can see it.
const { maintIgnoreMock, maintUnignoreMock } = vi.hoisted(() => ({
    maintIgnoreMock: vi.fn().mockResolvedValue({ ok: true }),
    maintUnignoreMock: vi.fn().mockResolvedValue({ ok: true }),
}))

vi.mock("./api", () => ({
    api: {
        maintWatched: () =>
            Promise.resolve({
                items: [
                    {
                        title: "Old Movie",
                        lib: "movies",
                        folder: "Old Movie (1999)",
                        files: ["/data/Old Movie (1999)/movie.mkv"],
                        size_bytes: 100,
                        file_count: 1,
                    },
                ],
            }),
        maintHanging: () =>
            Promise.resolve({
                items: [
                    {
                        path: "/data/orphan.mkv",
                        rel: "orphan.mkv",
                        size_bytes: 50,
                    },
                ],
            }),
        maintIgnored: () =>
            Promise.resolve({
                version: 1,
                pending: [],
                watched: [],
                hanging: [],
            }),
        maintIgnore: maintIgnoreMock,
        maintUnignore: maintUnignoreMock,
        maintPending: () =>
            Promise.resolve({
                items: [
                    {
                        sync_sub: "movies",
                        folder: "Synclet Ship Test Movie (2099) {tmdb-0}",
                        title: "Synclet Ship Test Movie (2099)",
                        kind: "movie",
                        lib: null,
                        rating_key: null,
                        already_watched_in_plex: false,
                    },
                    {
                        sync_sub: "tv",
                        folder: "Synclet Ship Test Show (2099) {tvdb-0}",
                        title: "Synclet Ship Test Show (2099)",
                        kind: "show",
                        lib: null,
                        rating_key: null,
                        seasons: [
                            {
                                season: 1,
                                episodes: [
                                    {
                                        season: 1,
                                        episode: 1,
                                        already_watched_in_plex: true,
                                        episode_rating_key: null,
                                        title: "",
                                    },
                                    {
                                        season: 1,
                                        episode: 2,
                                        already_watched_in_plex: false,
                                        episode_rating_key: null,
                                        title: "",
                                    },
                                ],
                            },
                        ],
                    },
                ],
            }),
        maintResolve: vi.fn().mockResolvedValue({
            results: [],
            cleanup: { removed_files: 0, removed_dirs: 0 },
        }),
    },
}))

async function mountWaited(): Promise<ReturnType<typeof mount>> {
    const wrapper = mount(MaintenanceView)
    await new Promise((r) => setTimeout(r, 50))
    await nextTick()
    await nextTick()
    return wrapper
}

describe("MaintenanceView pending pane visual artifact", () => {
    it("renders the new pending-deletions pane with movie and show groups", async () => {
        const wrapper = await mountWaited()
        const html = wrapper.html()

        expect(html).toContain("Deleted, awaiting Plex sync")
        expect(html).toContain("Synclet Ship Test Movie")
        expect(html).toContain("Synclet Ship Test Show")
        expect(html).toContain("Mark all watched")
        expect(html).toContain("Reject all")
        expect(html).toContain("Mark watched")
        expect(html).toContain("Reject")
        expect(html).toContain('data-testid="pending-deletions"')
        // Ignore affordance present on the movie row.
        expect(html).toContain("Ignore")
    })
})

// Each test calls a single Ignore button and asserts the wire body shape the
// frontend sent. The backend route branches on `kind`, so getting the union
// shape wrong here is the kind of bug that ships silently — no rendering
// difference, just a 422 at click time. These contract tests pin the shape.
describe("MaintenanceView ignore-button wire contract", () => {
    it("sends a watched-kind IgnoreOp when clicking Ignore on a watched row", async () => {
        maintIgnoreMock.mockClear()
        const wrapper = await mountWaited()

        // Find the watched-pane Ignore button. Watched panel comes first, so
        // the first button labeled "Ignore" inside it is the watched one.
        const watchedSection = wrapper
            .findAll("section")
            .find((s) => s.html().includes("Watched in synced-media"))
        expect(watchedSection).toBeDefined()
        const ignoreBtn = watchedSection!
            .findAll("button")
            .find((b) => b.text() === "Ignore")
        expect(ignoreBtn).toBeDefined()
        await ignoreBtn!.trigger("click")

        expect(maintIgnoreMock).toHaveBeenCalledTimes(1)
        expect(maintIgnoreMock).toHaveBeenCalledWith({
            kind: "watched",
            ref: { lib: "movies", folder: "Old Movie (1999)" },
        })
    })

    it("sends a hanging-kind IgnoreOp when clicking Ignore on a hanging row", async () => {
        maintIgnoreMock.mockClear()
        const wrapper = await mountWaited()

        const hangingSection = wrapper
            .findAll("section")
            .find((s) => s.html().includes("Hanging files"))
        expect(hangingSection).toBeDefined()
        const ignoreBtn = hangingSection!
            .findAll("button")
            .find((b) => b.text() === "Ignore")
        expect(ignoreBtn).toBeDefined()
        await ignoreBtn!.trigger("click")

        expect(maintIgnoreMock).toHaveBeenCalledTimes(1)
        expect(maintIgnoreMock).toHaveBeenCalledWith({
            kind: "hanging",
            ref: { path: "/data/orphan.mkv" },
        })
    })

    it("sends a pending-kind IgnoreOp with null season/episode for a movie row", async () => {
        maintIgnoreMock.mockClear()
        const wrapper = await mountWaited()

        const pendingSection = wrapper.find('[data-testid="pending-deletions"]')
        expect(pendingSection.exists()).toBe(true)
        // Movie row Ignore button (the show row uses a Mark-all/Reject-all
        // header without an Ignore at that level).
        const ignoreBtn = pendingSection
            .findAll("button")
            .find((b) => b.text() === "Ignore")
        expect(ignoreBtn).toBeDefined()
        await ignoreBtn!.trigger("click")

        expect(maintIgnoreMock).toHaveBeenCalledTimes(1)
        expect(maintIgnoreMock).toHaveBeenCalledWith({
            kind: "pending",
            ref: {
                sync_sub: "movies",
                folder: "Synclet Ship Test Movie (2099) {tmdb-0}",
                season: null,
                episode: null,
            },
        })
    })
})
