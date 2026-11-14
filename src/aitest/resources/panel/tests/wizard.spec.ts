import { test, expect } from "@playwright/test";
import { connect, mountPanel, reply, replyDoctor, sentMessages, waitForSend } from "./support";

const session = {
  project_id: "guided-0123456789abcdef0123456789abcdef",
  root: "C:/data/guided",
  source: "C:/samples/orders",
  entry: "main.py",
  name: "订单计算",
  endpoint: "https://api.deepseek.com",
  model: "deepseek-flash",
  timeout: 60,
  reference: "cred-1",
};

const projectCommands = [
  { key: "project", action: "save_context", intent_id: "intent-project", expected_revision: 0, binding_revision: 1, parameters: { project: {} } },
  { key: "binding", action: "save_binding", intent_id: "intent-binding", expected_revision: 0, binding_revision: 1, target: "binding-guided", parameters: { binding: {} } },
  { key: "environment", action: "save_environment", intent_id: "intent-env", expected_revision: 0, binding_revision: 1, parameters: { environment: {} } },
  { key: "snapshot", action: "analyze_project", intent_id: "intent-snapshot", expected_revision: 0, binding_revision: 1, parameters: { purpose: "prepare" } },
];

const modelCommands = [
  { key: "policy", action: "save_model_outbound_policy", intent_id: "intent-policy", expected_revision: 0, binding_revision: 1, target: "policy-1", parameters: { policy: {} } },
  { key: "suggestions", action: "generate_draft", intent_id: "intent-draft", expected_revision: 0, binding_revision: 1, parameters: { generation_mode: "model" } },
];

const commitCommands = [
  { key: "case-case-1", action: "save_case", intent_id: "intent-case-1", expected_revision: 0, binding_revision: 1, parameters: { case: {} } },
  { key: "scope", action: "save_acceptance", intent_id: "intent-scope", expected_revision: 0, binding_revision: 1, parameters: { acceptance_scope: {} } },
  { key: "publish", action: "publish_plan", intent_id: "intent-publish", expected_revision: 0, binding_revision: 1, target: "plan-guided", parameters: { plan_id: "plan-guided" } },
];

const prepareCommands = [
  { key: "prepare", action: "prepare_run", intent_id: "intent-prepare", expected_revision: 0, binding_revision: 1, parameters: { mode: "from_saved" } },
];

const registerCommands = [
  { key: "register", action: "register_run", intent_id: "intent-register", expected_revision: 0, binding_revision: 1, target: "prepared-1", parameters: { prepared_run_id: "prepared-1", record_revision: 1 } },
];

async function sendCommands(
  page: import("@playwright/test").Page,
  stage: string,
  commands: unknown[],
  assertForm?: (form: Record<string, unknown>) => void,
): Promise<void> {
  const message = await waitForSend(page, { kind: "wizardCommands", stage });
  if (assertForm) assertForm((message["form"] ?? {}) as Record<string, unknown>);
  await reply(page, { kind: "wizardResult", commands }, { kind: "wizardCommands", stage });
}

async function answer(
  page: import("@playwright/test").Page,
  action: string,
  result: Record<string, unknown>,
  expectedKind?: "writeDispatch" | "controlledDispatch",
): Promise<Record<string, unknown>> {
  const message = await waitForSend(page, { action });
  if (expectedKind) expect(message["kind"]).toBe(expectedKind);
  await reply(page, { kind: "outcome", action, status: "ok", response: { result } }, { action });
  return message;
}

