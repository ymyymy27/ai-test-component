import { flushPromises } from "@vue/test-utils";
import { beforeEach, describe, expect, it } from "vitest";

import { bootstrap, applyHostContext, workspace } from "@/state/workspace";

import {
  DOCTOR,
  PLAN,
  PROJECT,
  RUN_DETAIL,
  TASK,
  installHost,
  resetWorkspace,
  workspaceRoutes,
} from "./fixtures";

async function settle(times = 6): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await flushPromises();
  }
}

describe("编辑器工作区上下文", () => {
  beforeEach(() => {
    resetWorkspace();
  });

  it("匹配到项目时清空预填路径", () => {
    installHost(workspaceRoutes());
    applyHostContext({
      type: "context",
      projectId: PROJECT.project_id,
      projectRoot: PROJECT.root,
      workspaceFolderCount: 1,
    });
    expect(workspace.hostContext?.projectId).toBe(PROJECT.project_id);
    expect(workspace.prefillRoot).toBe("");
  });

  it("单个工作区目录没有匹配时只预填路径", () => {
    installHost(workspaceRoutes());
    applyHostContext({
      type: "context",
      projectId: "",
      projectRoot: "C:/work/new-service",
      workspaceFolderCount: 1,
    });
    expect(workspace.prefillRoot).toBe("C:/work/new-service");
  });

  it("启动时优先使用编辑器当前项目并恢复运行", async () => {
    const harness = installHost(workspaceRoutes());
    applyHostContext({ type: "context", projectId: PROJECT.project_id, projectRoot: PROJECT.root });
    await bootstrap();
    await settle();
    expect(harness.calls.some((call) => call.command === "project.show")).toBe(true);
    expect(workspace.project?.project_id).toBe(PROJECT.project_id);
    expect(workspace.stage).toBe("result");
    expect(workspace.run?.run.run_id).toBe(RUN_DETAIL.run.run_id);
  });

  it("没有任务时进入目标候选，不静默建任务", async () => {
    const harness = installHost(
      workspaceRoutes({
        "task.list": { tasks: [] },
        "task.show": { task: TASK, plans: [] },
      }),
    );
    applyHostContext({ type: "context", projectId: PROJECT.project_id, projectRoot: PROJECT.root });
    await bootstrap();
    await settle();
    expect(workspace.stage).toBe("no-task");
    expect(workspace.goal).toContain("验证下单");
    expect(harness.calls.some((call) => call.command === "task.add")).toBe(false);
  });

  it("恢复上次运行时不把当前任务文字混进历史快照", async () => {
    const harness = installHost(workspaceRoutes());
    harness.bridge.setState?.({ projectId: PROJECT.project_id, runId: RUN_DETAIL.run.run_id, planId: PLAN.plan_id });
    applyHostContext({ type: "context", projectId: PROJECT.project_id, projectRoot: PROJECT.root });
    await bootstrap();
    await settle();
    expect(workspace.stage).toBe("result");
    expect(workspace.run?.task?.title).toBe(TASK.title);
    expect(workspace.history.length).toBeGreaterThan(0);
  });

  it("环境自检失败不阻塞项目加载", async () => {
    installHost(
      workspaceRoutes({
        doctor: { __error: { code: "HOST_CALL_FAILED", message: "自检失败", next_step: "稍后重试" } },
      }),
    );
    applyHostContext({ type: "context", projectId: PROJECT.project_id, projectRoot: PROJECT.root });
    await bootstrap();
    await settle();
    expect(workspace.project?.project_id).toBe(PROJECT.project_id);
    expect(workspace.doctor).toBeNull();
  });

  it("保留自检事实供设置页展示", async () => {
    installHost(workspaceRoutes({ doctor: DOCTOR }));
    applyHostContext({ type: "context", projectId: PROJECT.project_id, projectRoot: PROJECT.root });
    await bootstrap();
    await settle();
    expect(workspace.doctor?.python.version).toBe("3.13.1");
  });
});
