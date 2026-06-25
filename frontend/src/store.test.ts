/**
 * Tests for the pure helpers in store.ts.
 *
 * The fuzzy-score algorithm here is a port of synclet/fuzzy.py:fuzzy_score
 * on the backend. Both sides must agree on rankings — any change here
 * needs the matching change in the Python file, and vice versa.
 */
import { describe, expect, it } from "vitest"
import { store } from "./store"
import {
    humanSize,
    epCode,
    libraryShort,
    libraryLabel,
    recordScrobbled,
    recordUnwatched,
    isScrobbledThisSession,
    isUnwatchedThisSession,
} from "./store"

// fuzzyScore is module-private (not exported). We re-import the file and
// poke at the implementation via an exported re-export. Add export when needed.

describe("humanSize", () => {
    it("formats bytes under 1 KB", () => {
        expect(humanSize(0)).toBe("0 B")
        expect(humanSize(512)).toBe("512 B")
    })

    it("formats kilobytes", () => {
        expect(humanSize(1024)).toBe("1.0 KB")
        expect(humanSize(2048)).toBe("2.0 KB")
    })

    it("formats megabytes", () => {
        expect(humanSize(1024 * 1024)).toBe("1.0 MB")
        expect(humanSize(5 * 1024 * 1024)).toBe("5.0 MB")
    })

    it("formats gigabytes", () => {
        expect(humanSize(1024 ** 3)).toBe("1.0 GB")
    })

    it("uses integer formatting for large values within a unit", () => {
        expect(humanSize(50 * 1024)).toBe("50 KB")
    })

    it("scales up to terabytes and beyond", () => {
        expect(humanSize(1024 ** 4)).toBe("1.0 TB")
    })
})

describe("epCode", () => {
    it("pads single-digit numbers", () => {
        expect(epCode(1, 1)).toBe("S01E01")
        expect(epCode(2, 9)).toBe("S02E09")
    })

    it("does not truncate two-digit numbers", () => {
        expect(epCode(12, 34)).toBe("S12E34")
    })

    it("handles zero", () => {
        // Specials are S00 — must not lose the digit
        expect(epCode(0, 1)).toBe("S00E01")
    })
})

describe("libraryShort / libraryLabel", () => {
    it("returns fallback when libraries are empty", () => {
        store.libraries = []
        expect(libraryShort("tv")).toBe("??")
        expect(libraryLabel("tv")).toBe("tv") // falls back to id
    })

    it("returns the API-supplied short and label", () => {
        store.libraries = [
            {
                id: "tv",
                label: "TV",
                short: "TV",
                kind: "show",
                sync_sub: "tv",
            },
            {
                id: "movies",
                label: "Movies",
                short: "MO",
                kind: "movie",
                sync_sub: "movies",
            },
        ]
        expect(libraryShort("tv")).toBe("TV")
        expect(libraryShort("movies")).toBe("MO")
        expect(libraryLabel("movies")).toBe("Movies")
    })

    it("returns fallback for unknown library id", () => {
        store.libraries = [
            {
                id: "tv",
                label: "TV",
                short: "TV",
                kind: "show",
                sync_sub: "tv",
            },
        ]
        expect(libraryShort("nonexistent")).toBe("??")
    })
})

describe("session watch-state overlay (scrobbled vs unwatched)", () => {
    // Distinct keys per case so the module-level overlay Sets don't leak
    // between assertions (there is no reset hook; the overlay clears on reload).
    it("records each direction independently for separate items", () => {
        recordScrobbled("tv", "A", 1, 1)
        recordUnwatched("tv", "B", 1, 1)
        expect(isScrobbledThisSession("tv", "A", 1, 1)).toBe(true)
        expect(isUnwatchedThisSession("tv", "A", 1, 1)).toBe(false)
        expect(isUnwatchedThisSession("tv", "B", 1, 1)).toBe(true)
        expect(isScrobbledThisSession("tv", "B", 1, 1)).toBe(false)
    })

    it("is last-write-wins: marking unwatched clears a prior scrobble", () => {
        recordScrobbled("tv", "C", 2, 3)
        expect(isScrobbledThisSession("tv", "C", 2, 3)).toBe(true)
        recordUnwatched("tv", "C", 2, 3)
        expect(isUnwatchedThisSession("tv", "C", 2, 3)).toBe(true)
        expect(isScrobbledThisSession("tv", "C", 2, 3)).toBe(false)
    })

    it("is last-write-wins in the other direction too", () => {
        recordUnwatched("movies", "D")
        expect(isUnwatchedThisSession("movies", "D")).toBe(true)
        recordScrobbled("movies", "D")
        expect(isScrobbledThisSession("movies", "D")).toBe(true)
        expect(isUnwatchedThisSession("movies", "D")).toBe(false)
    })
})
