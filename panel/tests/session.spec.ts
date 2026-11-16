import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import SessionView from "@/views/SessionView.vue";
import { loadProposal, refreshRun, session } from "@/state/session";

import {
  ITEMS,
  PLAN,
  PLAN_CASES,
  PROJECT,
  RUN_DETAIL,
  RUNNING_DETAIL,
  RUN_SUMMARIES,
  TASK,
  installHost,
  resetSession,
  sessionRoutes,
  type Route,
} from "./fixtures";

async function settle(times = 4): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await flushPromises();
  }
}

async function mountProposal(
  overrides: Record<string, Route> = {},
): Promise<{ wrapper: VueWrapper; harness: ReturnType<typeof installHost> }> {
  const harness = installHost(sessionRoutes(overrides));
  resetSession();
  session.project = PROJECT;
  session.projects = [{ project_id: PROJECT.project_id, name: PROJECT.name, root: PROJECT.root }];
  await loadProposal();
  const wrapper = mount(SessionView, { attachTo: document.body });
  await settle();
  return { wrapper, harness };
}

function seedResult(): void {
  resetSession();
  session.project = PROJECT;
  session.projects = [{ project_id: PROJECT.project_id, name: PROJECT.name, root: PROJECT.root }];
  session.phase = "result";
  session.result = RUN_DETAIL;
  session.plan = PLAN;
  session.task = TASK;
  session.activeRunId = RUN_DETAIL.run.run_id;
  session.activePlanId = PLAN.plan_id;
  session.activeTaskId = TASK.task_id;
  session.history = RUN_SUMMARIES;
}

