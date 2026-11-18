import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it } from "vitest";

import SettingsView from "@/views/SettingsView.vue";
import { DOCTOR, PROJECT, installHost, resetWorkspace, workspaceRoutes } from "./fixtures";
import { workspace } from "@/state/workspace";

describe("设置页", () => {
  beforeEach(() => {
    resetWorkspace();
    workspace.project = PROJECT;
    workspace.doctor = DOCTOR;
    installHost(workspaceRoutes());
  });

  it("显示环境自检事实", async () => {
    const wrapper = mount(SettingsView);
    await flushPromises();
    const text = wrapper.text();
    expect(text).toContain("环境自检");
    expect(text).toContain("3.13.1");
    expect(text).toContain("C:\\data\\ws");
    expect(text).toContain("模型");
    expect(text).toContain("GitHub");
  });

  it("保存项目配置调用 project.config update", async () => {
    const harness = installHost(workspaceRoutes());
    const wrapper = mount(SettingsView);
    await flushPromises();
    await wrapper.findAll("button").find((item) => item.text() === "编辑项目配置")!.trigger("click");
    const textarea = wrapper.find("textarea");
    await textarea.setValue('{"api":{"health":"http://localhost:8000/health"}}');
    const button = wrapper.findAll("button").find((item) => item.text() === "保存这段配置");
    await button!.trigger("click");
    await flushPromises();
    const call = [...harness.calls].reverse().find((item) => item.command === "project.config");
    expect(call?.payload["action"]).toBe("update");
    expect(call?.payload["patch"]).toEqual({
      services: { api: { health: "http://localhost:8000/health" } },
    });
  });

  it("配置不是对象时指出下一步", async () => {
    const wrapper = mount(SettingsView);
    await flushPromises();
    await wrapper.findAll("button").find((item) => item.text() === "编辑项目配置")!.trigger("click");
    await wrapper.find("textarea").setValue("[1,2]");
    const button = wrapper.findAll("button").find((item) => item.text() === "保存这段配置");
    await button!.trigger("click");
    expect(wrapper.text()).toContain("必须是一个对象");
  });
});