test("first-run wizard prepares, publishes and registers through the controlled channels", async ({ page }) => {
  await mountPanel(page);
  await connect(page);
  await page.getByRole("button", { name: "继续准备测试", exact: true }).click();
  await expect(page.getByText("选择一个本地项目与测试入口")).toBeVisible();

  await page.getByRole("button", { name: "选择项目目录", exact: true }).click();
  await reply(page, { kind: "wizardResult", session }, { kind: "wizardChoose" });
  await expect(page.getByText("准备这次测试")).toBeVisible();

  await page.getByLabel("测试数据如何隔离、测试后如何复位").fill("只在内存中计算，不连接业务系统");
  await page.getByRole("button", { name: "确认这是本地测试入口", exact: true }).click();
  const beforePrepare = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "准备环境并分析项目", exact: true }).click();
  await reply(page, { kind: "wizardResult", setup: { workspace: "C:/data/guided/workspace" }, session }, { kind: "wizardSetup" });

  await waitForSend(page, { kind: "restartCore" }, 5000, beforePrepare);
  const beforeRestartReply = (await sentMessages(page)).length;
  await reply(page, {
    kind: "outcome", action: "restart-core", status: "ok",
    response: { status: "READY", workspace_id: "workspace-1", instance_id: "core-2" },
  }, { kind: "restartCore" });
  await waitForSend(page, { action: "doctor" }, 5000, beforeRestartReply);
  await replyDoctor(page, {
    workspace_id: "workspace-1",
    supported_actions: ["doctor", "query", "read_view", "events", "storage_usage", "prepare_execution", "authorize_step", "execute_step", "verify_pending", "create_report", "export_report", "start_run"],
  });

  await sendCommands(page, "project", projectCommands);
  const saveContext = await answer(page, "save_context", { record_id: "project-1", revision: 1 }, "writeDispatch");
  expect(saveContext["contextId"]).toBe("ctx-1");
  await answer(page, "save_binding", { record_revision: 1 }, "controlledDispatch");
  await answer(page, "save_environment", { record_revision: 1 }, "writeDispatch");
  await answer(page, "analyze_project", {
    snapshot_id: "snap-1",
    record_revision: 1,
    purpose: "prepare",
    content_identity: "sha256:" + "a".repeat(64),
    // analyze_project 的真实结果在顶层带 goal_candidates；understanding.body
    // 里是同一份理解。面板优先读顶层，不能再把有候选的项目当成无目标。
    goal_candidates: [{
      intent_id: "intent-1",
      statement: "验证订单金额与持久化",
      why: "最近改动且失败影响最大",
      risk: "金额错误",
      function_ids: ["order.total"],
    }],
    understanding: {
      understanding_id: "understanding-1",
      revision: 1,
      body: {
        goal_candidates: [{
          intent_id: "intent-1",
          statement: "验证订单金额与持久化",
          why: "最近改动且失败影响最大",
          risk: "金额错误",
          function_ids: ["order.total"],
        }],
      },
    },
  }, "writeDispatch");

  await expect(page.getByText("先选一个业务目标")).toBeVisible();
  await page.getByRole("button", { name: /验证订单金额与持久化/ }).click();
  await page.getByRole("button", { name: "用选中的目标生成计划", exact: true }).click();
  await expect(page.getByText("生成一份可核对的计划提案")).toBeVisible();
  await sendCommands(page, "model", modelCommands);
  await answer(page, "save_model_outbound_policy", {}, "controlledDispatch");
  await answer(page, "generate_draft", {
    proposal: { schema_version: "aitest.plan-proposal/1.1", cases: [{ case_id: "case-1" }] },
    draft_text: "建议覆盖正常、边界和异常输入。",
  }, "writeDispatch");

  await reply(page, {
    kind: "wizardResult",
    valid: true,
    preview: {
      cases: [{
        case_id: "case-1",
        objective: "正常订单金额",
        layer: "L2",
        importance: "required",
        verification_method: "pure-function-stdout",
        assertion_basis: { state: "present_unconfirmed" },
      }],
      coverage: { template_items: 2, covered_items: 1, required_items: 1 },
      coverage_preview: [{ function_id: "order.total", overall_status: "covered", dimensions: { normal: "covered" } }],
      coverage_contract: { contract_id: "coverage-1" },
      understanding_ref: { understanding_id: "understanding-1", revision: 1 },
    },
  }, { kind: "wizardReview" });

  await expect(page.getByText("核对计划、覆盖与依据")).toBeVisible();
  await page.screenshot({ path: "test-results/wizard-review.png", fullPage: true });
  await page.getByText("验证预期（可选，JSON）").click();
  await page.getByLabel("验证预期 JSON").fill('{"case-1":{"sum":5}}');
  await page.getByRole("button", { name: "重新校验", exact: true }).click();
  const reviewed = await waitForSend(page, { kind: "wizardReview" });
  const reviewForm = (reviewed["form"] ?? {}) as Record<string, unknown>;
  const reviewProposal = (reviewForm["proposal"] ?? {}) as Record<string, unknown>;
  const reviewCases = (reviewProposal["cases"] ?? []) as Array<Record<string, unknown>>;
  expect(JSON.parse(String(reviewCases[0]["assertion_basis_text"]))).toEqual({
    schema_version: "aitest.pure-function-basis/1",
    expected_facts: { sum: 5 },
  });
  await reply(page, {
    kind: "wizardResult",
    valid: true,
    preview: {
      cases: [],
      coverage: { template_items: 2, covered_items: 1, required_items: 1 },
      coverage_preview: [],
      coverage_contract: { contract_id: "coverage-1" },
      understanding_ref: { understanding_id: "understanding-1", revision: 1 },
    },
  }, { kind: "wizardReview" });
  await page.getByRole("button", { name: "确认业务功能目录完整", exact: true }).click();
  await page.getByRole("button", { name: "确认并开始", exact: true }).click();
  await sendCommands(page, "commit", commitCommands);
  await answer(page, "save_case", { record_revision: 1 }, "writeDispatch");
  await answer(page, "save_acceptance", { record_revision: 1 }, "writeDispatch");
  const publish = await answer(page, "publish_plan", {
    published: true,
    plan_id: "plan-guided",
    basis_confirmations: [{ confirmation_id: "basis-1", case_id: "case-1", basis_revision: 1, confirmed_at_commit: 1 }],
  }, "controlledDispatch");
  expect(publish["intentId"]).toBe("intent-publish");

  await sendCommands(page, "prepare", prepareCommands, form => {
    // 人工四项检查已移除；表单不再携带 checks。
    expect("checks" in form).toBe(false);
  });
  await answer(page, "prepare_run", { prepared_run_id: "prepared-1" }, "writeDispatch");
  await sendCommands(page, "register", registerCommands, form => {
    expect("checks" in form).toBe(false);
  });
  await answer(page, "register_run", { run_id: "run-1", steps: [{ step_id: "step-1" }] }, "writeDispatch");

  await expect(page.getByText("运行已登记，可以开始执行")).toBeVisible();
  await page.getByRole("button", { name: "进入测试工作台", exact: true }).click();
  await expect(page.getByText("按步骤执行与核验")).toBeVisible();

  const messages = await sentMessages(page);
  const writeActions = new Set([
    "save_context", "save_binding", "save_environment", "analyze_project",
    "save_model_outbound_policy", "generate_draft", "save_case", "save_acceptance",
    "publish_plan", "prepare_run", "register_run",
  ]);
  for (const message of messages) {
    const action = typeof message["action"] === "string" ? message["action"] : "";
    if (!writeActions.has(action)) continue;
    expect(["writeDispatch", "controlledDispatch"]).toContain(String(message["kind"]));
    expect(message["contextId"]).toBe("ctx-1");
  }
});

