import { flushPromises } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import { applyHostContext, bootstrap, session } from "@/state/session";

import {
  DOCTOR,
  PROJECT,
  installHost,
  resetSession,
  sessionRoutes,
} from "./fixtures";

const OTHER_PROJECT = {
  ...PROJECT,
  project_id: "proj_other",
  name: "库存服务",
  root: "E:\\work\\stock-service",
};

async function settle(times = 5): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await flushPromises();
  }
}

describe("编辑器工作区上下文", () => {
  it("匹配到项目时清空预填路径", () => {
    resetSession();
    applyHostContext({
      type: "context",
      projectId: PROJECT.project_id,
      projectRoot: PROJECT.root,
      workspaceFolderCount: 1,
    });
    expect(session.hostContext?.projectId).toBe(PROJECT.project_id);
    expect(session.prefillRoot).toBe("");
  });

  it("单个工作区目录没有匹配时只预填路径", () => {
    resetSession();
    applyHostContext({
      type: "context",
      projectId: "",
      projectRoot: "C:/work/new-service",
      workspaceFolderCount: 1,
    });
    expect(session.prefillRoot).toBe("C:/work/new-service");
  });

  it("消息事件里的上下文也会进入面板状态", () => {
    resetSession();
    window.dispatchEvent(
      new MessageEvent("message", {
        data: {
          type: "context",
          projectId: "",
          projectRoot: "D:/work/another",
          workspaceFolderCount: 2,
        },
      }),
    );
    expect(session.hostContext?.workspaceFolderCount).toBe(2);
    expect(session.prefillRoot).toBe("D:/work/another");
  });

  it("启动时优先打开工作区匹配的项目，并进入建议测试单", async () => {
    installHost(
      sessionRoutes({
        doctor: DOCTOR,
        "project.list": { projects: [PROJECT, OTHER_PROJECT] },
        "project.show": (payload: Record<string, unknown>) => {
          const id = payload["project_id"];
          return { project: id === OTHER_PROJECT.project_id ? OTHER_PROJECT : PROJECT };
        },
        "run.list": { runs: [] },
      }),
    );
    resetSession();
    applyHostContext({
      type: "context",
      projectId: OTHER_PROJECT.project_id,
      projectRoot: OTHER_PROJECT.root,
      workspaceFolderCount: 1,
    });

    await bootstrap();
    await settle();

    expect(session.project?.project_id).toBe(OTHER_PROJECT.project_id);
    expect(session.phase).toBe("proposal");
    expect(session.proposal.goal).toBe("验证下单成功后订单只出现一次");
  });

  it("最近一次运行存在时直接回放结果", async () => {
    installHost(sessionRoutes());
    resetSession();
    applyHostContext({
      type: "context",
      projectId: PROJECT.project_id,
      projectRoot: PROJECT.root,
      workspaceFolderCount: 1,
    });

    await bootstrap();
    await settle();

    expect(session.phase).toBe("result");
    expect(session.result?.run.run_id).toBe("run_1");
  });
});
