import { describe, expect, it, vi, beforeAll } from "vitest"
import { nextTick } from "vue"
import { mount } from "@vue/test-utils"
import MaintenanceView from "./components/MaintenanceView.vue"

// Stub the api module so we control the wire data.
vi.mock("./api", () => ({
  api: {
    maintWatched: () => Promise.resolve({ items: [] }),
    maintHanging: () => Promise.resolve({ items: [] }),
    maintIgnored: () => Promise.resolve({
      version: 1,
      pending: [],
      watched: [],
      hanging: [],
    }),
    maintIgnore: vi.fn().mockResolvedValue({ ok: true }),
    maintUnignore: vi.fn().mockResolvedValue({ ok: true }),
    maintPending: () => Promise.resolve({
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
                { season: 1, episode: 1, already_watched_in_plex: true, episode_rating_key: null, title: "" },
                { season: 1, episode: 2, already_watched_in_plex: false, episode_rating_key: null, title: "" },
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

describe("MaintenanceView pending pane visual artifact", () => {
  it("renders the new pending-deletions pane with movie and show groups", async () => {
    const wrapper = mount(MaintenanceView)
    // Wait for onMounted + Promise resolution + reactivity flush
    await new Promise(r => setTimeout(r, 50))
    await nextTick()
    await nextTick()

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
