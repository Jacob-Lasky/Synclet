import { describe, expect, it, vi } from "vitest"
import { nextTick } from "vue"
import { mount } from "@vue/test-utils"
import DetailDrawer from "./components/DetailDrawer.vue"
import { store } from "./store"

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
          season: 1, episode: 1, title: "Pilot", size_bytes: 0,
          files: [], is_synced: false,
          watch_state: "unwatched" as const, watch_pct: 0,
        },
        {
          season: 1, episode: 2, title: "Ep Two", size_bytes: 0,
          files: [], is_synced: false,
          watch_state: "unwatched" as const, watch_pct: 0,
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
    await new Promise(r => setTimeout(r, 50))
    await nextTick()
    await nextTick()

    const html = wrapper.html()
    expect(html).toContain('data-testid="mark-series-watched"')
    expect(html).toContain('data-testid="mark-season-watched"')
    expect(html).toContain('data-testid="mark-episode-watched"')
    expect(html).toContain("Mark series watched")
    // Two episode tiles, each with its own mark-watched button
    expect(html.match(/data-testid="mark-episode-watched"/g)?.length).toBe(2)

    store.detail = null
  })

  it("renders Mark watched on the movie pane when movie is unwatched", async () => {
    const { api } = await import("./api")
    ;(api.title as ReturnType<typeof vi.fn>).mockResolvedValue(MOVIE_FIXTURE)
    store.detail = { lib: "movies", folder: "M" }

    const wrapper = mount(DetailDrawer)
    await new Promise(r => setTimeout(r, 50))
    await nextTick()
    await nextTick()

    const html = wrapper.html()
    expect(html).toContain('data-testid="mark-movie-watched"')
    expect(html).toContain("Mark watched")

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
    await new Promise(r => setTimeout(r, 50))
    await nextTick()
    await nextTick()

    const html = wrapper.html()
    expect(html).not.toContain('data-testid="mark-movie-watched"')

    store.detail = null
  })
})
