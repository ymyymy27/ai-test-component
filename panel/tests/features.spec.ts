import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ProjectView from "@/views/ProjectView.vue";
import ResultView from "@/views/ResultView.vue";
import WizardView from "@/views/WizardView.vue";
import { session } from "@/state/session";

import {
  PLAN,
  PROJECT,
  RUN_DETAIL,
  TASK,
  installHost,
  resetSession,
  wizardRoutes,
  type Route,
} from "./fixtures";

const DRAFT = {
  done: ["下单接口"],
  not_done: [],
  changed_modules: ["order"],
  interface_changes: [],
  run_commands: ["pytest tests/test_order.py"],
  test_data: ["测试库"],
  dependencies: [],
  mocks: [],
  self_test_evidence: ["自测截图"],
};

function prepareProject(routes: Record<string, Route>) {
  const harness = installHost({
    "task.list": { tasks: [{ task_id: TASK.task_id, title: TASK.title, revision: 1 }] },
    ...routes,
  });
  resetSession();
  session.project = PROJECT;
  session.projects = [{ project_id: PROJECT.project_id, name: PROJECT.name, root: PROJECT.root }];
  return harness;
}

describe("交付说明起草", () => {
  it("起草后填入可编辑字段，保存时按字段写回", async () => {
    const harness = prepareProject({
      "delivery.draft": { draft: DRAFT, sources: { readme: "README.md", manifest: "", commit_stat: false } },
      "delivery.save": { delivery_id: "dlv_1", revision: 1, complete: true },
    });
    const wrapper = mount(ProjectView);
    await flushPromises();

    await wrapper.find('[data-testid="delivery-draft-button"]').trigger("click");
    await flushPromises();

    // 摘要卡片只给 chips；“查看 / 编辑”打开抽屉后才出现字段。
    expect(wrapper.find("textarea").exists()).toBe(false);
    expect(wrapper.find('[data-testid="delivery-draft"]').text()).toContain("order");
    expect(wrapper.find('[data-testid="delivery-draft"]').text()).toContain("pytest tests/test_order.py");

    await wrapper.find('[data-testid="delivery-edit"]').trigger("click");
    await flushPromises();

    const done = wrapper.find('textarea[aria-label="已完成"]').element as HTMLTextAreaElement;
    expect(done.value).toBe("下单接口");
    const testData = wrapper.find('textarea[aria-label="测试数据"]').element as HTMLTextAreaElement;
    expect(testData.value).toBe("测试库");

    await wrapper.find('[data-testid="delivery-save-button"]').trigger("click");
    await flushPromises();

    const saved = harness.calls.find((call) => call.command === "delivery.save");
    expect(saved?.payload).toMatchObject({
      project_id: PROJECT.project_id,
      task_id: TASK.task_id,
      done: ["下单接口"],
      changed_modules: ["order"],
      run_commands: ["pytest tests/test_order.py"],
      test_data: "测试库",
      self_test_evidence: ["自测截图"],
    });
    expect(wrapper.find('[data-testid="delivery-saved"]').text()).toContain("已保存");
  });
});

describe("模块建议", () => {
  it("展示建议，确认后逐条调用 module.add", async () => {
    const harness = prepareProject({
      "module.suggest": {
        modules: [
          {
            name: "order",
            responsibility: "下单",
            owner: "",
            deps: [{ to: "stock", type: "calls", note: "扣减" }],
          },
          { name: "stock", responsibility: "库存", owner: "", deps: [] },
        ],
        registered: [],
      },
      "module.add": { module_id: "mod_new", revision: 3 },
      "project.show": { project: PROJECT },
      "run.list": { runs: [] },
    });
    const wrapper = mount(ProjectView);
    await flushPromises();

    await wrapper.find('[data-testid="module-suggest-button"]').trigger("click");
    await flushPromises();

    const list = wrapper.find('[data-testid="module-suggestions"]');
    expect(list.text()).toContain("order");
    expect(list.text()).toContain("stock（calls）");

    await wrapper.find('[data-testid="module-confirm-button"]').trigger("click");
    await flushPromises();

    const adds = harness.calls.filter((call) => call.command === "module.add");
    expect(adds).toHaveLength(2);
    expect(adds[0].payload).toMatchObject({
      project_id: PROJECT.project_id,
      name: "order",
      deps: [{ to: "stock", type: "calls", note: "扣减" }],
    });
    expect(adds[1].payload).toMatchObject({ name: "stock" });
  });
});

