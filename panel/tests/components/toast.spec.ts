import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Toast from "@/components/Toast.vue";
import { useUiStore } from "@/stores/ui";

async function tick(): Promise<void> {
  await nextTick();
  await Promise.resolve();
}

describe("toast feedback", () => {
  beforeEach(() => {
    vi.useRealTimers();
    setActivePinia(createPinia());
  });

  it("uses alert semantics for errors", async () => {
    const ui = useUiStore();
    const wrapper = mount(Toast);

    ui.showToast("保存失败", "error");
    await tick();
    expect(wrapper.attributes("role")).toBe("alert");
    expect(wrapper.attributes("aria-live")).toBe("assertive");
  });

  it("marks success messages with the pass tone", async () => {
    const ui = useUiStore();
    const wrapper = mount(Toast);

    ui.showToast("已保存", "success");
    await tick();
    expect(wrapper.attributes("role")).toBe("status");
    expect(wrapper.classes()).toContain("is-success");
  });

  it("can be dismissed and then shows the next queued message", async () => {
    vi.useFakeTimers();
    const ui = useUiStore();
    const wrapper = mount(Toast);

    ui.showToast("保存失败", "error");
    ui.showToast("第二条", "info");
    await tick();
    expect(ui.toast?.message).toBe("保存失败");

    await wrapper.find(".toast-close").trigger("click");
    vi.advanceTimersByTime(200);
    await tick();
    expect(ui.toast?.message).toBe("第二条");
    expect(wrapper.find(".toast-close").exists()).toBe(true);
  });

  it("copies the message when the copy action is used", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const ui = useUiStore();
    const wrapper = mount(Toast);
    ui.showToast("需要复制的提示", "info");
    await tick();

    await wrapper.find(".toast-copy").trigger("click");
    expect(writeText).toHaveBeenCalledWith("需要复制的提示");
  });

  it("lets an error replace a visible non-error message", () => {
    const ui = useUiStore();
    ui.showToast("正在保存", "info");
    ui.showToast("保存失败", "error");
    expect(ui.toast?.message).toBe("保存失败");
    expect(ui.toast?.tone).toBe("error");
  });
});