describe("单页测试单：五个状态", () => {
  it("无项目时只有一个选择动作和一句说明", async () => {
    installHost(sessionRoutes());
    resetSession();
    session.phase = "no-project";
    const wrapper = mount(SessionView, { attachTo: document.body });
    await settle();

    expect(wrapper.find('[data-testid="no-project"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("先选择一个项目文件夹");
    expect(wrapper.findAll("button")).toHaveLength(1);
    expect(wrapper.find("input").exists()).toBe(false);

    await wrapper.find('[data-testid="select-project"]').trigger("click");
    expect(wrapper.find("input#project-root").exists()).toBe(true);
    wrapper.unmount();
  });

  it("选项目后直接进入建议测试单，不启动运行", async () => {
    const harness = installHost(
      sessionRoutes({ "run.list": { runs: [] } }),
    );
    resetSession();
    session.phase = "no-project";
    const wrapper = mount(SessionView, { attachTo: document.body });
    await settle();

    await wrapper.find('[data-testid="select-project"]').trigger("click");
    await wrapper.find("input#project-root").setValue(PROJECT.root);
    await wrapper.find("form.picker").trigger("submit");
    await settle(6);

    expect(session.phase).toBe("proposal");
    expect(wrapper.find('[data-testid="proposal-panel"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("验证下单成功后订单只出现一次");
    expect(wrapper.find('[data-testid="start-test"]').exists()).toBe(true);

    const add = harness.calls.find((call) => call.command === "task.add");
    expect(add?.payload).toMatchObject({
      project_id: PROJECT.project_id,
      requirement: "验证下单成功后订单只出现一次",
    });
    expect(harness.calls.some((call) => call.command === "run.start")).toBe(false);
    wrapper.unmount();
  });

  it("建议测试单默认全选，标题取第一条候选且没有常驻文本框", async () => {
    const { wrapper, harness } = await mountProposal();

    expect(wrapper.find(".order-head h1").text()).toBe("验证下单成功后订单只出现一次");
    expect(wrapper.text()).toContain("依据：order 模块近三次提交");
    expect(wrapper.text()).toContain("将验证 2 条验收标准");
    expect(wrapper.findAll('[data-testid="proposal-criteria"] li')).toHaveLength(3);
    expect(wrapper.findAll('[role="switch"]')).toHaveLength(2);
    for (const toggle of wrapper.findAll('[role="switch"]')) {
      expect(toggle.attributes("aria-checked")).toBe("true");
    }
    expect(
      wrapper.find('[data-testid="proposal-scope-value"]').text().replace(/\s+/g, ""),
    ).toContain("2/2条适用");
    expect(wrapper.findAll('input[type="text"]')).toHaveLength(0);
    expect(wrapper.findAll("textarea")).toHaveLength(0);

    const add = harness.calls.filter((call) => call.command === "task.add");
    expect(add).toHaveLength(1);
    const listIndex = harness.calls.findIndex((call) => call.command === "task.list");
    const addIndex = harness.calls.findIndex((call) => call.command === "task.add");
    expect(listIndex).toBeGreaterThanOrEqual(0);
    expect(listIndex).toBeLessThan(addIndex);
    wrapper.unmount();
  });

  it("开始测试一次点击完成生成计划与确认执行，原地进入运行", async () => {
    const { wrapper, harness } = await mountProposal({
      "run.status": RUNNING_DETAIL,
    });

    await wrapper.find('[data-testid="start-test"]').trigger("click");
    await settle(6);

    const generate = harness.calls.find((call) => call.command === "plan.generate");
    expect(generate?.payload["scope"]).toMatchObject({
      preset: "all_applicable",
      acceptance_item_ids: [],
    });
    const start = harness.calls.find((call) => call.command === "run.start");
    expect(start?.options.confirm).toBe(true);
    expect(start?.payload).toMatchObject({ plan_id: "plan_1" });
    expect(session.phase).toBe("running");
    expect(wrapper.find('[data-testid="run-panel"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="test-order"]').attributes("data-phase")).toBe("running");
    wrapper.unmount();
  });

  it("运行中显示进度、当前用例与取消，结束后同一张单子变结果", async () => {
    let statusCalls = 0;
    const { wrapper } = await mountProposal({
      "run.status": () => {
        statusCalls += 1;
        return statusCalls === 1 ? RUNNING_DETAIL : RUN_DETAIL;
      },
    });

    await wrapper.find('[data-testid="start-test"]').trigger("click");
    await settle(6);

    expect(wrapper.text()).toContain("运行中");
    expect(wrapper.text()).toContain("每 2 秒更新");
    expect(wrapper.text()).toContain("1 / 2");
    expect(wrapper.text()).toContain("并发下单核对库存");
    expect(wrapper.text()).toContain("取消这次运行");

    await wrapper.find(".cancel-zone button").trigger("click");
    expect(wrapper.text()).toContain("确认取消？");
    await wrapper.findAll(".cancel-zone button").find((button) => button.text() === "继续运行")?.trigger("click");

    await refreshRun();
    await settle();

    expect(session.phase).toBe("result");
    expect(wrapper.find('[data-testid="run-panel"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="result-panel"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="verdict-word"]').text()).toBe("失败");
    expect(wrapper.find('[data-testid="test-order"]').attributes("data-phase")).toBe("result");
    wrapper.unmount();
  });
});

describe("结果相位的操作", () => {
  it("再测一次复用最近计划，不重新生成计划", async () => {
    seedResult();
    const harness = installHost(
      sessionRoutes({ "run.status": RUNNING_DETAIL }),
    );
    const wrapper = mount(SessionView, { attachTo: document.body });
    await settle();

    await wrapper.find('[data-testid="rerun-button"]').trigger("click");
    await settle(6);

    const shown = harness.calls.find((call) => call.command === "plan.show");
    expect(shown?.payload).toMatchObject({ plan_id: PLAN.plan_id });
    const start = harness.calls.find((call) => call.command === "run.start");
    expect(start?.options.confirm).toBe(true);
    expect(start?.payload).toMatchObject({ plan_id: PLAN.plan_id });
    expect(harness.calls.some((call) => call.command === "plan.generate")).toBe(false);
    expect(session.phase).toBe("running");
    wrapper.unmount();
  });

  it("计划不在了才重新生成，再开始新运行", async () => {
    seedResult();
    const harness = installHost(
      sessionRoutes({
        "plan.show": {
          __error: { code: "RUN_NOT_FOUND", message: "计划已不存在", next_step: "重新生成" },
        },
        "run.status": RUNNING_DETAIL,
      }),
    );
    const wrapper = mount(SessionView, { attachTo: document.body });
    await settle();

    await wrapper.find('[data-testid="rerun-button"]').trigger("click");
    await settle(6);

    const generate = harness.calls.find((call) => call.command === "plan.generate");
    expect(generate?.payload).toMatchObject({ task_id: TASK.task_id });
    const start = harness.calls.find((call) => call.command === "run.start");
    expect(start?.options.confirm).toBe(true);
    expect(session.phase).toBe("running");
    wrapper.unmount();
  });

  it("计划过期时自动重新生成并用新计划重试一次", async () => {
    seedResult();
    let starts = 0;
    const harness = installHost(
      sessionRoutes({
        "plan.generate": {
          plan_id: "plan_new",
          revision: 1,
          status: "published",
          scope: PLAN.scope,
          coverage: { selected: 2, applicable: 3 },
          cases: PLAN_CASES.length,
          source_signature: "sha256:new",
        },
        "run.start": () => {
          starts += 1;
          if (starts === 1) {
            return {
              __error: {
                code: "PLAN_STALE",
                message: "计划生成后项目已变化",
                next_step: "重新生成计划后重试",
              },
            };
          }
          return { run_id: "run_1", status: "running", scope: { selected: 2, applicable: 3 } };
        },
        "run.status": RUNNING_DETAIL,
      }),
    );
    const wrapper = mount(SessionView, { attachTo: document.body });
    await settle();

    await wrapper.find('[data-testid="rerun-button"]').trigger("click");
    await settle(8);

    const generate = harness.calls.filter((call) => call.command === "plan.generate");
    expect(generate).toHaveLength(1);
    expect(generate[0].payload).toMatchObject({ task_id: TASK.task_id });
    const startsCalls = harness.calls.filter((call) => call.command === "run.start");
    expect(startsCalls).toHaveLength(2);
    expect(startsCalls[0].payload["plan_id"]).toBe(PLAN.plan_id);
    expect(startsCalls[1].payload["plan_id"]).toBe("plan_new");
    expect(session.notice).toContain("源码或配置已变化，已重新生成计划");
    expect(session.phase).toBe("running");
    wrapper.unmount();
  });

  it("重新生成的计划仍过期时只重试一次并显示真实错误", async () => {
    seedResult();
    let starts = 0;
    const harness = installHost(
      sessionRoutes({
        "plan.generate": {
          plan_id: "plan_new",
          revision: 1,
          status: "published",
          scope: PLAN.scope,
          coverage: { selected: 2, applicable: 3 },
          cases: PLAN_CASES.length,
        },
        "run.start": () => {
          starts += 1;
          return {
            __error: {
              code: "PLAN_STALE",
              message: "计划生成后项目、交付提交、规则或服务配置已变化",
              next_step: "重新生成计划后重试",
            },
          };
        },
      }),
    );
    const wrapper = mount(SessionView, { attachTo: document.body });
    await settle();

    await wrapper.find('[data-testid="rerun-button"]').trigger("click");
    await settle(8);

    expect(starts).toBe(2);
    expect(
      harness.calls.filter((call) => call.command === "plan.generate"),
    ).toHaveLength(1);
    expect(
      harness.calls.filter((call) => call.command === "run.start"),
    ).toHaveLength(2);
    expect(session.phase).toBe("result");
    expect(session.notice).toBe("");
    expect(wrapper.text()).toContain("计划生成后项目、交付提交、规则或服务配置已变化");
    wrapper.unmount();
  });

  it("同 title+goal 的已有任务直接复用，不重复创建", async () => {
    const { wrapper, harness } = await mountProposal({
      "task.list": {
        tasks: [
          {
            task_id: "task_existing",
            title: "验证下单成功后订单只出现一次",
            revision: 3,
          },
        ],
      },
      "task.show": (payload: Record<string, unknown>) => ({
        task: { ...TASK, task_id: String(payload["task_id"]), revision: 3 },
      }),
    });

    expect(harness.calls.some((call) => call.command === "task.add")).toBe(false);
    const shown = harness.calls.find((call) => call.command === "task.show");
    expect(shown?.payload["task_id"]).toBe("task_existing");
    expect(session.proposal.taskId).toBe("task_existing");
    expect(session.proposal.taskRevision).toBe(3);
    expect(wrapper.find('[data-testid="proposal-panel"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("新目标也优先复用同 title+goal 的任务", async () => {
    seedResult();
    const harness = installHost(
      sessionRoutes({
        "task.list": {
          tasks: [
            {
              task_id: "task_existing",
              title: "验证下单成功后订单只出现一次",
              revision: 2,
            },
          ],
        },
        "task.show": (payload: Record<string, unknown>) => ({
          task: { ...TASK, task_id: String(payload["task_id"]), revision: 2 },
        }),
      }),
    );
    const wrapper = mount(SessionView, { attachTo: document.body });
    await settle();

    await wrapper.find('[data-testid="new-target"]').trigger("click");
    await settle(6);

    expect(harness.calls.some((call) => call.command === "task.add")).toBe(false);
    expect(
      harness.calls.some(
        (call) =>
          call.command === "task.show" && call.payload["task_id"] === "task_existing",
      ),
    ).toBe(true);
    expect(session.proposal.taskId).toBe("task_existing");
    expect(session.phase).toBe("proposal");
    wrapper.unmount();
  });

  it("历史抽屉点一项就在当前视图回放该次结果", async () => {
    seedResult();
    const harness = installHost(
      sessionRoutes({
        "run.status": (payload: Record<string, unknown>) =>
          payload["run_id"] === "run_prev"
            ? {
                outcome: "incomplete",
                run: { ...RUN_DETAIL.run, run_id: "run_prev", status: "cancelled" },
              }
            : RUN_DETAIL,
      }),
    );
    const wrapper = mount(SessionView, { attachTo: document.body });
    await settle();

    await wrapper.findAll(".nav-btn").find((button) => button.text().includes("历史"))?.trigger("click");
    await settle();

    const list = wrapper.find('[data-testid="history-list"]');
    expect(list.exists()).toBe(true);
    expect(list.findAll(".run")).toHaveLength(3);

    await list.findAll(".run")[1].trigger("click");
    await settle(6);

    const shown = harness.calls.filter(
      (call) => call.command === "run.status" && call.payload["run_id"] === "run_prev",
    );
    expect(shown).toHaveLength(1);
    expect(session.activeRunId).toBe("run_prev");
    expect(session.phase).toBe("result");
    expect(session.historyOpen).toBe(false);
    expect(wrapper.find('[data-testid="result-panel"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="verdict-word"]').text()).toBe("未完成");
    wrapper.unmount();
  });

  it("新目标回到建议测试单并重新提取", async () => {
    seedResult();
    const harness = installHost(sessionRoutes());
    const wrapper = mount(SessionView, { attachTo: document.body });
    await settle();

    await wrapper.find('[data-testid="new-target"]').trigger("click");
    await settle(6);

    expect(harness.calls.some((call) => call.command === "task.suggest")).toBe(true);
    expect(harness.calls.filter((call) => call.command === "task.add")).toHaveLength(1);
    expect(harness.calls.some((call) => call.command === "run.start")).toBe(false);
    expect(session.phase).toBe("proposal");
    expect(wrapper.find('[data-testid="proposal-panel"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("换一个目标会重新提取验收标准但不启动", async () => {
    const { wrapper, harness } = await mountProposal();

    await wrapper.find('[data-testid="switch-target"]').trigger("click");
    await settle();
    const menu = wrapper.find('[role="dialog"][aria-label="换一个测试目标"]');
    expect(menu.exists()).toBe(true);

    await menu.findAll(".option")[1].trigger("click");
    await settle(6);

    const adds = harness.calls.filter((call) => call.command === "task.add");
    expect(adds).toHaveLength(2);
    expect(adds[1].payload["requirement"]).toBe("验证并发下单库存只扣一次");
    expect(session.proposal.index).toBe(1);
    expect(harness.calls.some((call) => call.command === "run.start")).toBe(false);
    wrapper.unmount();
  });
});

describe("调整抽屉", () => {
  it("编辑验收项后保存写回任务", async () => {
    let taskShows = 0;
    const editedTask = {
      ...TASK,
      revision: 2,
      acceptance_items: [
        { ...ITEMS[0], text: "下单后订单列表只出现一次" },
        ITEMS[1],
        ITEMS[2],
      ],
    };
    const { wrapper, harness } = await mountProposal({
      "task.show": () => {
        taskShows += 1;
        return { task: taskShows === 1 ? TASK : editedTask };
      },
    });

    await wrapper.find('[data-testid="open-adjust"]').trigger("click");
    await settle();
    expect(wrapper.find('[data-testid="adjust-drawer"]').exists()).toBe(true);

    await wrapper.findAll('[data-testid="adjust-drawer"] .item-row .link')[0].trigger("click");
    await wrapper
      .find('input[aria-label="编辑验收标准"]')
      .setValue("下单后订单列表只出现一次");
    await wrapper
      .findAll('[data-testid="adjust-drawer"] .item-row button')
      .find((button) => button.text() === "完成")
      ?.trigger("click");

    await wrapper.find('[data-testid="adjust-save"]').trigger("click");
    await settle(6);

    const update = harness.calls.find((call) => call.command === "task.update");
    expect(update?.payload).toMatchObject({
      project_id: PROJECT.project_id,
      task_id: TASK.task_id,
      expected_revision: 1,
    });
    const patch = update?.payload["patch"] as {
      acceptance_items: { text: string; observable: boolean }[];
    };
    expect(patch.acceptance_items[0]).toMatchObject({
      text: "下单后订单列表只出现一次",
      observable: true,
    });
    expect(patch.acceptance_items).toHaveLength(3);
    expect(session.proposal.items[0].text).toBe("下单后订单列表只出现一次");
    expect(session.proposal.itemsDirty).toBe(false);
    expect(session.adjustOpen).toBe(false);
    wrapper.unmount();
  });

  it("关闭调整抽屉不会保留未保存的验收项改动", async () => {
    const { wrapper } = await mountProposal();
    const before = session.proposal.items[0].text;

    await wrapper.find('[data-testid="open-adjust"]').trigger("click");
    await settle();
    await wrapper.findAll('[data-testid="adjust-drawer"] .item-row .link')[0].trigger("click");
    await wrapper
      .find('input[aria-label="编辑验收标准"]')
      .setValue("临时改动的验收标准");
    await wrapper
      .findAll('[data-testid="adjust-drawer"] .item-row button')
      .find((button) => button.text() === "完成")
      ?.trigger("click");
    expect(session.proposal.items[0].text).toBe("临时改动的验收标准");

    await wrapper
      .findAll(".drawer-foot button")
      .find((button) => button.text() === "取消")
      ?.trigger("click");
    await settle();

    expect(session.adjustOpen).toBe(false);
    expect(session.proposal.items[0].text).toBe(before);
    wrapper.unmount();
  });

  it("抽屉里可以起草并保存交付说明", async () => {
    const { wrapper, harness } = await mountProposal();

    await wrapper.find('[data-testid="open-adjust"]').trigger("click");
    await settle();

    await wrapper.find('[data-testid="delivery-draft-button"]').trigger("click");
    await settle();

    await wrapper.find("details.delivery-fields summary").trigger("click");
    const done = wrapper.find('textarea[aria-label="已完成"]').element as HTMLTextAreaElement;
    expect(done.value).toBe("下单接口");

    await wrapper.find('[data-testid="delivery-save-button"]').trigger("click");
    await settle();

    const save = harness.calls.find((call) => call.command === "delivery.save");
    expect(save?.payload).toMatchObject({
      project_id: PROJECT.project_id,
      task_id: TASK.task_id,
      done: ["下单接口"],
      run_commands: ["pytest tests/test_order.py"],
      test_data: "测试库",
    });
    wrapper.unmount();
  });
});
