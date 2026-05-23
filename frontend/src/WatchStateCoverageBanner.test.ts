/**
 * The banner is the user's only visible cue that a library round-trips via
 * Plex direct rather than WatchState. If it stops rendering when WatchState
 * lacks coverage, the user is back to "marks revert silently" , the exact
 * UX bug this feature exists to surface.
 *
 * The threshold (observed < expected * 0.5) catches Jake's specific failure
 * mode: YouTube has a handful of Jellyfin-sourced rows but Plex section 6
 * advertises hundreds of episodes, so the gap is real even though observed
 * is not literally zero.
 */
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import WatchStateCoverageBanner from "./components/WatchStateCoverageBanner.vue"
import { store } from "./store"
import type { CoverageEntry } from "./types"

function setCoverage(entries: CoverageEntry[] | null): void {
    store.coverage = entries
}

describe("WatchStateCoverageBanner", () => {
    it("renders nothing while coverage is still loading", () => {
        setCoverage(null)
        const wrapper = mount(WatchStateCoverageBanner)
        expect(wrapper.find('[data-testid="coverage-banner"]').exists()).toBe(
            false
        )
    })

    it("renders nothing when every library is well-covered", () => {
        setCoverage([
            {
                id: "tv",
                label: "TV",
                section: 2,
                kind: "show",
                watchstate_rows: 12345,
                expected_rows: 13000,
            },
            {
                id: "movies",
                label: "Movies",
                section: 1,
                kind: "movie",
                watchstate_rows: 230,
                expected_rows: 250,
            },
        ])
        const wrapper = mount(WatchStateCoverageBanner)
        expect(wrapper.find('[data-testid="coverage-banner"]').exists()).toBe(
            false
        )
    })

    it("names the uncovered library when it has zero rows", () => {
        setCoverage([
            {
                id: "tv",
                label: "TV",
                section: 2,
                kind: "show",
                watchstate_rows: 12345,
                expected_rows: 13000,
            },
            {
                id: "YouTube",
                label: "YouTube",
                section: 6,
                kind: "youtube",
                watchstate_rows: 0,
                expected_rows: 650,
            },
        ])
        const wrapper = mount(WatchStateCoverageBanner)
        const banner = wrapper.find('[data-testid="coverage-banner"]')
        expect(banner.exists()).toBe(true)
        const text = banner.text()
        expect(text).toContain("YouTube")
        // Well-covered libraries are NOT named , the banner is about the
        // gap, not the inventory.
        expect(text).not.toContain("TV,")
        expect(text).not.toContain(" TV ")
    })

    it("flags partial coverage where Jellyfin overlay leaks into a YT section", () => {
        // The exact shape we see in production: a handful of Jellyfin-sourced
        // rows in WatchState join to Plex's YouTube section, but Plex itself
        // advertises hundreds of episodes. Observed is non-zero but well
        // below expected → banner must still fire.
        setCoverage([
            {
                id: "YouTube",
                label: "YouTube",
                section: 6,
                kind: "youtube",
                watchstate_rows: 17,
                expected_rows: 650,
            },
        ])
        const wrapper = mount(WatchStateCoverageBanner)
        expect(wrapper.find('[data-testid="coverage-banner"]').exists()).toBe(
            true
        )
    })

    it("does not flag a library that hits the coverage threshold", () => {
        // Right on the boundary: observed = expected * 0.5 should pass.
        setCoverage([
            {
                id: "tv-4kUHD",
                label: "TV 4K",
                section: 12,
                kind: "show",
                watchstate_rows: 50,
                expected_rows: 100,
            },
        ])
        const wrapper = mount(WatchStateCoverageBanner)
        expect(wrapper.find('[data-testid="coverage-banner"]').exists()).toBe(
            false
        )
    })

    it("names every uncovered library when multiple are missing", () => {
        setCoverage([
            {
                id: "tv",
                label: "TV",
                section: 2,
                kind: "show",
                watchstate_rows: 12345,
                expected_rows: 13000,
            },
            {
                id: "YouTube",
                label: "YouTube",
                section: 6,
                kind: "youtube",
                watchstate_rows: 0,
                expected_rows: 650,
            },
            {
                id: "movies-4kUHD",
                label: "Movies 4K",
                section: 7,
                kind: "movie",
                watchstate_rows: 0,
                expected_rows: 200,
            },
        ])
        const wrapper = mount(WatchStateCoverageBanner)
        const text = wrapper.find('[data-testid="coverage-banner"]').text()
        expect(text).toContain("YouTube")
        expect(text).toContain("Movies 4K")
    })
})
