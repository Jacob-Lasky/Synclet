import { afterEach, describe, expect, it, vi } from "vitest"
import { nextTick } from "vue"
import { mount, flushPromises, type VueWrapper } from "@vue/test-utils"
import DetailDrawer from "./components/DetailDrawer.vue"
import { store } from "./store"
import type { TitleDetail } from "./types"

// Every test owns its drawer's subscriptions, even if it fails before cleanup.
const mountedDrawers: VueWrapper[] = []
function mountDrawer() {
    const wrapper = mount(DetailDrawer)
    mountedDrawers.push(wrapper)
    return wrapper
}
afterEach(() => {
    for (const wrapper of mountedDrawers.splice(0)) {
        if (wrapper.exists()) wrapper.unmount()
    }
    store.detail = null
})

vi.mock("./api", () => ({
    api: {
        artUrl: () => "",
        title: vi.fn(),
        job: vi.fn(),
        state: vi
            .fn()
            .mockResolvedValue({ titles: [], libraries: [], disk: {} }),
        mediaDeletePreview: vi.fn().mockResolvedValue({
            source_files: 2,
            offline_files: 0,
            source_bytes: 200,
            offline_bytes: 0,
            fingerprint: "preview-fingerprint",
        }),
        scrobble: vi.fn().mockResolvedValue({
            scrobbled: 1,
            failed: 0,
            results: [{ season: 1, episode: 1, status: "ok" }],
        }),
        sync: vi.fn(),
        unsync: vi.fn(),
        follow: vi.fn().mockResolvedValue({ following: true }),
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
                    has_video: true,
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
                    has_video: true,
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

describe("DetailDrawer initial season focus", () => {
    it("opens the latest season with an unwatched offline episode", async () => {
        const { api } = await import("./api")
        const show = JSON.parse(JSON.stringify(SHOW_FIXTURE)) as TitleDetail
        show.seasons[0]!.episodes[0]!.watch_state = "watched"
        show.seasons[0]!.watched_episodes = 1
        show.seasons.push({
            season: 2,
            total_bytes: 1024,
            synced_episodes: 1,
            watched_episodes: 0,
            episodes: [
                {
                    season: 2,
                    episode: 1,
                    title: "New episode",
                    size_bytes: 1024,
                    files: [],
                    has_video: true,
                    is_synced: true,
                    watch_state: "unwatched",
                    watch_pct: 0,
                },
            ],
        })
        ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue(show)
        store.detail = { lib: "tv", folder: "Test Show" }
        const wrapper = mountDrawer()
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()

        const headers = wrapper.findAll(".season-head")
        expect(headers).toHaveLength(2)
        expect(headers[0]!.find(".chev").classes()).not.toContain("open")
        expect(headers[1]!.find(".chev").classes()).toContain("open")
        store.detail = null
    })
})

describe("DetailDrawer follow control", () => {
    it("can follow a show before any episode is offline", async () => {
        const { api } = await import("./api")
        ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue({
            ...SHOW_FIXTURE,
            followed: false,
        })
        store.detail = { lib: "tv", folder: "Test Show" }
        const wrapper = mountDrawer()
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()
        await wrapper.get('[data-testid="follow-series"]').trigger("click")
        await nextTick()
        expect(api.follow).toHaveBeenCalledWith("tv", "Test Show", true)
        expect(wrapper.get('[data-testid="follow-series"]').text()).toContain(
            "Stop following"
        )
        store.detail = null
    })
})

describe("DetailDrawer series deletion", () => {
    it("previews only fully watched episodes across seasons", async () => {
        const { api } = await import("./api")
        HTMLDialogElement.prototype.showModal = vi.fn()
        ;(api.mediaDeletePreview as ReturnType<typeof vi.fn>).mockClear()
        const show = JSON.parse(JSON.stringify(SHOW_FIXTURE)) as TitleDetail
        show.folder = "Delete Watched Show"
        show.seasons[0]!.episodes[0]!.watch_state = "watched"
        show.seasons[0]!.watched_episodes = 1
        show.seasons[0]!.episodes[1]!.watch_state = "progress"
        show.seasons.push({
            season: 2,
            total_bytes: 100,
            synced_episodes: 0,
            watched_episodes: 1,
            episodes: [
                {
                    season: 2,
                    episode: 1,
                    title: "Watched later",
                    size_bytes: 100,
                    files: [],
                    has_video: true,
                    is_synced: false,
                    watch_state: "watched",
                    watch_pct: 100,
                },
                {
                    season: 2,
                    episode: 2,
                    title: "Unwatched later",
                    size_bytes: 100,
                    files: [],
                    has_video: true,
                    is_synced: false,
                    watch_state: "unwatched",
                    watch_pct: 0,
                },
                {
                    season: 2,
                    episode: 3,
                    title: "Leftover subtitle",
                    size_bytes: 5,
                    files: ["leftover.en.srt"],
                    has_video: false,
                    is_synced: false,
                    watch_state: "watched",
                    watch_pct: 100,
                },
            ],
        })
        ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue(show)
        store.detail = { lib: "tv", folder: show.folder }
        const wrapper = mountDrawer()
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()

        const button = wrapper.get('[data-testid="delete-watched-media"]')
        expect(button.text()).toContain("Delete watched (2)")
        await button.trigger("click")
        await nextTick()
        expect(api.mediaDeletePreview).toHaveBeenCalledWith({
            lib: "tv",
            folder: show.folder,
            selection_type: "episodes",
            episodes: [
                [1, 1],
                [2, 1],
            ],
            unmonitor: true,
        })
        expect(wrapper.get("#delete-title").text()).toContain(
            "2 watched episodes of Test Show"
        )
        expect(wrapper.get('[data-testid="unmonitor-note"]').text()).toContain(
            "Sonarr will stop monitoring them"
        )
        wrapper.unmount()
        store.detail = null
    })

    it("leaves subtitle-only rows out of a selected deletion", async () => {
        const { api } = await import("./api")
        HTMLDialogElement.prototype.showModal = vi.fn()
        ;(api.mediaDeletePreview as ReturnType<typeof vi.fn>).mockClear()
        const show = JSON.parse(JSON.stringify(SHOW_FIXTURE)) as TitleDetail
        show.folder = "Selected Delete Show"
        show.seasons[0]!.episodes[1]!.has_video = false
        show.seasons[0]!.episodes[1]!.files = ["leftover.en.srt"]
        ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue(show)
        store.detail = { lib: "tv", folder: show.folder }
        const wrapper = mountDrawer()
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()

        await wrapper.get(".actions-row button:nth-child(2)").trigger("click")
        await wrapper
            .get('[data-testid="delete-selected-media"]')
            .trigger("click")
        await nextTick()
        expect(api.mediaDeletePreview).toHaveBeenCalledWith({
            lib: "tv",
            folder: show.folder,
            selection_type: "episodes",
            episodes: [[1, 1]],
        })
        // A hand-picked delete may be a bad copy to re-grab: monitoring stays.
        expect(wrapper.find('[data-testid="unmonitor-note"]').exists()).toBe(
            false
        )
        wrapper.unmount()
        store.detail = null
    })

    it.each([
        [true, true],
        [false, false],
    ])(
        "a movie delete unmonitors in Radarr only when watched (watched=%s)",
        async (watched, unmonitor) => {
            const { api } = await import("./api")
            HTMLDialogElement.prototype.showModal = vi.fn()
            ;(api.mediaDeletePreview as ReturnType<typeof vi.fn>).mockClear()
            ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue({
                ...MOVIE_FIXTURE,
                watched,
            })
            store.detail = { lib: "movies", folder: "M" }
            const wrapper = mountDrawer()
            await new Promise((r) => setTimeout(r, 50))
            await nextTick()

            await wrapper
                .get('[data-testid="delete-movie-media"]')
                .trigger("click")
            await nextTick()
            expect(api.mediaDeletePreview).toHaveBeenCalledWith({
                lib: "movies",
                folder: "M",
                selection_type: "movie",
                episodes: [],
                unmonitor,
            })
            const note = wrapper.find('[data-testid="unmonitor-note"]')
            expect(note.exists()).toBe(unmonitor)
            if (unmonitor) {
                expect(note.text()).toContain(
                    "Radarr will stop monitoring this movie"
                )
            }
            wrapper.unmount()
            store.detail = null
        }
    )
})

describe("DetailDrawer mark-watched affordances", () => {
    it("renders Mark series watched + Mark season watched + per-episode mark buttons", async () => {
        const { api } = await import("./api")
        ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue(SHOW_FIXTURE)
        store.detail = { lib: "tv", folder: "Test Show" }

        const wrapper = mountDrawer()
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

        const wrapper = mountDrawer()
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
        const wrapper = mountDrawer()
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
        const w1 = mountDrawer()
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
        const w2 = mountDrawer()
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

        const wrapper = mountDrawer()
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()
        await nextTick()

        const html = wrapper.html()
        expect(html).not.toContain('data-testid="mark-movie-watched"')

        store.detail = null
    })
})

describe("DetailDrawer mark-unwatched affordances", () => {
    it("renders Mark series unwatched + Mark season unwatched + per-episode unwatch on a watched episode", async () => {
        const { api } = await import("./api")
        // A show whose one episode is already watched, so the per-episode tile
        // offers unwatch rather than watch.
        ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue({
            ...SHOW_FIXTURE,
            folder: "Watched Show",
            seasons: [
                {
                    season: 1,
                    total_bytes: 0,
                    synced_episodes: 0,
                    watched_episodes: 1,
                    episodes: [
                        {
                            season: 1,
                            episode: 1,
                            title: "Pilot",
                            size_bytes: 0,
                            files: [],
                            has_video: true,
                            is_synced: false,
                            watch_state: "watched" as const,
                            watch_pct: 100,
                        },
                    ],
                },
            ],
        })
        store.detail = { lib: "tv", folder: "Watched Show" }

        const wrapper = mountDrawer()
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()
        await nextTick()

        const html = wrapper.html()
        expect(html).toContain('data-testid="mark-series-unwatched"')
        expect(html).toContain('data-testid="mark-season-unwatched"')
        expect(html).toContain('data-testid="mark-episode-unwatched"')
        expect(html).toContain("Mark series unwatched")

        store.detail = null
    })

    it("renders Mark unwatched on the movie pane when the movie is watched", async () => {
        const { api } = await import("./api")
        ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue({
            ...MOVIE_FIXTURE,
            folder: "Watched Movie",
            watched: true,
        })
        store.detail = { lib: "movies", folder: "Watched Movie" }

        const wrapper = mountDrawer()
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()
        await nextTick()

        const html = wrapper.html()
        expect(html).toContain('data-testid="mark-movie-unwatched"')
        expect(html).toContain("Mark unwatched")

        store.detail = null
    })

    it("posts watched:false when Mark series unwatched is clicked", async () => {
        const { api } = await import("./api")
        ;(api.scrobble as ReturnType<typeof vi.fn>).mockClear()
        ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue({
            ...SHOW_FIXTURE,
            folder: "Click Show",
        })
        store.detail = { lib: "tv", folder: "Click Show" }

        const wrapper = mountDrawer()
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()
        await nextTick()

        await wrapper
            .find('[data-testid="mark-series-unwatched"]')
            .trigger("click")
        await nextTick()

        expect(api.scrobble).toHaveBeenCalledWith(
            expect.objectContaining({
                lib: "tv",
                folder: "Click Show",
                scope: "series",
                watched: false,
            })
        )

        store.detail = null
    })

    it("keeps season-level mark-unwatched sticky against a stale refresh (inverse Unsettled bug)", async () => {
        // Symmetric to the mark-watched Unsettled bug: unscrobble lands at Plex,
        // we optimistically flip to unwatched, then refreshInPlace fires and
        // api.title() returns the STALE watched state (WatchState daemon hasn't
        // caught up). The unwatch overlay must re-apply so the episodes don't
        // flicker back to watched.
        const { api } = await import("./api")
        const WATCHED_FIXTURE = {
            ...SHOW_FIXTURE,
            folder: "Sticky Unwatch Show",
            seasons: [
                {
                    season: 1,
                    total_bytes: 0,
                    synced_episodes: 0,
                    watched_episodes: 2,
                    episodes: [
                        {
                            season: 1,
                            episode: 1,
                            title: "Pilot",
                            size_bytes: 0,
                            files: [],
                            has_video: true,
                            is_synced: false,
                            watch_state: "watched" as const,
                            watch_pct: 100,
                        },
                        {
                            season: 1,
                            episode: 2,
                            title: "Ep Two",
                            size_bytes: 0,
                            files: [],
                            has_video: true,
                            is_synced: false,
                            watch_state: "watched" as const,
                            watch_pct: 100,
                        },
                    ],
                },
            ],
        }
        // Every load (initial + stale refresh) returns a fresh watched copy, so
        // the only thing that can keep the episodes unwatched is the overlay.
        ;(api.title as ReturnType<typeof vi.fn>).mockImplementation(() =>
            Promise.resolve(JSON.parse(JSON.stringify(WATCHED_FIXTURE)))
        )
        ;(api.scrobble as ReturnType<typeof vi.fn>).mockResolvedValue({
            scrobbled: 2,
            failed: 0,
            results: [
                { season: 1, episode: 1, status: "ok" },
                { season: 1, episode: 2, status: "ok" },
            ],
        })
        window.confirm = () => true

        store.detail = { lib: "tv", folder: "Sticky Unwatch Show" }
        const wrapper = mountDrawer()
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()

        // Sanity: both episodes start watched.
        expect(
            (wrapper.html().match(/class="ico watched"/g) ?? []).length
        ).toBe(2)

        await wrapper
            .find('[data-testid="mark-season-unwatched"]')
            .trigger("click")
        await nextTick()
        await new Promise((r) => setTimeout(r, 50))
        await nextTick()

        // Wait for the stale refreshInPlace timer to fire (returns watched).
        await new Promise((r) => setTimeout(r, 1200))
        await nextTick()
        await nextTick()

        const html = wrapper.html()
        // The overlay forced both episodes unwatched and re-applied on the
        // stale refresh, so NO watched dots remain.
        expect((html.match(/class="ico watched"/g) ?? []).length).toBe(0)
        // And the season "N ✓" watched tag is gone (recomputed count = 0).
        expect(html).not.toContain("2 ✓")

        store.detail = null
    })
})

describe("movie subtitle repair", () => {
    it("requests only subtitles for an already synced movie", async () => {
        const { api } = await import("./api")
        vi.mocked(api.title).mockResolvedValue({
            ...MOVIE_FIXTURE,
            synced_bytes: 100,
            files: [
                {
                    name: "movie.mkv",
                    path: "/movie.mkv",
                    size_bytes: 100,
                    is_video: true,
                    is_synced: true,
                },
            ],
            subtitle_check: {
                videos: [
                    {
                        video: "movie.mkv",
                        status: "tracks_unknown",
                        sidecars: [],
                    },
                ],
                warnings: [],
                note: "unknown",
            },
        })
        vi.mocked(api.sync).mockResolvedValue({
            job_id: null,
            total_files: 0,
            total_media_files: 0,
            total_bytes: 0,
        })
        store.detail = { lib: "movies", folder: "M" }
        const wrapper = mountDrawer()
        await new Promise((resolve) => setTimeout(resolve, 15))
        const button = wrapper
            .findAll("button")
            .find((b) => b.text() === "Verify / copy subtitles")!
        await button.trigger("click")
        expect(api.sync).toHaveBeenCalledWith({
            lib: "movies",
            folder: "M",
            selection_type: "movie",
            require_forced: false,
            subtitles_only: true,
        })
        await nextTick()
        expect(wrapper.text()).toContain("No subtitle files available to copy")
        wrapper.unmount()
        store.detail = null
    })
})

it("refreshes subtitle evidence on actual repair completion with the active requirement", async () => {
    const { api } = await import("./api")
    vi.useFakeTimers()
    let repaired = false
    let complete!: (job: import("./types").Job) => void
    const wire = () => ({
        ...MOVIE_FIXTURE,
        synced_bytes: 100,
        files: [
            {
                name: "movie.mkv",
                path: "/movie.mkv",
                size_bytes: 100,
                is_video: true,
                is_synced: true,
            },
        ],
        subtitle_check: {
            videos: [
                {
                    video: "movie.mkv",
                    status: repaired
                        ? "forced_sidecar_available"
                        : "required_forced_missing",
                    sidecars: [
                        {
                            name: "movie.en.forced.srt",
                            source_exists: true,
                            offline: repaired ? "verified" : "not_verified",
                        },
                    ],
                },
            ],
            warnings: [],
            note: "unknown",
        },
    })
    vi.mocked(api.title).mockImplementation(async () => wire())
    vi.mocked(api.sync).mockResolvedValue({
        job_id: "subtitle-repair",
        total_files: 1,
        total_media_files: 0,
        total_bytes: 8,
    })
    vi.mocked(api.job).mockImplementation(
        () =>
            new Promise((resolve) => {
                complete = resolve
            })
    )
    store.detail = { lib: "movies", folder: "M" }
    const wrapper = mountDrawer()
    try {
        await flushPromises()
        await wrapper.get('input[type="checkbox"]').setValue(true)
        await flushPromises()
        expect(wrapper.text()).toContain("offline copy not verified")
        const button = wrapper
            .findAll("button")
            .find((b) => b.text() === "Verify / copy subtitles")!
        await button.trigger("click")
        await flushPromises()
        repaired = true
        complete({
            id: "subtitle-repair",
            op: "sync",
            status: "done",
            total_files: 1,
            total_media_files: 0,
            processed_files: 1,
            processed_media_files: 0,
            processed_bytes: 8,
            total_bytes: 8,
            current_file: "",
            title: "Movie M",
            started_at: 0,
            ended_at: 1,
            error: "",
            verified_subtitles: 1,
        })
        await flushPromises()
        expect(api.title).toHaveBeenLastCalledWith("movies", "M", true)
        expect(wrapper.text()).toContain("offline copy verified")
        expect(wrapper.text()).not.toContain("offline copy not verified")
        expect(wrapper.find('[role="alert"]').exists()).toBe(false)
        expect(
            (wrapper.get('input[type="checkbox"]').element as HTMLInputElement)
                .checked
        ).toBe(true)
    } finally {
        wrapper.unmount()
        store.detail = null
        store.toasts = []
        vi.clearAllTimers()
        vi.useRealTimers()
    }
})

it("ignores a completion refresh made before a requirement toggle", async () => {
    const { api } = await import("./api")
    const movie = (status: string) => ({
        ...MOVIE_FIXTURE,
        subtitle_check: {
            videos: [{ video: "movie.mkv", status, sidecars: [] }],
            warnings: [],
            note: "unknown",
        },
    })
    let stale!: (detail: TitleDetail) => void
    vi.mocked(api.title).mockResolvedValue(movie("tracks_unknown"))
    store.detail = { lib: "movies", folder: "M" }
    const wrapper = mountDrawer()
    try {
        await flushPromises()
        vi.mocked(api.title).mockImplementationOnce(
            () =>
                new Promise((resolve) => {
                    stale = resolve
                })
        )
        store.syncedVersion++
        await flushPromises()
        vi.mocked(api.title).mockResolvedValue(movie("required_forced_missing"))
        await wrapper.get('input[type="checkbox"]').setValue(true)
        await flushPromises()
        expect(wrapper.text()).toContain("expected but missing")
        stale(movie("forced_sidecar_available"))
        await flushPromises()
        expect(wrapper.text()).toContain("expected but missing")
        expect(wrapper.text()).not.toContain(
            "English forced subtitle file found"
        )
        expect(
            (wrapper.get('input[type="checkbox"]').element as HTMLInputElement)
                .checked
        ).toBe(true)
    } finally {
        wrapper.unmount()
        store.detail = null
    }
})

it("ignores a completion refresh after navigation to another movie", async () => {
    const { api } = await import("./api")
    const movie = (name: string) => ({
        ...MOVIE_FIXTURE,
        name,
        subtitle_check: {
            videos: [
                { video: "movie.mkv", status: "tracks_unknown", sidecars: [] },
            ],
            warnings: [],
            note: "unknown",
        },
    })
    let stale!: (detail: TitleDetail) => void
    vi.mocked(api.title).mockResolvedValue(movie("Movie M"))
    store.detail = { lib: "movies", folder: "M" }
    const wrapper = mountDrawer()
    try {
        await flushPromises()
        vi.mocked(api.title).mockImplementationOnce(
            () =>
                new Promise((resolve) => {
                    stale = resolve
                })
        )
        store.syncedVersion++
        await flushPromises()
        vi.mocked(api.title).mockResolvedValue({
            ...movie("Movie N"),
            folder: "N",
            id: "movies/N",
        })
        store.detail = { lib: "movies", folder: "N" }
        await flushPromises()
        stale({
            ...movie("Movie M"),
            subtitle_check: {
                videos: [
                    {
                        video: "movie.mkv",
                        status: "required_forced_missing",
                        sidecars: [],
                    },
                ],
                warnings: [],
                note: "unknown",
            },
        })
        await flushPromises()
        expect(wrapper.text()).toContain("Movie N")
        expect(wrapper.text()).not.toContain("expected but missing")
    } finally {
        wrapper.unmount()
        store.detail = null
    }
})

it("keeps newer completion evidence when an earlier same-requirement read returns late", async () => {
    const { api } = await import("./api")
    const movie = (status: string) => ({
        ...MOVIE_FIXTURE,
        subtitle_check: {
            videos: [{ video: "movie.mkv", status, sidecars: [] }],
            warnings: [],
            note: "unknown",
        },
    })
    let stale!: (detail: TitleDetail) => void
    vi.mocked(api.title).mockResolvedValue(movie("tracks_unknown"))
    store.detail = { lib: "movies", folder: "M" }
    const wrapper = mountDrawer()
    try {
        await flushPromises()
        vi.mocked(api.title).mockImplementationOnce(
            () =>
                new Promise((resolve) => {
                    stale = resolve
                })
        )
        store.syncedVersion++
        await flushPromises()
        vi.mocked(api.title).mockResolvedValue(
            movie("forced_sidecar_available")
        )
        store.syncedVersion++
        await flushPromises()
        expect(wrapper.text()).toContain("English forced subtitle file found")
        stale(movie("required_forced_missing"))
        await flushPromises()
        expect(wrapper.text()).toContain("English forced subtitle file found")
        expect(wrapper.text()).not.toContain("expected but missing")
    } finally {
        wrapper.unmount()
        store.detail = null
    }
})

it.each(["success", "failure"])(
    "keeps verified terminal evidence when an older toggle finishes with %s",
    async (outcome) => {
        const { api } = await import("./api")
        vi.useFakeTimers()
        const movie = (verified: boolean) => ({
            ...MOVIE_FIXTURE,
            synced_bytes: 100,
            files: [
                {
                    name: "movie.mkv",
                    path: "/movie.mkv",
                    size_bytes: 100,
                    is_video: true,
                    is_synced: true,
                },
            ],
            subtitle_check: {
                videos: [
                    {
                        video: "movie.mkv",
                        status: verified
                            ? "forced_sidecar_available"
                            : "required_forced_missing",
                        sidecars: [
                            {
                                name: "movie.en.forced.srt",
                                source_exists: true,
                                offline: verified ? "verified" : "not_verified",
                            },
                        ],
                    },
                ],
                warnings: [],
                note: "unknown",
            },
        })
        let oldSuccess!: (value: TitleDetail) => void
        let oldFailure!: (error: Error) => void
        let complete!: (job: import("./types").Job) => void
        vi.mocked(api.title).mockReset()
        vi.mocked(api.title).mockResolvedValue(movie(true))
        vi.mocked(api.title).mockResolvedValueOnce(movie(false))
        vi.mocked(api.title).mockImplementationOnce(
            () =>
                new Promise((resolve, reject) => {
                    oldSuccess = resolve
                    oldFailure = reject
                })
        )
        vi.mocked(api.sync).mockResolvedValue({
            job_id: "ordered-repair",
            total_files: 1,
            total_media_files: 0,
            total_bytes: 8,
        })
        vi.mocked(api.job).mockImplementation(
            () =>
                new Promise((resolve) => {
                    complete = resolve
                })
        )
        store.detail = { lib: "movies", folder: "M" }
        const wrapper = mountDrawer()
        try {
            await flushPromises()
            await wrapper.get('input[type="checkbox"]').setValue(true)
            await flushPromises()
            const repair = wrapper
                .findAll("button")
                .find((button) => button.text() === "Verify / copy subtitles")!
            await repair.trigger("click")
            await flushPromises()
            complete({
                id: "ordered-repair",
                op: "sync",
                status: "done",
                total_files: 1,
                total_media_files: 0,
                processed_files: 1,
                processed_media_files: 0,
                processed_bytes: 8,
                total_bytes: 8,
                current_file: "",
                title: "Movie M",
                started_at: 0,
                ended_at: 1,
                error: "",
                verified_subtitles: 1,
            })
            await flushPromises()
            expect(api.title).toHaveBeenLastCalledWith("movies", "M", true)
            expect(vi.mocked(api.title).mock.calls.length).toBe(3)
            expect(wrapper.text()).toContain("offline copy verified")
            if (outcome === "success") oldSuccess(movie(false))
            else oldFailure(new Error("stale toggle failure"))
            await flushPromises()
            expect(wrapper.text()).toContain("offline copy verified")
            expect(wrapper.text()).not.toContain("offline copy not verified")
            expect(wrapper.text()).not.toContain("stale toggle failure")
            expect(wrapper.find('[role="alert"]').exists()).toBe(false)
            expect(
                (
                    wrapper.get('input[type="checkbox"]')
                        .element as HTMLInputElement
                ).checked
            ).toBe(true)
        } finally {
            wrapper.unmount()
            store.detail = null
            store.toasts = []
            vi.clearAllTimers()
            vi.useRealTimers()
        }
    }
)

it("clears a current subtitle error when a newer completion read succeeds", async () => {
    const { api } = await import("./api")
    const movie = {
        ...MOVIE_FIXTURE,
        subtitle_check: {
            videos: [
                {
                    video: "movie.mkv",
                    status: "forced_sidecar_available",
                    sidecars: [
                        {
                            name: "movie.en.forced.srt",
                            source_exists: true,
                            offline: "verified",
                        },
                    ],
                },
            ],
            warnings: [],
            note: "unknown",
        },
    }
    vi.mocked(api.title).mockResolvedValue(movie)
    store.detail = { lib: "movies", folder: "M" }
    const wrapper = mountDrawer()
    try {
        await flushPromises()
        vi.mocked(api.title).mockRejectedValueOnce(
            new Error("current toggle error")
        )
        await wrapper.get('input[type="checkbox"]').setValue(true)
        await flushPromises()
        expect(wrapper.text()).toContain("current toggle error")
        store.syncedVersion++
        await flushPromises()
        expect(api.title).toHaveBeenLastCalledWith("movies", "M", true)
        expect(wrapper.text()).toContain("offline copy verified")
        expect(wrapper.text()).not.toContain("current toggle error")
        expect(
            (wrapper.get('input[type="checkbox"]').element as HTMLInputElement)
                .checked
        ).toBe(true)
    } finally {
        wrapper.unmount()
        store.detail = null
    }
})

it("shows a genuine current completion read error and keeps the requirement", async () => {
    const { api } = await import("./api")
    const movie = {
        ...MOVIE_FIXTURE,
        subtitle_check: {
            videos: [
                { video: "movie.mkv", status: "tracks_unknown", sidecars: [] },
            ],
            warnings: [],
            note: "unknown",
        },
    }
    vi.mocked(api.title).mockResolvedValue(movie)
    store.detail = { lib: "movies", folder: "M" }
    const wrapper = mountDrawer()
    try {
        await flushPromises()
        await wrapper.get('input[type="checkbox"]').setValue(true)
        await flushPromises()
        vi.mocked(api.title).mockRejectedValueOnce(
            new Error("current completion read error")
        )
        store.syncedVersion++
        await flushPromises()
        expect(wrapper.text()).toContain("current completion read error")
        expect(
            (wrapper.get('input[type="checkbox"]').element as HTMLInputElement)
                .checked
        ).toBe(true)
    } finally {
        wrapper.unmount()
        store.detail = null
    }
})
