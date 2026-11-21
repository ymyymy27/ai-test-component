import { describe, expect, it, vi } from "vitest";

import { useUiStore } from "@/stores/ui";

describe("ui store", () => {
  it("resolves a superseded confirmation as cancelled instead of leaking it", async () => {
    const ui = useUiStore();
    const first = ui.askConfirm({ title: "第一个", body: "" });
    const second = ui.askConfirm({ title: "第二个", body: "" });
    await expect(first).resolves.toBeNull();
    ui.resolveConfirm("ok");
    await expect(second).resolves.toBe("ok");
  });

  it("keeps an error toast visible until the user dismisses it", async () => {
    vi.useFakeTimers();
    const ui = useUiStore();
    ui.showToast("保存失败", "error");
    vi.advanceTimersByTime(10_000);
    expect(ui.toastVisible).toBe(true);
    ui.dismissToast();
    vi.useRealTimers();
  });
});
