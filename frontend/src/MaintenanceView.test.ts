import { beforeEach, describe, expect, it, vi } from "vitest"
import { nextTick } from "vue"
import { mount } from "@vue/test-utils"
import MaintenanceView from "./components/MaintenanceView.vue"

// Hoisted mocks let individual tests rebind per-call behavior (resolve returns
// scrobble_failed, maintWatched returns an updated set after refetch, etc.)
// without redefining the whole api module per describe block.
const {
    maintIgnoreMock,
    maintUnignoreMock,
    maintResolveMock,
    maintWatchedMock,
    maintHangingMock,
    maintPendingMock,
    maintIgnoredMock,
    maintRemoveMock,
    maintCountsMock,
} = vi.hoisted(() => {
    const initialWatched = [
        {
            title: "Old Movie",
            lib: "movies",
            folder: "Old Movie (1999)",
            files: ["/data/Old Movie (1999)/movie.mkv"],
            size_bytes: 100,
            file_count: 1,
        },
    ]
    const initialHanging = [
        {
            path: "/data/orphan.mkv",
            rel: "orphan.mkv",
            size_bytes: 50,
        },
    ]
    const initialPending = [
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
    ]
    const initialIgnored = {
        version: 1,
        pending: [],
        watched: [],
        hanging: [],
    }
    return {
        maintIgnoreMock: vi.fn().mockResolvedValue({ ok: true }),
        maintUnignoreMock: vi.fn().mockResolvedValue({ ok: true }),
        maintResolveMock: vi.fn().mockResolvedValue({
            results: [],
            cleanup: { removed_files: 0, removed_dirs: 0 },
        }),
        maintRemoveMock: vi.fn().mockResolvedValue({
            removed: 0,
            bytes_freed: 0,
            cleanup: { removed_files: 0, removed_dirs: 0 },
        }),
        maintWatchedMock: vi.fn().mockResolvedValue({ items: initialWatched }),
        maintHangingMock: vi.fn().mockResolvedValue({ items: initialHanging }),
        maintPendingMock: vi.fn().mockResolvedValue({ items: initialPending }),
        maintIgnoredMock: vi.fn().mockResolvedValue(initialIgnored),
        maintCountsMock: vi.fn().mockResolvedValue({
            watched_titles: 0,
            hanging_files: 0,
            pending_items: 0,
            total: 0,
        }),
    }
})

vi.mock("./api", () => ({
    api: {
        maintWatched: maintWatchedMock,
        maintHanging: maintHangingMock,
        maintIgnored: maintIgnoredMock,
        maintPending: maintPendingMock,
        maintIgnore: maintIgnoreMock,
        maintUnignore: maintUnignoreMock,
        maintResolve: maintResolveMock,
        maintRemove: maintRemoveMock,
        maintCounts: maintCountsMock,
        state: vi.fn().mockResolvedValue({
            titles: [],
            disk: null,
            libraries: [],
        }),
    },
}))

// confirm() prompts default to NOT confirming under happy-dom, which would
// short-circuit the resolve/remove paths. Override per-suite so the actual
// optimistic mutate runs.
beforeEach(() => {
    window.confirm = () => true
})

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