const TEMPLATE_RESULT = {
  template_id: "python-service",
  name: "Python服务",
  version: "1.0.0",
  applied_at: "2026-11-16T10:00:00+08:00",
  items: [
    {
      item_id: "startup",
      layer: "L1",
      objective: "服务能启动",
      disposition: "applicable" as const,
      reason: "有启动命令",
    },
    {
      item_id: "state",
      layer: "L2",
      objective: "状态可核对",
      disposition: "unknown" as const,
      reason: "模型未提供理由",
    },
  ],
  summary: { applicable: 1, not_applicable: 0, unsupported: 0, unknown: 1 },
};

async function reachConfirm(overrides: Record<string, Route> = {}) {
  let planShows = 0;
  const harness = installHost(
    wizardRoutes({
      "template.list": {
        templates: [
          { template_id: "python-service", name: "Python服务", version: "1.0.0", item_count: 2 },
        ],
      },
      "template.apply": {
        template: TEMPLATE_RESULT,
        path: "tasks/task_1/templates/python-service.json",
        plan_id: PLAN.plan_id,
        plan_revision: 2,
      },
      "plan.show": () => {
        planShows += 1;
        return { plan: planShows === 1 ? PLAN : { ...PLAN, templates: [TEMPLATE_RESULT] } };
      },
      ...overrides,
    }),
  );
  resetSession();
  session.project = PROJECT;
  session.projects = [{ project_id: PROJECT.project_id, name: PROJECT.name, root: PROJECT.root }];
  const wrapper = mount(WizardView, { attachTo: document.body });
  await flushPromises();
  await wrapper.find(".writer-link").trigger("click");
  await wrapper.find("#goal-input").setValue("验证下单流程");
  await wrapper.find('[data-testid="wizard-next"]').trigger("click");
  await flushPromises();
  await wrapper.find('[data-testid="wizard-next"]').trigger("click");
  await flushPromises();
  await wrapper.find('[data-testid="wizard-next"]').trigger("click");
  await flushPromises();
  return { wrapper: wrapper as VueWrapper, harness };
}

describe("模板确认页", () => {
  it("应用模板后只读展示检查项与处置", async () => {
    const { wrapper, harness } = await reachConfirm();

    expect(wrapper.find('[data-testid="template-block"]').text()).toContain("模板检查项");
    await wrapper.find('[data-testid="apply-template"]').trigger("click");
    await flushPromises();

    const apply = harness.calls.find((call) => call.command === "template.apply");
    expect(apply?.payload).toMatchObject({
      project_id: PROJECT.project_id,
      task_id: TASK.task_id,
      template_name: "python-service",
      plan_id: PLAN.plan_id,
    });

    const text = wrapper.find('[data-testid="template-block"]').text();
    expect(text).toContain("服务能启动");
    expect(text).toContain("适用");
    expect(text).toContain("状态可核对");
    expect(text).toContain("未知");
    expect(text).toContain("模型未提供理由");
    expect(text).toContain("不进入必测分母");
  });
});

describe("链路定位", () => {
  it("展示首错节点、上下游与代码位置", async () => {
    installHost({});
    resetSession();
    session.project = PROJECT;
    session.result = {
      ...RUN_DETAIL,
      link: {
        first_anomaly: { node_id: "log:12", status: "error" },
        upstream: [{ node_id: "log:1", status: "passed" }],
        downstream: [{ node_id: "log:14", status: "unknown" }],
        location: { known: true, path: "app.log", line: 12 },
      },
    };
    session.plan = PLAN;
    session.task = TASK;
    const wrapper = mount(ResultView);
    await flushPromises();

    const block = wrapper.find('[data-testid="link-block"]');
    expect(block.text()).toContain("首错节点");
    expect(block.text()).toContain("log:12");
    expect(block.text()).toContain("log:1");
    expect(block.text()).toContain("log:14");
    expect(block.text()).toContain("app.log:12");
  });

  it("位置解析不到时显示未知范围与原因", async () => {
    installHost({});
    resetSession();
    session.project = PROJECT;
    session.result = {
      ...RUN_DETAIL,
      link: {
        first_anomaly: { node_id: "db:1", status: "unknown" },
        upstream: [],
        downstream: [],
        location: { known: false, reason: "数据库引用不是文件位置" },
      },
    };
    session.plan = PLAN;
    session.task = TASK;
    const wrapper = mount(ResultView);
    await flushPromises();

    const block = wrapper.find('[data-testid="link-block"]');
    expect(block.text()).toContain("未知范围");
    expect(block.text()).toContain("数据库引用不是文件位置");
  });
});
