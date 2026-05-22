import { describe, expect, it, vi } from "vitest"
import { nextTick } from "vue"
import { mount } from "@vue/test-utils"
import DetailDrawer from "./components/DetailDrawer.vue"
import { store } from "./store"
import type { TitleDetail } from "./types"

vi.mock("./api", () => ({
    api: {
        artUrl: () => "",
        title: vi.fn(),
        scrobble: vi.fn().mockResolvedValue({
            scrobbled: 1,
            failed: 0,
            results: [{ season: 1, episode: 1, status: "ok" }],
        }),
        sync: vi.fn(),
        unsync: vi.fn(),
    },
}))

const SHOW_FIXTURE = {
    id: "tv/Test",
    lib: "tv",
    folder: "Test Show",
    name: "Test Show",
    kind: "show" as const,
    year: 2024,
    total_bytes: 0,
    synced_bytes: 0,
    files: [],
    seasons: [
        {
            season: 1,
            total_bytes: 0,
            synced_episodes: 0,
            watched_episodes: 0,
            episodes: [
                {
                    season: 1,
                    episode: 1,
                    title: "Pilot",
                    size_bytes: 0,
                    files: [],
                    is_synced: false,
                    watch_state: "unwatched" as const,
                    watch_pct: 0,
                },
                {
                    season: 1,
                    episode: 2,
                    title: "Ep Two",
                    size_bytes: 0,
                    files: [],
                    is_synced: false,
                    watch_state: "unwatched" as const,
                    watch_pct: 0,
                },
            ],
        },
    ],
}

const MOVIE_FIXTURE = {
    id: "movies/M",
    lib: "movies",
    folder: "M",
    name: "Movie M",
    kind: "movie" as const,
    year: 2024,
    total_bytes: 0,
    synced_bytes: 0,
    files: [],
    seasons: [],
    watched: false,
}

