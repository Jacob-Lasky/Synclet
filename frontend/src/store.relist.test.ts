import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const { libraryMock } = vi.hoisted(() => ({ libraryMock: vi.fn() }))

vi.mock("./api", () => ({ api: { library: libraryMock } }))

import { relistSettled, waitForRelist } from "./store"
import type { LibraryStatus } from "./types"

const ASKED = 1000

function status(
    libs: Record<string, Partial<LibraryStatus["libraries"][string]>>,
    gridPending = false
): LibraryStatus {
    const libraries: LibraryStatus["libraries"] = {}
    for (const [id, lib] of Object.entries(libs)) {
        libraries[id] = {
            titles: 1,
            listed_at: null,
            failed_at: null,
            age_s: 0,
            listing: false,
            ...lib,
        }
    }
    return { relist_interval_s: 21600, libraries, grid_pending: gridPending }
}

describe("relistSettled", () => {
    it("waits for every library to list after the click", () => {
        expect(
            relistSettled(
                status({ tv: { listed_at: 1001 }, movies: { listed_at: 999 } }),
                ASKED
            )
        ).toBe(false)
        expect(
            relistSettled(
                status({
                    tv: { listed_at: 1001 },
                    movies: { listed_at: 1002 },
                }),
                ASKED
            )
        ).toBe(true)
    })

    it("counts a failed listing as settled (last-good kept)", () => {
        expect(
            relistSettled(
                status({ tv: { listed_at: 10, failed_at: 1001 } }),
                ASKED
            )
        ).toBe(true)
    })

    it("is not settled while a listing or the grid rebuild is running", () => {
        expect(
            relistSettled(
                status({ tv: { listed_at: 1001, listing: true } }),
                ASKED
            )
        ).toBe(false)
        expect(
            relistSettled(status({ tv: { listed_at: 1001 } }, true), ASKED)
        ).toBe(false)
    })
})

describe("waitForRelist", () => {
    beforeEach(() => {
        vi.useFakeTimers()
        libraryMock.mockReset()
    })
    afterEach(() => vi.useRealTimers())

    it("polls until settled", async () => {
        libraryMock
            .mockResolvedValueOnce(
                status({ tv: { listed_at: 1, listing: true } })
            )
            .mockResolvedValueOnce(status({ tv: { listed_at: 1001 } }))
        const done = waitForRelist(ASKED)
        await vi.advanceTimersByTimeAsync(1000)
        await expect(done).resolves.toBe(true)
        expect(libraryMock).toHaveBeenCalledTimes(2)
    })

    it("gives up after the wait budget instead of spinning forever", async () => {
        libraryMock.mockResolvedValue(status({ tv: { listing: true } }))
        const done = waitForRelist(ASKED)
        await vi.advanceTimersByTimeAsync(181_000)
        await expect(done).resolves.toBe(false)
    })
})
