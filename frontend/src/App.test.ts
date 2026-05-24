import { beforeEach, describe, expect, it, vi } from "vitest"
import { mount } from "@vue/test-utils"
import { nextTick } from "vue"

// App.vue's onMounted is the load-bearing piece of the cold-load fix. The
// grid is gated on store.loaded which loadState() sets; firing the three
// badge endpoints concurrently with /api/state was making the cold case race
// for the backend's Plex section_index lru_cache and stretching the user-
// visible "loading" state to ~22s instead of ~1s. After the fix, /api/state
// is awaited FIRST, then the badge endpoints fire-and-forget.
//
// This contract test pins the staging order. Each API mock resolves on a
// deferred Promise we control, so we can assert "loadState is in flight,
// badges have not been called yet" before letting state resolve.

const {
    stateMock,
    countsMock,
    watchlistMock,
    coverageMock,
    syncthingOverviewMock,
} = vi.hoisted(() => ({
    stateMock: vi.fn(),
    countsMock: vi.fn(),
    watchlistMock: vi.fn(),
    coverageMock: vi.fn(),
    syncthingOverviewMock: vi.fn(),
}))

vi.mock("./api", () => ({
    api: {
        state: stateMock,
        maintCounts: countsMock,
        watchlist: watchlistMock,
        coverage: coverageMock,
        syncthingOverview: syncthingOverviewMock,
        // Surface the rest of the api shape App.vue / its children may touch
        // during mount; return safe empties so the grid render doesn't crash.
        artUrl: () => "",
        thumbUrl: () => "",
        refresh: vi.fn().mockResolvedValue({ ok: true }),
        synced: vi.fn().mockResolvedValue({ items: [] }),
        title: vi.fn().mockResolvedValue(null),
        maintWatched: vi.fn().mockResolvedValue({ items: [] }),
        maintHanging: vi.fn().mockResolvedValue({ items: [] }),
        maintPending: vi.fn().mockResolvedValue({ items: [] }),
        maintIgnored: vi.fn().mockResolvedValue({
            version: 1,
            pending: [],
            watched: [],
            hanging: [],
        }),
    },
}))

beforeEach(() => {
    stateMock.mockReset()
    countsMock.mockReset()
    watchlistMock.mockReset()
    coverageMock.mockReset()
    syncthingOverviewMock.mockReset()
})

interface Deferred<T> {
    promise: Promise<T>
    resolve: (v: T) => void
}

function deferred<T>(): Deferred<T> {
    let resolve!: (v: T) => void
    const promise = new Promise<T>((r) => {
        resolve = r
    })
    return { promise, resolve }
}

describe("App.vue onMounted staging", () => {
    it("awaits loadState BEFORE firing the badge / coverage endpoints", async () => {
        // /api/state is in flight (deferred); the badge endpoints resolve
        // immediately if they're called. The test asserts they are NOT called
        // until after state resolves.
        const stateDeferred = deferred<{
            titles: never[]
            disk: null
            libraries: never[]
        }>()
        stateMock.mockReturnValueOnce(stateDeferred.promise)
        countsMock.mockResolvedValue({
            watched_titles: 0,
            hanging_files: 0,
            pending_items: 0,
            total: 0,
        })
        watchlistMock.mockResolvedValue({ items: [] })
        coverageMock.mockResolvedValue({ libraries: [] })

        // Lazy import so the vi.mock hoist is applied before the SUT loads.
        const { default: App } = await import("./App.vue")
        mount(App)

        // Microtask flush — onMounted is synchronous up to the first await.
        await nextTick()
        await nextTick()

        // loadState IS in flight, badges have NOT been called.
        expect(stateMock).toHaveBeenCalledTimes(1)
        expect(countsMock).not.toHaveBeenCalled()
        expect(watchlistMock).not.toHaveBeenCalled()
        expect(coverageMock).not.toHaveBeenCalled()

        // Let /api/state resolve. The follow-up endpoints should fire now.
        stateDeferred.resolve({ titles: [], disk: null, libraries: [] })
        await nextTick()
        await nextTick()
        await new Promise((r) => setTimeout(r, 10))
        await nextTick()

        expect(countsMock).toHaveBeenCalledTimes(1)
        expect(watchlistMock).toHaveBeenCalledTimes(1)
        expect(coverageMock).toHaveBeenCalledTimes(1)
    })

    it("still fires the badge endpoints when loadState errors", async () => {
        // Failure must not strand the badges — a Plex outage shouldn't leave
        // the tab badges blank forever; they'll surface their own errors.
        stateMock.mockRejectedValueOnce(new Error("plex offline"))
        countsMock.mockResolvedValue({
            watched_titles: 0,
            hanging_files: 0,
            pending_items: 0,
            total: 0,
        })
        watchlistMock.mockResolvedValue({ items: [] })
        coverageMock.mockResolvedValue({ libraries: [] })

        const { default: App } = await import("./App.vue")
        mount(App)

        // Drain the promise queue.
        await nextTick()
        await new Promise((r) => setTimeout(r, 10))
        await nextTick()

        expect(stateMock).toHaveBeenCalledTimes(1)
        expect(countsMock).toHaveBeenCalledTimes(1)
        expect(watchlistMock).toHaveBeenCalledTimes(1)
        expect(coverageMock).toHaveBeenCalledTimes(1)
    })
})
