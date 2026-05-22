import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import SyncthingView from "./components/SyncthingView.vue"
import type { SyncthingFolder, SyncthingOverview } from "./types"

// Stub the api module so each test controls the wire data and we can
// count poll cycles.
const mockOverview = vi.fn<() => Promise<SyncthingOverview>>()
vi.mock("./api", () => ({
    api: {
        syncthingOverview: (): Promise<SyncthingOverview> => mockOverview(),
    },
}))

// Named so test variants can spread it without an indexed lookup (which is
// `T | undefined` under noUncheckedIndexedAccess).
const HAPPY_FOLDER: SyncthingFolder = {
    folder_id: "default",
    label: "Media",
    percent: 75,
    state: "syncing",
    need_bytes: 250,
    in_sync_bytes: 750,
    global_bytes: 1000,
    devices: [
        {
            device_id: "DEV_LAPTOP",
            name: "Laptop",
            completion: 100,
            need_bytes: 0,
            connected: true,
            paused: false,
        },
        {
            device_id: "DEV_PHONE",
            name: "Phone",
            completion: 60,
            need_bytes: 400,
            connected: false,
            paused: false,
        },
    ],
}

const HAPPY_OVERVIEW: SyncthingOverview = {
    configured: true,
    folders: [HAPPY_FOLDER],
}

describe("SyncthingView", () => {
    beforeEach(() => {
        mockOverview.mockReset()
    })

    afterEach(() => {
        // Belt-and-suspenders: tests that opted into fake timers must restore
        // real ones before the next test runs, even if they threw mid-flight.
        vi.useRealTimers()
    })

    describe("render states", () => {
        it("shows the not-configured panel when backend reports configured=false", async () => {
            mockOverview.mockResolvedValue({ configured: false, folders: [] })
            const wrapper = mount(SyncthingView)
            await flushPromises()
            expect(wrapper.text()).toContain("Syncthing not configured")
            expect(wrapper.text()).toContain("SYNCTHING_URL")
            expect(wrapper.text()).toContain("SYNCTHING_API_KEY")
        })

        it("renders folder rows + per-device chips when populated", async () => {
            mockOverview.mockResolvedValue(HAPPY_OVERVIEW)
            const wrapper = mount(SyncthingView)
            await flushPromises()

            const text = wrapper.text()
            expect(text).toContain("Media")
            expect(text).toContain("75%")
            expect(text).toContain("syncing")

            // Both devices visible with their completion and state tags.
            expect(text).toContain("Laptop")
            expect(text).toContain("100%")
            expect(text).toContain("in sync")
            expect(text).toContain("Phone")
            expect(text).toContain("60%")
            expect(text).toContain("offline")
        })

        it("shows the empty-folders message when configured but Syncthing has no folders", async () => {
            mockOverview.mockResolvedValue({ configured: true, folders: [] })
            const wrapper = mount(SyncthingView)
            await flushPromises()
            expect(wrapper.text()).toContain("no folders are configured")
        })

        it("shows the no-remote-devices message for a folder without sharers", async () => {
            mockOverview.mockResolvedValue({
                configured: true,
                folders: [
                    {
                        ...HAPPY_FOLDER,
                        devices: [],
                    },
                ],
            })
            const wrapper = mount(SyncthingView)
            await flushPromises()
            expect(wrapper.text()).toContain(
                "No remote devices share this folder"
            )
        })
    })

    describe("polling cadence", () => {
        it("polls the overview endpoint every 8s while mounted", async () => {
            mockOverview.mockResolvedValue(HAPPY_OVERVIEW)
            vi.useFakeTimers()

            const wrapper = mount(SyncthingView)
            // Initial load fires synchronously from onMounted (returns a Promise
            // we need to flush). Do NOT use vi.runOnlyPendingTimersAsync here:
            // it fires the setInterval's first tick prematurely.
            await flushPromises()
            expect(mockOverview).toHaveBeenCalledTimes(1)

            // Just under 8s: no new call yet.
            await vi.advanceTimersByTimeAsync(7000)
            expect(mockOverview).toHaveBeenCalledTimes(1)

            // Crossing the 8s boundary fires the next poll.
            await vi.advanceTimersByTimeAsync(1500)
            expect(mockOverview).toHaveBeenCalledTimes(2)

            // Another full 8s cycle.
            await vi.advanceTimersByTimeAsync(8000)
            expect(mockOverview).toHaveBeenCalledTimes(3)

            wrapper.unmount()
        })

        it("stops polling after unmount", async () => {
            mockOverview.mockResolvedValue(HAPPY_OVERVIEW)
            vi.useFakeTimers()

            const wrapper = mount(SyncthingView)
            await flushPromises()
            expect(mockOverview).toHaveBeenCalledTimes(1)

            wrapper.unmount()

            // After unmount, no further polls should land regardless of how
            // much time passes.
            await vi.advanceTimersByTimeAsync(30_000)
            expect(mockOverview).toHaveBeenCalledTimes(1)
        })
    })
})
