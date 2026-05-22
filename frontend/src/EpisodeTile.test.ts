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
