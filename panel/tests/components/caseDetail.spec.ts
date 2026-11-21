import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CaseDetail from "@/components/CaseDetail.vue";
import type { EvidenceRef, RunCase } from "@/domain/types";

const evidence: EvidenceRef = {
  id: "ev_1",
  name: "run.log",
  mediaType: "LOG",
  bytes: 12,
  sha256: "a".repeat(64),
  note: "",
  missing: false,
  truncated: false,
  addedAt: "2026-11-21T10:00:00+08:00",
  sourcePath: "D:/workspace/runs/run_1/evidence/run.log",
};

const testCase = {
  id: "case_1",
  caseId: "case_1",
  itemId: "ai_1",
  title: "人工检查页面",
  method: "",
  layer: "L3",
  expected: "显示成功",
  expectationVerified: true,
  dependsOn: null,
  actual: "已显示",
  result: "passed",
  evidence: [evidence],
  message: "",
  errorType: "",
  stackKey: "",
  attempts: 1,
  manualSteps: [],
} as RunCase;

describe("CaseDetail", () => {
  it("lets a recorded manual result be modified", () => {
    const wrapper = mount(CaseDetail, {
      props: { testCase, result: "passed", allowManual: true },
    });
    expect(wrapper.text()).toContain("修改人工结果");
  });

  it("emits the evidence reference instead of opening the add-evidence flow", async () => {
    const wrapper = mount(CaseDetail, {
      props: { testCase, result: "passed", allowManual: false },
    });
    await wrapper.find(".evidence-chip").trigger("click");
    expect(wrapper.emitted("showEvidence")?.[0]?.[0]).toMatchObject({
      id: "ev_1",
      sourcePath: evidence.sourcePath,
    });
  });
});