// The clunkiness Jake reported was the maintenance pane collapsing into the
// "Scanning…" overlay every time the user clicked Mark-watched or Reject on a
// pending item. The cause was load() setting loading.value = true after each
// action, which the template's v-if at line 409 of MaintenanceView.vue hides
// the whole pane on. These tests pin the fix: resolveItems is now optimistic
// and never re-triggers load(), so the "Scanning…" string must not reappear
// in the DOM after a click, and the pending list must shrink synchronously.
describe("MaintenanceView optimistic resolveItems", () => {
    it("removes a pending movie instantly on Mark watched, pane stays mounted", async () => {
        maintResolveMock.mockClear()
        maintResolveMock.mockResolvedValueOnce({
            results: [
                {
                    sync_sub: "movies",
                    folder: "Synclet Ship Test Movie (2099) {tmdb-0}",
                    season: null,
                    episode: null,
                    status: "ok",
                },
            ],
            cleanup: { removed_files: 0, removed_dirs: 0 },
        })
        const wrapper = await mountWaited()

        // Sanity: both pending entries are in the DOM at start.
        expect(wrapper.html()).toContain("Synclet Ship Test Movie")
        expect(wrapper.html()).toContain("Synclet Ship Test Show")

        const pendingSection = wrapper.find('[data-testid="pending-deletions"]')
        const markBtn = pendingSection
            .findAll("button")
            .find((b) => b.text() === "Mark watched")
        expect(markBtn).toBeDefined()
        await markBtn!.trigger("click")
        // Allow microtasks (the optimistic mutate runs sync before the await
        // on maintResolve resolves) to flush.
        await nextTick()

        // Movie row is gone, show row still present, "Scanning…" never reappears.
        const html = wrapper.html()
        expect(html).not.toContain("Synclet Ship Test Movie")
        expect(html).toContain("Synclet Ship Test Show")
        expect(html).not.toContain("Scanning")
        expect(maintResolveMock).toHaveBeenCalledTimes(1)
    })

    it("removes a pending movie instantly on Reject, pane stays mounted", async () => {
        maintResolveMock.mockClear()
        maintResolveMock.mockResolvedValueOnce({
            results: [
                {
                    sync_sub: "movies",
                    folder: "Synclet Ship Test Movie (2099) {tmdb-0}",
                    season: null,
                    episode: null,
                    status: "rejected",
                },
            ],
            cleanup: { removed_files: 0, removed_dirs: 0 },
        })
        const wrapper = await mountWaited()

        const pendingSection = wrapper.find('[data-testid="pending-deletions"]')
        const rejectBtn = pendingSection
            .findAll("button")
            .find((b) => b.text() === "Reject")
        expect(rejectBtn).toBeDefined()
        await rejectBtn!.trigger("click")
        await nextTick()

        const html = wrapper.html()
        expect(html).not.toContain("Synclet Ship Test Movie")
        expect(html).not.toContain("Scanning")
        expect(maintResolveMock).toHaveBeenCalledTimes(1)
        // Wire shape: action = "reject", items contains the movie's
        // (sync_sub, folder) tuple.
        expect(maintResolveMock).toHaveBeenCalledWith(
            [
                {
                    sync_sub: "movies",
                    folder: "Synclet Ship Test Movie (2099) {tmdb-0}",
                },
            ],
            "reject"
        )
    })

    it("rolls back to pre-click state when the resolve API throws", async () => {
        maintResolveMock.mockClear()
        maintResolveMock.mockRejectedValueOnce(new Error("boom"))
        const wrapper = await mountWaited()

        const pendingSection = wrapper.find('[data-testid="pending-deletions"]')
        const markBtn = pendingSection
            .findAll("button")
            .find((b) => b.text() === "Mark watched")
        await markBtn!.trigger("click")
        // Microtask + macrotask flush so the catch branch's rollback runs.
        await nextTick()
        await new Promise((r) => setTimeout(r, 10))
        await nextTick()

        // Optimistic removal undone: the movie reappears.
        const html = wrapper.html()
        expect(html).toContain("Synclet Ship Test Movie")
        expect(html).not.toContain("Scanning")
    })
})

