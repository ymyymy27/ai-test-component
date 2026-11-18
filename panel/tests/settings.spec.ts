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

  it("新建规则版本调用 rules.save", async () => {
    const harness = installHost(workspaceRoutes());
    const wrapper = mount(SettingsView);
    await flushPromises();
    await wrapper.findAll("button").find((item) => item.text() === "新建规则版本")!.trigger("click");
    const byPlaceholder = (value: string) =>
      wrapper.findAll("input").find((item) => item.attributes("placeholder") === value)!;
    await byPlaceholder("例如：下单回归规则").setValue("回归规则");
    await byPlaceholder("例如：1.0.0").setValue("1.0.1");
    const areas = wrapper.findAll("textarea");
    await areas[0]!.setValue("按页面主流程验证");
    await areas[1]!.setValue("只读操作\n不访问外网");
    await areas[2]!.setValue("截图");
    await wrapper.findAll("button").find((item) => item.text() === "保存规则版本")!.trigger("click");
    await flushPromises();
    const call = harness.calls.find((item) => item.command === "rules.save");
    expect(call?.payload["name"]).toBe("回归规则");
    expect(call?.payload["version"]).toBe("1.0.1");
    expect(call?.payload["ai_rules"]).toEqual(["只读操作", "不访问外网"]);
    expect(call?.payload["evidence_requirements"]).toEqual(["截图"]);
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
