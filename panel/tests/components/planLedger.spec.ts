import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import PlanLedger from "@/components/PlanLedger.vue";
import type { AcceptanceItem, Plan, PlanCase, Task } from "@/domain/types";

function item(id: string, no: string): AcceptanceItem {
  return {
    id,
    no,
    text: `验收项 ${no}`,
    observable: true,
    status: "active",
    source: "测试",
    moduleId: null,
    moduleName: "模块",
    featureName: "功能",
  };
}

function planCase(extraItemIds: string[]): PlanCase {
  return {
    id: "case_1",
    itemId: "item_1",
    extraItemIds,
    layer: "L2",
    title: "同时覆盖两个验收项",
    preconditions: [],
    steps: [],
    expected: "通过",
    expectationSource: "验收项 01",
    expectationVerified: true,
    verifyMethod: "pytest",
    dependencies: [],
    mockAllowed: false,
    importance: "high",
    dependsOn: null,
    scriptName: null,
    scriptContent: null,
    manual: false,
  };
}

describe("PlanLedger", () => {
  it("lists a case under every acceptance item it covers", () => {
    const plan = {
      id: "plan_1",
      projectId: "p",
      taskId: "t",
      no: 1,
      revision: 1,
      status: "published",
      taskRevision: 1,
      taskContract: "",
      createdAt: "",
      scope: {
        preset: "selected",
        moduleIds: [],
        acceptanceItemIds: ["item_1", "item_2"],
        coverage: { selected: 2, applicable: 2 },
      },
      applicableCount: 2,
      rulesVersionId: null,
      cases: [planCase(["item_2"])],
      templates: [],
      unselectedByModule: [],
      warnings: [],
      sourceSignature: "",
      confirmation: null,
      stale: false,
      staleReason: "",
    } as Plan;
    const task = {
      id: "t",
      acceptanceItems: [item("item_1", "01"), item("item_2", "02")],
    } as unknown as Task;
    const wrapper = mount(PlanLedger, { props: { plan, task } });
    const summaries = wrapper.findAll(".ledger-state").map((node) => node.text());
    expect(summaries).toEqual(["1 条检查", "1 条检查"]);
  });
});