// The unignore flow previously hard-reloaded the entire maintenance pane to
// reconstruct the entry it didn't have local data for (see the legacy comment
// at MaintenanceView.vue:186 before this refactor). The cache lets the round
// trip stay optimistic: ignore stashes the entry, unignore reads it back. On
// cross-session unignore (cache miss because the entry was ignored in a
// previous session), the component falls back to a focused refetch of the
// source list ONLY, never the full load() that gates the pane on
// loading.value. These tests pin both branches.
describe("MaintenanceView optimistic unignoreItem", () => {
    it("ignore-then-unignore round-trip restores the entry without a refetch", async () => {
        maintIgnoreMock.mockClear()
        maintUnignoreMock.mockClear()
        maintWatchedMock.mockClear()
        const wrapper = await mountWaited()

        // load() called maintWatched once on mount. Capture that baseline so
        // we can assert no additional call happens during the round-trip.
        const baselineMaintWatched = maintWatchedMock.mock.calls.length

        // Ignore the watched row.
        const watchedSection = wrapper
            .findAll("section")
            .find((s) => s.html().includes("Watched in synced-media"))
        const ignoreBtn = watchedSection!
            .findAll("button")
            .find((b) => b.text() === "Ignore")
        await ignoreBtn!.trigger("click")
        await nextTick()
        await new Promise((r) => setTimeout(r, 10))
        await nextTick()

        // Entry moved from Watched to Ignored.
        expect(maintIgnoreMock).toHaveBeenCalledTimes(1)
        let html = wrapper.html()
        expect(html).toContain('data-testid="ignored-section"')

        // Open the ignored section so the Unignore button is in the DOM.
        const ignoredHeaderBtn = wrapper
            .find('[data-testid="ignored-section"]')
            .findAll("button")
            .find((b) => b.text().includes("Ignored"))
        await ignoredHeaderBtn!.trigger("click")
        await nextTick()

        // Click Unignore.
        const unignoreBtn = wrapper
            .find('[data-testid="ignored-section"]')
            .findAll("button")
            .find((b) => b.text() === "Unignore")
        expect(unignoreBtn).toBeDefined()
        await unignoreBtn!.trigger("click")
        await nextTick()
        await new Promise((r) => setTimeout(r, 10))
        await nextTick()

        // Cache hit: maintWatched count must NOT have grown beyond the load()
        // baseline. The restore happened from the local cache, not via a
        // server round-trip.
        expect(maintWatchedMock.mock.calls.length).toBe(baselineMaintWatched)
        expect(maintUnignoreMock).toHaveBeenCalledTimes(1)

        // Original watched entry is back in the source list and the ignored
        // section is empty (so the v-if gates it out).
        html = wrapper.html()
        expect(html).toContain("Old Movie")
        expect(html).not.toContain("Scanning")
    })

    it("cross-session unignore triggers a focused refetch (not full load)", async () => {
        // Simulate the "ignored in a previous session" case: the ignored list
        // is pre-populated at mount but the entry was never cached locally.
        maintIgnoredMock.mockResolvedValueOnce({
            version: 1,
            pending: [],
            watched: [{ lib: "movies", folder: "Some Other Movie (1980)" }],
            hanging: [],
        })
        maintUnignoreMock.mockClear()
        maintWatchedMock.mockClear()
        maintHangingMock.mockClear()
        maintPendingMock.mockClear()
        maintIgnoredMock.mockClear()

        const wrapper = await mountWaited()

        // Initial mount fires each list endpoint once.
        const baselineWatched = maintWatchedMock.mock.calls.length
        const baselineHanging = maintHangingMock.mock.calls.length
        const baselinePending = maintPendingMock.mock.calls.length
        const baselineIgnored = maintIgnoredMock.mock.calls.length

        // Open the ignored section.
        const ignoredHeaderBtn = wrapper
            .find('[data-testid="ignored-section"]')
            .findAll("button")
            .find((b) => b.text().includes("Ignored"))
        await ignoredHeaderBtn!.trigger("click")
        await nextTick()

        // Click Unignore on the cross-session entry.
        const unignoreBtn = wrapper
            .find('[data-testid="ignored-section"]')
            .findAll("button")
            .find((b) => b.text() === "Unignore")
        expect(unignoreBtn).toBeDefined()
        await unignoreBtn!.trigger("click")
        await nextTick()
        await new Promise((r) => setTimeout(r, 10))
        await nextTick()

        expect(maintUnignoreMock).toHaveBeenCalledTimes(1)
        // Cache miss path: maintWatched fires exactly once more (the focused
        // refetch). The other source-list endpoints stay flat. The full
        // load() would have fired ALL four, so this asserts the cheap path.
        expect(maintWatchedMock.mock.calls.length).toBe(baselineWatched + 1)
        expect(maintHangingMock.mock.calls.length).toBe(baselineHanging)
        expect(maintPendingMock.mock.calls.length).toBe(baselinePending)
        expect(maintIgnoredMock.mock.calls.length).toBe(baselineIgnored)
        expect(wrapper.html()).not.toContain("Scanning")
    })

    it("preserves the cache entry on a failed unignore so retry stays fast", async () => {
        // Regression: if restoreIgnoredEntry deleted the cache entry inside
        // mutate(), a thrown API call would rollback the lists but leak the
        // cache entry, downgrading the retry to a focused-refetch path. The
        // fix moves the delete into the onResult success branch.
        maintIgnoreMock.mockClear()
        maintUnignoreMock.mockClear()
        maintWatchedMock.mockClear()
        const wrapper = await mountWaited()
        const baselineMaintWatched = maintWatchedMock.mock.calls.length

        // Ignore a watched entry to populate the cache.
        const watchedSection = wrapper
            .findAll("section")
            .find((s) => s.html().includes("Watched in synced-media"))
        const ignoreBtn = watchedSection!
            .findAll("button")
            .find((b) => b.text() === "Ignore")
        await ignoreBtn!.trigger("click")
        await nextTick()
        await new Promise((r) => setTimeout(r, 10))
        await nextTick()

        // Open ignored section and click Unignore with the API set to fail.
        const ignoredHeaderBtn = wrapper
            .find('[data-testid="ignored-section"]')
            .findAll("button")
            .find((b) => b.text().includes("Ignored"))
        await ignoredHeaderBtn!.trigger("click")
        await nextTick()

        maintUnignoreMock.mockRejectedValueOnce(new Error("server down"))
        const unignoreBtn = wrapper
            .find('[data-testid="ignored-section"]')
            .findAll("button")
            .find((b) => b.text() === "Unignore")
        await unignoreBtn!.trigger("click")
        await nextTick()
        await new Promise((r) => setTimeout(r, 10))
        await nextTick()

        // Retry: this should still hit the cache fast path (no extra
        // maintWatched call beyond the load() baseline).
        maintUnignoreMock.mockResolvedValueOnce({ ok: true })
        // Need to re-open ignored section since the rollback restored it.
        const retryBtn = wrapper
            .find('[data-testid="ignored-section"]')
            .findAll("button")
            .find((b) => b.text() === "Unignore")
        expect(retryBtn).toBeDefined()
        await retryBtn!.trigger("click")
        await nextTick()
        await new Promise((r) => setTimeout(r, 10))
        await nextTick()

        // If cache deletion had leaked, this would now be baseline + 1 (the
        // refetch path). With the fix, it stays at baseline.
        expect(maintWatchedMock.mock.calls.length).toBe(baselineMaintWatched)
    })
})

