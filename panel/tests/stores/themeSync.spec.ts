import { createPinia, setActivePinia } from "pinia";
import { afterEach, describe, expect, it, vi } from "vitest";

describe("ui store host theme auto-sync", () => {
  afterEach(() => {
    delete window.aitestHost;
    delete document.documentElement.dataset.theme;
    localStorage.clear();
  });

  it("tracks window.aitestHost theme on initTheme and live onTheme events", async () => {
    vi.resetModules();
    const listeners: Array<(theme: string) => void> = [];
    window.aitestHost = {
      call: async () => ({ ok: true, data: {} }),
      getState: () => null,
      setState: () => undefined,
      getTheme: () => "dark",
      onTheme: (cb: (theme: string) => void) => {
        listeners.push(cb);
        return () => {
          const index = listeners.indexOf(cb);
          if (index >= 0) {
            listeners.splice(index, 1);
          }
        };
      },
    };

    const { useUiStore } = await import("@/stores/ui");
    setActivePinia(createPinia());
    const ui = useUiStore();
    ui.setTheme("auto");
    ui.initTheme();

    expect(ui.resolvedTheme).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");

    listeners.forEach((cb) => cb("light"));
    expect(ui.resolvedTheme).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
