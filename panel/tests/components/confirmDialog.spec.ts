import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ConfirmDialog from "@/components/ConfirmDialog.vue";
import { useUiStore } from "@/stores/ui";

describe("ConfirmDialog", () => {
  it("clears the required-text error as soon as the user types", async () => {
    const ui = useUiStore();
    const wrapper = mount(ConfirmDialog);
    const pending = ui.askConfirm({
      title: "关闭问题",
      body: "写下理由",
      requireText: true,
      textLabel: "关闭理由",
    });
    await wrapper.vm.$nextTick();
    await wrapper.find(".dialog-confirm").trigger("click");
    expect(wrapper.find(".field-error").text()).toContain("关闭理由");
    expect(wrapper.find("textarea").attributes("aria-invalid")).toBe("true");

    await wrapper.find("textarea").setValue("复测通过");
    expect(wrapper.find(".field-error").exists()).toBe(false);
    expect(wrapper.find("textarea").attributes("aria-invalid")).toBeUndefined();

    await wrapper.find(".dialog-cancel").trigger("click");
    await expect(pending).resolves.toBeNull();
  });
});