// The partial-failure path of resolveItems removes items optimistically and
// then has to restore the ones the server couldn't process. Tested explicitly
// because the easy path ("if anyFailed call load()") would re-introduce the
// Scanning… overlay we just removed; the actual implementation uses
// refetchSourceList("pending") to keep the pane mounted.
describe("MaintenanceView resolve partial-failure handling", () => {
    it("refetches pending list (not full load) when server reports a scrobble_failed", async () => {
        maintResolveMock.mockClear()
        maintWatchedMock.mockClear()
        maintHangingMock.mockClear()
        maintPendingMock.mockClear()
        maintIgnoredMock.mockClear()
        maintResolveMock.mockResolvedValueOnce({
            results: [
                {
                    sync_sub: "movies",
                    folder: "Synclet Ship Test Movie (2099) {tmdb-0}",
                    season: null,
                    episode: null,
                    status: "scrobble_failed",
                },
            ],
            cleanup: { removed_files: 0, removed_dirs: 0 },
        })
        const wrapper = await mountWaited()
        const baselineWatched = maintWatchedMock.mock.calls.length
        const baselineHanging = maintHangingMock.mock.calls.length
        const baselinePending = maintPendingMock.mock.calls.length
        const baselineIgnored = maintIgnoredMock.mock.calls.length

        const pendingSection = wrapper.find('[data-testid="pending-deletions"]')
        const markBtn = pendingSection
            .findAll("button")
            .find((b) => b.text() === "Mark watched")
        await markBtn!.trigger("click")
        await nextTick()
        await new Promise((r) => setTimeout(r, 10))
        await nextTick()

        // Pane stayed mounted (no Scanning overlay).
        expect(wrapper.html()).not.toContain("Scanning")
        // Pending refetched exactly once. Other endpoints flat.
        expect(maintPendingMock.mock.calls.length).toBe(baselinePending + 1)
        expect(maintWatchedMock.mock.calls.length).toBe(baselineWatched)
        expect(maintHangingMock.mock.calls.length).toBe(baselineHanging)
        expect(maintIgnoredMock.mock.calls.length).toBe(baselineIgnored)
    })
})

// The ignore action throwing must roll back the source list AND the ignored
// list. The original code did this manually in two try/catch blocks; the new
// optimisticUpdate helper centralizes the snapshot/restore. This test pins
// that the centralized helper still restores both refs.
describe("MaintenanceView ignore-throw rollback", () => {
    it("restores the source list and ignored list when the ignore API throws", async () => {
        maintIgnoreMock.mockClear()
        maintIgnoreMock.mockRejectedValueOnce(new Error("boom"))
        const wrapper = await mountWaited()

        // Sanity: the watched entry is in the source list, the ignored
        // section is absent (no muted entries yet).
        expect(wrapper.html()).toContain("Old Movie")
        expect(wrapper.html()).not.toContain('data-testid="ignored-section"')

        const watchedSection = wrapper
            .findAll("section")
            .find((s) => s.html().includes("Watched in synced-media"))
        const ignoreBtn = watchedSection!
            .findAll("button")
            .find((b) => b.text() === "Ignore")
        await ignoreBtn!.trigger("click")
        await nextTick()
        await new Promise((r) => setTimeout(r, 10))
        await nextTick()

        // Source list restored, ignored section still gone.
        const html = wrapper.html()
        expect(html).toContain("Old Movie")
        expect(html).not.toContain('data-testid="ignored-section"')
        expect(html).not.toContain("Scanning")
    })
})
