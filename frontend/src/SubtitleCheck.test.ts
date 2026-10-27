import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import SubtitleCheck from "./components/SubtitleCheck.vue"

const check = (status: string) => ({
    videos: [{ video: "film.mkv", status, sidecars: [] }],
    warnings: [],
    note: "Metadata can be stale",
})

describe("Subtitle evidence", () => {
    it("keeps unknown need distinct from known missing forced subtitles", () => {
        const unknown = mount(SubtitleCheck, {
            props: {
                check: check("no_forced_track_reported"),
                required: false,
            },
        })
        expect(unknown.text()).toContain("need unknown")
        expect(unknown.find('[role="alert"]').exists()).toBe(false)
        const missing = mount(SubtitleCheck, {
            props: { check: check("required_forced_missing"), required: true },
        })
        expect(missing.get('[role="alert"]').text()).toContain(
            "expected but missing"
        )
        expect(missing.text()).toContain("burned into the video")
    })
    it("shows missing source and unverified offline sidecars honestly", async () => {
        const evidence = check("forced_sidecar_available")
        const wrapper = mount(SubtitleCheck, {
            props: {
                check: {
                    ...evidence,
                    videos: [
                        {
                            ...evidence.videos[0]!,
                            sidecars: [
                                {
                                    name: "film.en.forced.srt",
                                    source_exists: false,
                                    offline: "not_verified",
                                },
                            ],
                        },
                    ],
                },
                required: false,
            },
        })
        expect(wrapper.text()).toContain("source file missing")
        expect(wrapper.text()).not.toContain("offline copy verified")
        await wrapper.get("input").setValue(true)
        expect(wrapper.emitted("required")).toEqual([[true]])
    })
})