describe("DetailDrawer mark-watched affordances", () => {
    it("renders Mark series watched + Mark season watched + per-episode mark buttons", async () => {
        const { api } = await import("./api")
        ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue(SHOW_FIXTURE)
        store.detail = { lib: "tv", folder: "Test Show" }

        const wrapper = mount(DetailDrawer)
        // wait for onMounted + load + render
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()
        await nextTick()

        const html = wrapper.html()
        expect(html).toContain('data-testid="mark-series-watched"')
        expect(html).toContain('data-testid="mark-season-watched"')
        expect(html).toContain('data-testid="mark-episode-watched"')
        expect(html).toContain("Mark series watched")
        // Two episode tiles, each with its own mark-watched button
        expect(html.match(/data-testid="mark-episode-watched"/g)?.length).toBe(
            2
        )

        store.detail = null
    })

    it("renders Mark watched on the movie pane when movie is unwatched", async () => {
        const { api } = await import("./api")
        ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue(
            MOVIE_FIXTURE
        )
        store.detail = { lib: "movies", folder: "M" }

        const wrapper = mount(DetailDrawer)
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()
        await nextTick()

        const html = wrapper.html()
        expect(html).toContain('data-testid="mark-movie-watched"')
        expect(html).toContain("Mark watched")

        store.detail = null
    })

    it("keeps season-level mark-watched sticky against a stale refresh (the Unsettled bug)", async () => {
        // Simulates the exact bug Jake hit: scrobble lands at Plex, frontend
        // optimistically updates, then refreshInPlace fires and api.title()
        // returns stale watchstate data (the daemon hasn't caught up). Without
        // the session overlay, the optimistic update gets clobbered.
        const { api } = await import("./api")
        const STALE_FIXTURE = JSON.parse(
            JSON.stringify(SHOW_FIXTURE)
        ) as TitleDetail

        // First call: initial load returns unwatched (real Plex state).
        // Subsequent calls (refreshInPlace) also return unwatched (stale DB).
        ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue(
            STALE_FIXTURE
        )
        // Scrobble route returns ok for both episodes.
        ;(api.scrobble as ReturnType<typeof vi.fn>).mockResolvedValue({
            scrobbled: 2,
            failed: 0,
            results: [
                { season: 1, episode: 1, status: "ok" },
                { season: 1, episode: 2, status: "ok" },
            ],
        })

        store.detail = { lib: "tv", folder: "Test Show" }
        const wrapper = mount(DetailDrawer)
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()

        // Trigger season-level mark-watched. The script setup exposes markWatched
        // indirectly through the season button.
        const seasonBtn = wrapper.find('[data-testid="mark-season-watched"]')
        expect(seasonBtn.exists()).toBe(true)

        // Auto-confirm the window.confirm prompt (happy-dom may not define it).
        window.confirm = () => true
        await seasonBtn.trigger("click")
        // Let the scrobble Promise + optimistic update flush.
        await nextTick()
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()

        // Trigger the refresh that previously clobbered the UI. The 1s timer
        // fires inside markWatched; vi can't easily fast-forward the real
        // setTimeout here without setting up fake timers, so we just call the
        // refresh-equivalent: another mock api.title returns stale data,
        // applyScrobbleOverlay should re-apply watched to the scrobbled episodes.
        // We assert by reading the rendered DOM: the watched indicator (.ico.watched
        // ●) should be present for both episodes.

        // Wait a beat for the 1s refresh timer to fire.
        await new Promise((r) => setTimeout(r, 1200))
        await nextTick()
        await nextTick()

        const html = wrapper.html()
        // Both episodes should show the watched indicator (● in EpisodeTile) — the
        // session overlay re-applied on the stale refresh.
        const watchedDots = (html.match(/class="ico watched"/g) ?? []).length
        expect(watchedDots).toBe(2)

        store.detail = null
    })

    it("overlay survives drawer re-mount within the same session", async () => {
        // User marks an episode watched, closes the drawer (navigates away),
        // reopens it. WatchState still hasn't synced, so api.title returns the
        // same stale unwatched state. The overlay must still re-apply.
        const { api } = await import("./api")
        const STALE = JSON.parse(JSON.stringify(SHOW_FIXTURE)) as TitleDetail
        ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue(STALE)
        ;(api.scrobble as ReturnType<typeof vi.fn>).mockResolvedValue({
            scrobbled: 2,
            failed: 0,
            results: [
                { season: 1, episode: 1, status: "ok" },
                { season: 1, episode: 2, status: "ok" },
            ],
        })
        window.confirm = () => true

        // First mount: open drawer, scrobble episode 1.
        store.detail = { lib: "tv", folder: "Test Show" }
        const w1 = mount(DetailDrawer)
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()
        // Use the season-level button (always rendered when the season header is
        // visible, no hover dependency).
        const seasonBtn = w1.find('[data-testid="mark-season-watched"]')
        expect(seasonBtn.exists()).toBe(true)
        await seasonBtn.trigger("click")
        await nextTick()
        await new Promise((r) => setTimeout(r, 50))
        w1.unmount()
        store.detail = null

        // Second mount: same lib/folder, api still returns stale.
        store.detail = { lib: "tv", folder: "Test Show" }
        const w2 = mount(DetailDrawer)
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()

        // The previously-scrobbled episode must still show as watched even
        // though the API call returned unwatched.
        const html = w2.html()
        expect(html).toMatch(/class="ico watched"/)

        store.detail = null
    })

    it("does NOT render Mark watched on a movie pane when watched is true", async () => {
        const { api } = await import("./api")
        ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue({
            ...MOVIE_FIXTURE,
            watched: true,
        })
        store.detail = { lib: "movies", folder: "M" }

        const wrapper = mount(DetailDrawer)
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()
        await nextTick()

        const html = wrapper.html()
        expect(html).not.toContain('data-testid="mark-movie-watched"')

        store.detail = null
    })
})
