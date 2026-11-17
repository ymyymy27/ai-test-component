import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import AnalysisPanel from "@/components/AnalysisPanel.vue";
import type { Analysis } from "@/api/types";

function render(analysis?: Analysis, canGenerate = true, notNeeded = false) {
  return mount(AnalysisPanel, {
    props: { analysis, canGenerate, notNeeded },
  });
}

describe("AI 失败分析：未生成原因", () => {
  it("没有失败且未生成分析对象时也说明不需要生成", () => {
    const wrapper = render(undefined, false, true);

    expect(wrapper.text()).toContain("本次没有失败，不需要生成分析。");
    expect(wrapper.findAll("button")).toHaveLength(0);
  });

  it("没有失败时说明不需要生成，且不提供生成按钮", () => {
    const wrapper = render({ status: "not_generated", reason: "no_failures" });

    expect(wrapper.text()).toContain("本次没有失败，不需要生成分析。");
    expect(wrapper.findAll("button")).toHaveLength(0);
  });

  it("模型缺失时说明未配置或不可用", () => {
    const wrapper = render({ status: "not_generated", reason: "model_missing" });

    expect(wrapper.text()).toContain("模型未配置或不可用，分析未生成。");
    expect(wrapper.find("button").exists()).toBe(true);
  });

  it("模型输出不合格时说明已隐藏", () => {
    const wrapper = render({ status: "invalid", reason: "model_invalid" });

    expect(wrapper.text()).toContain("模型输出不合格，已隐藏。");
    expect(wrapper.text()).not.toContain("半截内容");
  });

  it("模型调用失败时提示稍后重试", () => {
    const wrapper = render({ status: "not_generated", reason: "model_error" });

    expect(wrapper.text()).toContain("模型调用失败，可稍后重试。");
    expect(wrapper.find("button").exists()).toBe(true);
  });

  it("其他原因只说未生成，不猜成模型不可用", () => {
    const wrapper = render({ status: "not_generated", reason: "" });

    expect(wrapper.text()).toContain("分析未生成。");
    expect(wrapper.text()).not.toContain("模型未配置或不可用");
  });

  it("没有分析数据且不可生成时也保持中性文案", () => {
    const wrapper = render(undefined, false);

    expect(wrapper.text()).toContain("分析未生成。");
    expect(wrapper.text()).not.toContain("模型当前不可用");
  });
});
