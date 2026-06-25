import { describe, expect, it, vi } from "vitest"
import { mount } from "@vue/test-utils"
import EpisodeTile from "./components/EpisodeTile.vue"
import type { Episode } from "./types"

// onKey was refactored to drop unused season/episode params (the click event
// already carries them via $emit). Pin the keyboard a11y contract so the
// keydown -> click bridge can't regress: Enter/Space must synthesize a click,
// and the click must emit `toggle` with the right season/episode.

function makeEpisode(overrides: Partial<Episode> = {}): Episode {
    return {
        season: 2,
        episode: 5,
        title: "The One With The Test",
        size_bytes: 0,
        files: [],
        is_synced: false,
        watch_state: "unwatched",
        watch_pct: 0,
        ...overrides,
    }
}

describe("EpisodeTile keyboard activation", () => {
    it("emits toggle on Enter keydown with the tile's season/episode", async () => {
        const ep = makeEpisode()
        const wrapper = mount(EpisodeTile, {
            props: { ep, selected: false },
        })

        await wrapper
            .find('[role="button"]')
            .trigger("keydown", { key: "Enter" })

        const emitted = wrapper.emitted("toggle")
        expect(emitted).toBeTruthy()
        expect(emitted![0]).toEqual([2, 5, false])
    })

    it("emits toggle on Space keydown", async () => {
        const ep = makeEpisode({ season: 3, episode: 7 })
        const wrapper = mount(EpisodeTile, {
            props: { ep, selected: false },
        })

        await wrapper.find('[role="button"]').trigger("keydown", { key: " " })

        const emitted = wrapper.emitted("toggle")
        expect(emitted).toBeTruthy()
        expect(emitted![0]).toEqual([3, 7, false])
    })

    it("propagates shift modifier when shift is held during Enter", async () => {
        const ep = makeEpisode()
        const wrapper = mount(EpisodeTile, {
            props: { ep, selected: false },
        })

        await wrapper
            .find('[role="button"]')
            .trigger("keydown", { key: "Enter", shiftKey: true })

        const emitted = wrapper.emitted("toggle")
        expect(emitted).toBeTruthy()
        expect(emitted![0]).toEqual([2, 5, true])
    })

    it("ignores non-activation keys", async () => {
        const ep = makeEpisode()
        const wrapper = mount(EpisodeTile, {
            props: { ep, selected: false },
        })

        await wrapper.find('[role="button"]').trigger("keydown", { key: "a" })
        await wrapper.find('[role="button"]').trigger("keydown", { key: "Tab" })
        await wrapper
            .find('[role="button"]')
            .trigger("keydown", { key: "ArrowRight" })

        expect(wrapper.emitted("toggle")).toBeUndefined()
    })

    it("calls preventDefault on Space so the page doesn't scroll", async () => {
        const ep = makeEpisode()
        const wrapper = mount(EpisodeTile, {
            props: { ep, selected: false },
        })

        const preventDefault = vi.fn()
        await wrapper
            .find('[role="button"]')
            .trigger("keydown", { key: " ", preventDefault })

        expect(preventDefault).toHaveBeenCalled()
    })
})

// Probe anchors consumed by /test-ux's Synclet UI probe. These data-testids
// are a contract: the post-deploy probe selects the tile and its synced
// indicator by them, so a rename here must update the skill's selector list.
describe("EpisodeTile probe anchors", () => {
    it("exposes episode-tile testid and a SxxExx data-ep-code", () => {
        const ep = makeEpisode({ season: 2, episode: 6 })
        const tile = mount(EpisodeTile, {
            props: { ep, selected: false },
        }).find('[data-testid="episode-tile"]')

        expect(tile.exists()).toBe(true)
        expect(tile.attributes("data-ep-code")).toBe("S02E06")
    })

    it("renders the synced indicator only when is_synced", () => {
        const synced = mount(EpisodeTile, {
            props: { ep: makeEpisode({ is_synced: true }), selected: false },
        })
        expect(
            synced.find('[data-testid="episode-synced-indicator"]').exists()
        ).toBe(true)

        const unsynced = mount(EpisodeTile, {
            props: { ep: makeEpisode({ is_synced: false }), selected: false },
        })
        expect(
            unsynced.find('[data-testid="episode-synced-indicator"]').exists()
        ).toBe(false)
    })
})

describe("EpisodeTile mark watched/unwatched toggle", () => {
    it("shows mark-watched (not unwatched) on an unwatched episode and emits it", async () => {
        const w = mount(EpisodeTile, {
            props: {
                ep: makeEpisode({ watch_state: "unwatched" }),
                selected: false,
            },
        })
        expect(w.find('[data-testid="mark-episode-watched"]').exists()).toBe(
            true
        )
        expect(w.find('[data-testid="mark-episode-unwatched"]').exists()).toBe(
            false
        )
        await w.find('[data-testid="mark-episode-watched"]').trigger("click")
        expect(w.emitted("mark-watched")?.[0]).toEqual([2, 5])
    })

    it("shows mark-unwatched (not watched) on a watched episode and emits it", async () => {
        const w = mount(EpisodeTile, {
            props: {
                ep: makeEpisode({
                    season: 4,
                    episode: 2,
                    watch_state: "watched",
                }),
                selected: false,
            },
        })
        expect(w.find('[data-testid="mark-episode-unwatched"]').exists()).toBe(
            true
        )
        expect(w.find('[data-testid="mark-episode-watched"]').exists()).toBe(
            false
        )
        await w.find('[data-testid="mark-episode-unwatched"]').trigger("click")
        expect(w.emitted("mark-unwatched")?.[0]).toEqual([4, 2])
    })

    it("treats in-progress as not-yet-watched (offers mark-watched)", () => {
        const w = mount(EpisodeTile, {
            props: {
                ep: makeEpisode({ watch_state: "progress" }),
                selected: false,
            },
        })
        expect(w.find('[data-testid="mark-episode-watched"]').exists()).toBe(
            true
        )
        expect(w.find('[data-testid="mark-episode-unwatched"]').exists()).toBe(
            false
        )
    })
})
