import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import {
  CASE_RESULT_LABEL,
  INTENT_LABEL,
  LAYER_LABEL,
  RUN_STATUS_LABEL,
  SEVERITY_LABEL,
  VERDICT_LABEL,
} from "@/api/labels";
import TestView from "@/views/TestView.vue";
import { PLAN, PROJECT, RUN_DETAIL, TASK, installHost, resetWorkspace, workspaceRoutes } from "./fixtures";
import { workspace } from "@/state/workspace";

const BANNED = ["冒烟", "L1", "L2", "L3", "S/M", "Mock", "plan_id", "run_id"];

describe("界面用词", () => {
  it("枚举值翻译成产品语言", () => {
    const values = [
      ...Object.values(RUN_STATUS_LABEL),
      ...Object.values(VERDICT_LABEL),
      ...Object.values(CASE_RESULT_LABEL),
      ...Object.values(LAYER_LABEL),
      ...Object.values(INTENT_LABEL),
      ...Object.values(SEVERITY_LABEL),
    ].join(" ");
    for (const word of BANNED) {
      expect(values).not.toContain(word);
    }
    expect(values).toContain("项目快速检查");
    expect(values).toContain("按验收项验证");
    expect(values).toContain("项目级检查");
    expect(values).toContain("自动检查");
    expect(values).toContain("人工检查");
  });

  it("结果页不出现内部术语", () => {
    resetWorkspace();
    workspace.project = PROJECT;
    workspace.task = TASK;
    workspace.plan = PLAN;
    workspace.run = RUN_DETAIL;
    workspace.stage = "result";
    installHost(workspaceRoutes());
    const text = mount(TestView).text();
    for (const word of BANNED) {
      expect(text).not.toContain(word);
    }
    expect(text).toContain("没通过");
    expect(text).toContain("已完成 2 / 2 条检查");
  });
});
