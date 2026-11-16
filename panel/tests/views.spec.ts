import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import HistoryView from "@/views/HistoryView.vue";
import ProjectView from "@/views/ProjectView.vue";
import RunView from "@/views/RunView.vue";
import SettingsView from "@/views/SettingsView.vue";
import { session } from "@/state/session";

import {
  DOCTOR,
  PLAN,
  PROJECT,
  RUN_DETAIL,
  TASK,
  installHost,
  resetSession,
} from "./fixtures";

describe("项目页", () => {
  it("没有项目时只给选择目录与说明", async () => {
    installHost({});
    resetSession();
    const wrapper = mount(ProjectView);

    expect(wrapper.text()).toContain("先选择一个项目文件夹");
    expect(wrapper.find("input#project-root").exists()).toBe(false);
    const labels = wrapper.findAll("button").map((button) => button.text());
    expect(labels).toContain("选择项目文件夹");
    expect(labels).toContain("打开设置");

    await wrapper.find("button").trigger("click");
    expect(wrapper.find("input#project-root").exists()).toBe(true);
  });

  it("有项目时仍能登记另一个目录", async () => {
    installHost({});
    resetSession();
    session.project = PROJECT;
    session.projects = [{ project_id: PROJECT.project_id, name: PROJECT.name, root: PROJECT.root }];
    const wrapper = mount(ProjectView);

    expect(wrapper.find("input#project-root").exists()).toBe(false);
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("登记其他目录"))
      ?.trigger("click");
    expect(wrapper.find("input#project-root").exists()).toBe(true);
  });

  it("有项目时展示模块、入口说明与上次结论", async () => {
    installHost({});
    resetSession();
    session.project = PROJECT;
    session.projects = [{ project_id: PROJECT.project_id, name: PROJECT.name, root: PROJECT.root }];
    session.lastRun = {
      status: "completed",
      outcome: "incomplete",
      startedAt: "2026-11-16T14:20:00+08:00",
      selected: 2,
      applicable: 3,
    };
    session.history = [
      {
        run_id: "run_1",
        status: "completed",
        started_at: "2026-11-16T14:20:00+08:00",
      },
    ];

    const wrapper = mount(ProjectView);
    const text = wrapper.text();
    expect(text).toContain("订单服务");
    expect(text).toContain("订单");
    expect(text).toContain("库存");
    expect(text).toContain("运行前检查");
    expect(text).toContain("未完成");
    expect(text).toContain("新建测试");
    expect(text).toContain("查看历史");
  });

  it("项目数据齐全时主视图也不常驻交付输入框", async () => {
    installHost({
      "task.list": {
        tasks: [{ task_id: TASK.task_id, title: TASK.title, revision: 1 }],
      },
    });
    resetSession();
    session.project = PROJECT;
    session.projects = [{ project_id: PROJECT.project_id, name: PROJECT.name, root: PROJECT.root }];
    const wrapper = mount(ProjectView);
    await flushPromises();

    expect(wrapper.find('[data-testid="project-overview"]').exists()).toBe(true);
    expect(wrapper.findAll("textarea")).toHaveLength(0);
    expect(wrapper.findAll('input[type="text"]')).toHaveLength(0);
    expect(wrapper.find("select").exists()).toBe(false);
    expect(wrapper.text()).toContain("AI 生成摘要");
    // 模块建议收在“更多”折叠区里，默认不展开。
    expect(wrapper.find('details[data-testid="module-suggest"]').attributes("open")).toBeUndefined();
  });
});

describe("历史与设置", () => {
  it("没有运行时显示空态，不伪造记录", () => {
    installHost({});
    resetSession();
    session.project = PROJECT;
    session.history = [];
    const wrapper = mount(HistoryView);
    expect(wrapper.text()).toContain("还没有运行记录");
  });

  it("设置页按自检事实分组，并说明自动修复范围", () => {
    installHost({});
    resetSession();
    session.doctor = DOCTOR;
    session.env = "ok";
    const wrapper = mount(SettingsView);
    const text = wrapper.text();
    expect(text).toContain("环境自检");
    expect(text).toContain("3.13.1");
    expect(text).toContain("C:\\data\\ws");
    expect(text).toContain("不安装软件");
    expect(text).toContain("模型密钥");
  });
});

describe("运行页", () => {
  it("展示进度、当前用例与取消二次确认", async () => {
    const runningDetail = {
      outcome: null,
      run: {
        ...RUN_DETAIL.run,
        status: "running" as const,
        cases: [
          { ...RUN_DETAIL.run.cases[0], result: "passed" as const, evidence_refs: [], attempts: [] },
          { ...RUN_DETAIL.run.cases[1], result: "not_run" as const, evidence_refs: [], attempts: [] },
        ],
      },
    };
    const harness = installHost({
      "run.status": runningDetail,
      "plan.show": { plan: PLAN },
      "task.show": { task: TASK },
      "run.cancel": { cancel_requested: true },
    });
    resetSession();
    session.project = PROJECT;
    session.activeRunId = "run_1";
    session.plan = PLAN;
    session.task = TASK;

    const wrapper = mount(RunView);
    await flushPromises();

    expect(wrapper.text()).toContain("运行中");
    expect(wrapper.text()).toContain("每 2 秒更新");
    expect(wrapper.text()).toContain("1 / 2");
    expect(wrapper.text()).toContain("取消这次运行");

    await wrapper.find(".cancel-zone button").trigger("click");
    expect(wrapper.text()).toContain("确认取消？");

    const confirm = wrapper
      .findAll(".cancel-zone button")
      .find((button) => button.text() === "确认取消");
    await confirm?.trigger("click");
    await flushPromises();

    expect(harness.calls.some((call) => call.command === "run.cancel")).toBe(true);
    wrapper.unmount();
  });
});
