import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import SettingsView from "@/views/SettingsView.vue";
import { session } from "@/state/session";

import { DOCTOR, PROJECT, installHost, resetSession } from "./fixtures";

const CONFIG = {
  model: { base_url: "https://api.example", name: "deepseek-chat", timeout_seconds: 90 },
  services: { api: { start: "uvicorn app:main", health: "http://localhost:8000/health" } },
  link: { log_paths: ["logs/app.log"], log_format: "jsonl" },
  runners: { http: { timeout_seconds: 30 } },
  evidence: { media_budget_mb: 100, video: false },
  l1: { build: "python -m compileall src", entry: ["app.main"] },
  data: { cleanup: "rm -rf aitest_{run_id}" },
};

function prepare(routes: Record<string, unknown> = {}) {
  const harness = installHost({ "project.config": { config: CONFIG }, ...routes });
  resetSession();
  session.project = PROJECT;
  session.doctor = DOCTOR;
  session.env = "ok";
  return harness;
}

async function openAdvanced(wrapper: VueWrapper) {
  await wrapper.find('[data-testid="advanced-config"] .advanced-head').trigger("click");
  await flushPromises();
}

describe("设置页主视图", () => {
  it("只显示环境、模型、工作空间与 GitHub 事实，不出现 JSON 输入框", async () => {
    prepare();
    const wrapper = mount(SettingsView);
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("环境自检");
    expect(text).toContain("3.13.1");
    expect(text).toContain("C:\\data\\ws");
    expect(text).toContain("模型密钥");
    expect(text).toContain("GitHub");
    expect(text).toContain("高级配置");
    expect(text).toContain("不安装软件");

    expect(wrapper.find("textarea").exists()).toBe(false);
    expect(wrapper.findAll('input[type="text"]')).toHaveLength(0);
    expect(wrapper.findAll("input").length).toBe(0);
    expect(wrapper.html()).not.toContain("api_key");
  });

  it("高级配置展开后一次只显示一个分组的 JSON", async () => {
    prepare();
    const wrapper = mount(SettingsView);
    await flushPromises();

    await openAdvanced(wrapper);
    const text = wrapper.text();
    expect(text).toContain("服务");
    expect(text).toContain("链路");
    expect(text).toContain("运行器");
    expect(text).toContain("证据预算");
    expect(text).toContain("源码可运行（L1）");
    expect(text).toContain("https://api.example");

    expect(wrapper.findAll("textarea")).toHaveLength(1);
    const services = wrapper.find("textarea#config-services");
    expect(services.exists()).toBe(true);
    expect((services.element as HTMLTextAreaElement).value).toContain("uvicorn app:main");

    // 切换到“证据预算”后只保留该分组。
    await wrapper.findAll('[role="tab"]').find((tab) => tab.text().includes("证据预算"))?.trigger("click");
    expect(wrapper.findAll("textarea")).toHaveLength(1);
    expect(wrapper.find("textarea#config-services").exists()).toBe(false);
    expect(wrapper.find("textarea#config-evidence").exists()).toBe(true);
  });

  it("不提供密钥编辑入口", async () => {
    prepare();
    const wrapper = mount(SettingsView);
    await flushPromises();
    await openAdvanced(wrapper);

    expect(wrapper.find("textarea#config-model").exists()).toBe(false);
    expect(wrapper.html()).not.toContain("api_key");
  });
});

describe("设置页项目配置", () => {
  it("保存可编辑分组并调用 project.config update", async () => {
    const harness = prepare();
    const wrapper = mount(SettingsView);
    await flushPromises();
    await openAdvanced(wrapper);

    await wrapper
      .find("textarea#config-services")
      .setValue(JSON.stringify({ api: { start: "python -m app" } }));
    await wrapper.findAll('[role="tab"]').find((tab) => tab.text().includes("证据预算"))?.trigger("click");
    await wrapper
      .find("textarea#config-evidence")
      .setValue(JSON.stringify({ media_budget_mb: 50 }));
    await wrapper.findAll("button").find((button) => button.text() === "保存项目配置")?.trigger("click");
    await flushPromises();

    const save = harness.calls.find(
      (call) => call.command === "project.config" && call.payload["action"] === "update",
    );
    expect(save).toBeTruthy();
    const patch = save?.payload["patch"] as Record<string, Record<string, unknown>>;
    expect(patch.services).toEqual({ api: { start: "python -m app" } });
    expect(patch.evidence).toEqual({ media_budget_mb: 50 });
    expect(patch.link).toEqual(CONFIG.link);
    expect(wrapper.text()).toContain("项目配置已保存");
    expect(wrapper.text()).not.toContain("api_key");
  });

  it("JSON 写错时跳到对应分组，指出问题与下一步", async () => {
    prepare();
    const wrapper = mount(SettingsView);
    await flushPromises();
    await openAdvanced(wrapper);

    await wrapper.findAll('[role="tab"]').find((tab) => tab.text().includes("链路"))?.trigger("click");
    await wrapper.find("textarea#config-link").setValue("{not json");
    await wrapper.findAll("button").find((button) => button.text() === "保存项目配置")?.trigger("click");
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("“链路”的 JSON 无法解析");
    expect(text).toContain("下一步");
    expect(wrapper.find("textarea#config-link").exists()).toBe(true);
  });

  it("保存失败时显示哪里错了和下一步", async () => {
    prepare({
      "project.config": (payload: Record<string, unknown>) => {
        if (payload["action"] === "update") {
          return {
            __error: {
              code: "INVALID_REQUEST",
              message: "项目配置不允许包含密钥字段：services.api.api_key",
              next_step: "把密钥放到用户配置或环境变量中",
            },
          };
        }
        return { config: CONFIG };
      },
    });
    const wrapper = mount(SettingsView);
    await flushPromises();
    await openAdvanced(wrapper);

    await wrapper.findAll("button").find((button) => button.text() === "保存项目配置")?.trigger("click");
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("提交的内容不完整或有字段不合法");
    expect(text).toContain("把密钥放到用户配置或环境变量中");
  });
});

describe("服务配置说明", () => {
  it("同时说明单服务形状与 服务名→配置 映射形状", async () => {
    prepare();
    const wrapper = mount(SettingsView);
    await flushPromises();
    await openAdvanced(wrapper);

    const text = wrapper.text();
    expect(text).toContain("单服务：{ start, health, stop, ... }");
    expect(text).toContain("服务名 → 配置，取第一个");
  });
});
