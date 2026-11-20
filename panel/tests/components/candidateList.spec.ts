import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CandidateList from "@/components/CandidateList.vue";
import type { Candidate } from "@/domain/types";

const candidates: Candidate[] = [
  {
    id: "cand_1",
    title: "验证启动",
    reason: "来自目录",
    source: "来自目录",
    intent: "business",
    featureNames: ["启动与主流程"],
    itemCount: 1,
    estimatedScope: "启动与主流程 · 约 1 项",
    unknown: "环境变量未确认",
  },
  {
    id: "cand_2",
    title: "验证数据",
    reason: "来自模块",
    source: "来自模块",
    intent: "business",
    featureNames: ["数据写入与查询"],
    itemCount: 2,
    estimatedScope: "数据写入与查询 · 约 2 项",
    unknown: "判定字段未确认",
  },
];

describe("CandidateList", () => {
  it("shows evidence, estimated scope and unknown items", () => {
    const wrapper = mount(CandidateList, {
      props: { candidates, selectedId: "cand_1", busy: false },
    });
    expect(wrapper.findAll(".candidate")).toHaveLength(2);
    expect(wrapper.text()).toContain("来自目录");
    expect(wrapper.text()).toContain("预计范围");
    expect(wrapper.text()).toContain("不确定");
    expect(wrapper.findAll("button")).toHaveLength(0);
  });

  it("emits selection without a second create action", async () => {
    const wrapper = mount(CandidateList, {
      props: { candidates, selectedId: "cand_1", busy: false },
    });
    await wrapper.findAll('input[type="radio"]')[1]?.trigger("change");
    expect(wrapper.emitted("select")?.[0]).toEqual(["cand_2"]);
  });

  it("disables radios while busy", () => {
    const wrapper = mount(CandidateList, {
      props: { candidates, selectedId: null, busy: true },
    });
    expect(wrapper.get('input[type="radio"]').attributes("disabled")).toBeDefined();
  });
});
