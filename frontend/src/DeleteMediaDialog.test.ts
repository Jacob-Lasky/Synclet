import { describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import DeleteMediaDialog from "./components/DeleteMediaDialog.vue"

const { preview, deleteMedia } = vi.hoisted(() => ({
    preview: vi.fn(),
    deleteMedia: vi.fn(),
}))
preview.mockResolvedValue({
    source_files: 3,
    offline_files: 1,
    source_bytes: 1000,
    offline_bytes: 500,
    fingerprint: "preview-fingerprint",
})
deleteMedia.mockResolvedValue({
    source_deleted: 3,
    offline_deleted: 1,
    source_bytes: 1000,
    offline_bytes: 500,
    title_remaining: false,
})

vi.mock("./api", () => ({
    api: { mediaDeletePreview: preview, mediaDelete: deleteMedia },
}))
vi.mock("./store", () => ({ humanSize: (n: number) => `${n} B` }))

describe("DeleteMediaDialog", () => {
    it("shows the preview and requires the exact title before deleting", async () => {
        HTMLDialogElement.prototype.showModal = vi.fn()
        deleteMedia.mockClear()
        const body = {
            lib: "tv",
            folder: "Show (2026)",
            selection_type: "episodes" as const,
            episodes: [[2, 1] as [number, number]],
        }
        const wrapper = mount(DeleteMediaDialog, {
            props: { body, title: "Show", scopeLabel: "1 episode of Show" },
        })
        await flushPromises()
        expect(preview).toHaveBeenCalledWith(body)
        expect(wrapper.find('[data-testid="delete-preview"]').text()).toContain(
            "offline"
        )
        const button = wrapper.find('[data-testid="confirm-delete-media"]')
        expect(button.attributes("disabled")).toBeDefined()
        await wrapper.find("input").setValue("Wrong")
        expect(button.attributes("disabled")).toBeDefined()
        await wrapper.find("input").setValue("Show")
        expect(button.attributes("disabled")).toBeDefined()
        await wrapper.find('input[type="password"]').setValue("test-key")
        expect(button.attributes("disabled")).toBeUndefined()
        await button.trigger("click")
        await flushPromises()
        expect(deleteMedia).toHaveBeenCalledWith(
            body,
            "preview-fingerprint",
            "test-key"
        )
        expect(wrapper.emitted("deleted")).toHaveLength(1)
    })

    it("can refresh a stale preview after the delete request is rejected", async () => {
        HTMLDialogElement.prototype.showModal = vi.fn()
        preview.mockClear()
        deleteMedia.mockRejectedValueOnce(
            new Error("409 media changed since preview")
        )
        const wrapper = mount(DeleteMediaDialog, {
            props: {
                body: {
                    lib: "movies",
                    folder: "1917",
                    selection_type: "movie",
                    episodes: [],
                },
                title: "1917",
                scopeLabel: "1917",
            },
        })
        await flushPromises()
        await wrapper.find("input").setValue("1917")
        await wrapper.find('input[type="password"]').setValue("test-key")
        await wrapper
            .find('[data-testid="confirm-delete-media"]')
            .trigger("click")
        await flushPromises()
        expect(
            wrapper
                .find('[data-testid="confirm-delete-media"]')
                .attributes("disabled")
        ).toBeDefined()
        await wrapper.get('[data-testid="preview-again"]').trigger("click")
        await flushPromises()
        expect(preview).toHaveBeenCalledTimes(2)
        expect(wrapper.find('[data-testid="delete-preview"]').exists()).toBe(
            true
        )
    })
})
