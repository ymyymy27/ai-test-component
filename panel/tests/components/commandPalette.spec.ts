import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { describe, expect, it, vi } from "vitest";

import CommandPalette from "@/components/CommandPalette.vue";
import { useUiStore } from "@/stores/ui";
import { setRepository, useWorkspaceStore } from "@/stores/workspace";
import { seedProject } from "../fixtures";

function makeTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div />" } },
      { path: "/projects/:projectId/overview", component: { template: "<div />" } },
      { path: "/projects/:projectId/architecture", component: { template: "<div />" } },
      { path: "/projects/:projectId/issues", component: { template: "<div />" } },
      { path: "/projects/:projectId/history", component: { template: "<div />" } },
      { path: "/projects/:projectId/settings", component: { template: "<div />" } },
    ],
  });
}

describe("CommandPalette", () => {
  it("filters commands and navigates when executing a matching command", async () => {
    const seeded = await seedProject("D:/work/cmd-demo", "命令演示");
    setRepository(seeded.repo);
    const workspace = useWorkspaceStore();
    await workspace.bootstrap();

    const router = makeTestRouter();
    await router.push(`/projects/${seeded.projectId}/overview`);
    await router.isReady();
    const pushSpy = vi.spyOn(router, "push");

    const ui = useUiStore();
    const wrapper = mount(CommandPalette, {
      global: { plugins: [router] },
    });

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);

    ui.openCommandPalette();
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true);

    const search = wrapper.get(".cmd-input");
    await search.setValue("架构");

    const options = wrapper.findAll('[role="option"]');
    expect(options.length).toBeGreaterThan(0);
    expect(options[0]?.text()).toContain("架构");

    await options[0]?.trigger("click");
    expect(ui.commandPaletteOpen).toBe(false);
    expect(pushSpy).toHaveBeenCalledWith(`/projects/${seeded.projectId}/architecture`);
  });

  it("switches theme and closes palette when toggling appearance command", async () => {
    const router = makeTestRouter();
    await router.push("/");
    await router.isReady();

    const ui = useUiStore();
    ui.setTheme("light");
    ui.openCommandPalette();

    const wrapper = mount(CommandPalette, {
      global: { plugins: [router] },
    });
    const themeBtn = wrapper
      .findAll('[role="option"]')
      .find((node) => node.text().includes("曜石主题"));
    expect(themeBtn).toBeDefined();
    await themeBtn?.trigger("click");

    expect(ui.resolvedTheme).toBe("dark");
    expect(ui.commandPaletteOpen).toBe(false);
  });
});
