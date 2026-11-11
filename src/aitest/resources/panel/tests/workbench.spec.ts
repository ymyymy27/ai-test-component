import { test, expect } from "@playwright/test";
import { connectWithRun, mountPanel, reply, replyView, sentMessages, waitForSend } from "./support";

const panel = {
  project_id: "project-1",
  project_name: "工单状态修改",
  run_id: "run-1",
  stage: "runtime",
  control_state: "running",
  run_revision: 3,
  snapshot_commit_id: "snap-abc",
  snapshot_cursor: 12,
  tier: "full",
  driver: "planned",
  conclusion_ceiling: "incomplete",
  business_outcome: "incomplete",
  decisive_failure_case_ids: [],
  gap_codes: ["assertion_basis_unconfirmed"],
  manual_pending_step_ids: [],
  pending_verification_step_ids: ["step-2"],
};

const steps = [
  { step_id: "step-1", case_id: "case-1", state: "completed", current_attempt_id: "att-1", attempt_state: "completed", adapter_kind: "command", evidence_ids: ["ev-1"], evidence_revisions: [1] },
  { step_id: "step-2", case_id: "case-2", state: "pending_verification", current_attempt_id: "att-2", attempt_state: "completed", adapter_kind: "command", evidence_ids: ["ev-2"], evidence_revisions: [1] },
];

const run = {
  project_id: "project-1",
  run_id: "run-1",
  run_revision: 3,
  snapshot_commit_id: "snap-abc",
  snapshot_cursor: 12,
  plan_revision_ref: "plan-1@1",
  control_state: "running",
  driver: "planned",
  tier: "full",
  conclusion_ceiling: "incomplete",
  selected_case_ids: ["case-1", "case-2"],
  required_case_ids: ["case-1", "case-2"],
  selected_summary: { denominator: 2, executed_count: 2, verified_count: 1, failed_count: 0, unverified_count: 1 },
  required_summary: { denominator: 2, executed_count: 2, verified_count: 1, failed_count: 0, unverified_count: 1 },
  automatic_step_count: 2,
  automatic_completed_count: 1,
  manual_pending_step_ids: [],
  pending_verification_step_ids: ["step-2"],
  steps,
};

const completedRun = {
  ...run,
  run_revision: 4,
  control_state: "completed",
  verified_case_ids: ["case-1", "case-2"],
  unverified_case_ids: [],
  automatic_completed_count: 2,
  pending_verification_step_ids: [],
  steps: steps.map(step => step.step_id === "step-2" ? { ...step, state: "completed" } : step),
};

test("workbench walks prepare -> authorize -> execute -> verify and reads evidence", async ({ page }) => {
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.getByRole("button", { name: "步骤", exact: true }).click();
  await expect(page.locator(".card-title").filter({ hasText: "第 2 步 · case-2" })).toBeVisible();

  await page.getByRole("button", { name: "查看将执行的操作", exact: true }).click();
  const prepare = await waitForSend(page, { action: "prepare_execution" });
  expect(prepare["kind"]).toBe("writeDispatch");
  expect(prepare["target"]).toBe("step-2");
  expect((prepare["parameters"] as Record<string, unknown>)["run_id"]).toBe("run-1");
  await reply(page, {
    kind: "outcome", action: "prepare_execution", status: "ok",
    response: { result: { execution_action_id: "action-1" } },
  }, { action: "prepare_execution" });

  await page.getByRole("button", { name: "确认并授权", exact: true }).click();
  const authorize = await waitForSend(page, { action: "authorize_step" });
  expect(authorize["kind"]).toBe("controlledDispatch");
  expect(authorize["target"]).toBe("step-2");
  expect((authorize["parameters"] as Record<string, unknown>)["execution_action_id"]).toBe("action-1");
  await reply(page, {
    kind: "outcome", action: "authorize_step", status: "ok",
    response: { result: { authorization_id: "auth-1", step_id: "step-2" } },
  }, { action: "authorize_step" });

  await page.getByRole("button", { name: "执行已确认的操作", exact: true }).click();
  const execute = await waitForSend(page, { action: "execute_step" });
  expect(execute["kind"]).toBe("writeDispatch");
  const beforeRefresh = (await sentMessages(page)).length;
  await reply(page, {
    kind: "outcome", action: "execute_step", status: "ok",
    response: { result: { executed: true } },
  }, { action: "execute_step" });
  await waitForSend(page, { action: "read_view", view: "panel" }, 5000, beforeRefresh);
  await replyView(page, "panel", panel);
  await waitForSend(page, { action: "read_view", view: "run" }, 5000, beforeRefresh);
  await replyView(page, "run", run);

  await expect(page.getByRole("button", { name: "核验这次执行结果", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "核验这次执行结果", exact: true }).click();
  const verify = await waitForSend(page, { action: "verify_pending" });
  expect(verify["kind"]).toBe("writeDispatch");
  const verifyParameters = verify["parameters"] as Record<string, unknown>;
  expect(verifyParameters["attempt_id"]).toBe("att-2");
  expect(verifyParameters["base_snapshot_commit_id"]).toBe("snap-abc");
  const beforeVerifyRefresh = (await sentMessages(page)).length;
  await reply(page, {
    kind: "outcome", action: "verify_pending", status: "ok",
    response: { result: { verification: { observation: "matched" } } },
  }, { action: "verify_pending" });
  await waitForSend(page, { action: "read_view", view: "panel" }, 5000, beforeVerifyRefresh);
  await replyView(page, "panel", { ...panel, control_state: "completed", pending_verification_step_ids: [] });
  await waitForSend(page, { action: "read_view", view: "run" }, 5000, beforeVerifyRefresh);
  await replyView(page, "run", completedRun);

  await expect(page.getByText("该步骤已完成并取得有效核验。")).toBeVisible();

  await page.getByRole("button", { name: /查看证据 ev-2/ }).click();
  const evidence = await waitForSend(page, { action: "read_view", view: "evidence" });
  expect(evidence["parameters"]).toMatchObject({ record_id: "ev-2", revision: 1 });
  await reply(page, {
    kind: "outcome", action: "read_view", status: "ok",
    response: { result: { view_type: "evidence", view: {
      record_id: "ev-2", record_revision: 1, body: "stdout: order total 42",
      record: { evidence_id: "ev-2", evidence_revision: 1, evidence_kind: "stdout", evidence_level: "A", integrity: "verified" },
    } } },
  }, { action: "read_view", view: "evidence" });
  await expect(page.getByText("stdout: order total 42")).toBeVisible();
});

test("retry creates a new attempt through prepare and retry", async ({ page }) => {
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.getByRole("button", { name: "步骤", exact: true }).click();
  await page.getByRole("button", { name: "重试这一步", exact: true }).click();
  await expect(page.getByText("将为这一步重新解析并执行一个新尝试", { exact: false })).toBeVisible();
  const before = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "确认重试", exact: true }).click();

  const prepare = await waitForSend(page, { action: "prepare_execution" }, 5000, before);
  expect(prepare["kind"]).toBe("writeDispatch");
  const beforePrepareReply = (await sentMessages(page)).length;
  await reply(page, {
    kind: "outcome", action: "prepare_execution", status: "ok",
    response: { result: { execution_action_id: "retry-action-1" } },
  }, { action: "prepare_execution" });

  const retry = await waitForSend(page, { action: "retry_step" }, 5000, beforePrepareReply);
  expect(retry["kind"]).toBe("writeDispatch");
  expect(retry["target"]).toBe("step-2");
  expect((retry["parameters"] as Record<string, unknown>)["execution_action_id"]).toBe("retry-action-1");
  const beforeRetryReply = (await sentMessages(page)).length;
  await reply(page, {
    kind: "outcome", action: "retry_step", status: "ok",
    response: { result: { retried_from_attempt_index: 1, previous_attempt_preserved: true, attempt_id: "att-3" } },
  }, { action: "retry_step" });

  await waitForSend(page, { action: "read_view", view: "panel" }, 5000, beforeRetryReply);
  await replyView(page, "panel", panel);
  await waitForSend(page, { action: "read_view", view: "run" }, 5000, beforeRetryReply);
  await replyView(page, "run", {
    ...run,
    steps: run.steps.map(step => step.step_id === "step-2"
      ? { ...step, current_attempt_id: "att-3", state: "pending_verification" }
      : step),
  });
  await expect(page.getByText("已重试", { exact: false }).first()).toBeVisible();
});

test("return stage reports downstream steps from the core receipt", async ({ page }) => {
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.getByRole("button", { name: "步骤", exact: true }).click();
  await page.getByRole("button", { name: "退回上游", exact: true }).click();
  await expect(page.getByText("实际需重跑清单以核心回执为准", { exact: false })).toBeVisible();
  const before = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "确认退回", exact: true }).click();

  await waitForSend(page, { action: "prepare_execution" }, 5000, before);
  const beforePrepareReply = (await sentMessages(page)).length;
  await reply(page, {
    kind: "outcome", action: "prepare_execution", status: "ok",
    response: { result: { execution_action_id: "return-action-1" } },
  }, { action: "prepare_execution" });
  const returned = await waitForSend(page, { action: "return_stage" }, 5000, beforePrepareReply);
  expect(returned["kind"]).toBe("writeDispatch");
  expect((returned["parameters"] as Record<string, unknown>)["execution_action_id"]).toBe("return-action-1");
  const beforeReturnReply = (await sentMessages(page)).length;
  await reply(page, {
    kind: "outcome", action: "return_stage", status: "ok",
    response: { result: { downstream_step_ids: ["step-3"], downstream_requires_rerun: ["step-3"], downstream_note: "下游 1 个步骤需重跑" } },
  }, { action: "return_stage" });
  await waitForSend(page, { action: "read_view", view: "panel" }, 5000, beforeReturnReply);
  await replyView(page, "panel", panel);
  await waitForSend(page, { action: "read_view", view: "run" }, 5000, beforeReturnReply);
  await replyView(page, "run", run);
  await expect(page.getByText("下游需重跑：step-3", { exact: false }).first()).toBeVisible();
});

test("attach evidence prefills the current attempt and saves manual evidence", async ({ page }) => {
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.getByRole("button", { name: "步骤", exact: true }).click();
  await page.getByRole("button", { name: "补录证据", exact: true }).click();
  await expect(page.getByText("绑定当前尝试", { exact: false })).toBeVisible();
  await page.getByLabel("操作目标").fill("订单详情页");
  await page.getByLabel("实际结果").fill("页面显示已支付");
  await page.getByLabel("业务对象编号").fill("order-1");
  const before = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "提交人工证据", exact: true }).click();
  const attach = await waitForSend(page, { action: "attach_evidence" }, 5000, before);
  expect(attach["kind"]).toBe("writeDispatch");
  expect(attach["parameters"]).toMatchObject({
    run_id: "run-1", step_id: "step-2", attempt_id: "att-2",
    operation_type: "form", target_ref: "订单详情页",
    observed_result: "页面显示已支付", business_object_id: "order-1",
  });
  const beforeReply = (await sentMessages(page)).length;
  await reply(page, {
    kind: "outcome", action: "attach_evidence", status: "ok",
    response: { result: { evidence_id: "manual-evidence-1" } },
  }, { action: "attach_evidence" });
  await waitForSend(page, { action: "read_view", view: "panel" }, 5000, beforeReply);
  await replyView(page, "panel", panel);
  await waitForSend(page, { action: "read_view", view: "run" }, 5000, beforeReply);
  await replyView(page, "run", run);
  await expect(page.getByText("人工证据已保存", { exact: false }).first()).toBeVisible();
});


const revisableRun = {
  ...run,
  tier: "on_demand",
  selected_case_ids: ["case-1", "case-2", "case-3"],
  steps: [
    ...steps,
    { step_id: "step-3", case_id: "case-3", state: "not_started", current_attempt_id: null, attempt_state: null, adapter_kind: "command", evidence_ids: [], evidence_revisions: [] },
  ],
};

const revisionBasis = {
  plan_id: "plan-1",
  plan_record_revision: 1,
  plan_confirmation_id: "confirmation-plan-1",
  state: "ready",
  cases: [
    { case_id: "case-1", case_revision: 1, basis_confirmation_id: "confirmation-1" },
    { case_id: "case-2", case_revision: 1, basis_confirmation_id: "confirmation-2" },
    { case_id: "case-3", case_revision: 1, basis_confirmation_id: "confirmation-3" },
  ],
};

test("runtime revision reads the plan state and submits only saved case revisions", async ({ page }) => {
  await mountPanel(page);
  await connectWithRun(page, panel, revisableRun);
  await page.getByRole("button", { name: "步骤", exact: true }).click();
  await page.getByRole("button", { name: "读取修订依据", exact: true }).click();
  const stateQuery = await waitForSend(page, { action: "plan_confirmation_state" });
  expect((stateQuery["parameters"] as Record<string, unknown>)["plan_id"]).toBe("plan-1");
  expect((stateQuery["parameters"] as Record<string, unknown>)["plan_revision"]).toBe(1);
  await reply(page, {
    kind: "outcome", action: "plan_confirmation_state", status: "ok",
    response: { result: revisionBasis },
  }, { action: "plan_confirmation_state" });
  await expect(page.getByText("修订用例 case-3（当前修订 1）", { exact: false })).toBeVisible();

  await page.getByLabel("新用例修订").nth(2).fill("2");
  await page.getByLabel("修订原因").fill("按新修订执行剩余步骤");
  const beforeRevision = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "提交运行中修订", exact: true }).click();
  const revision = await waitForSend(page, { action: "revise_pending_steps" }, 5000, beforeRevision);
  expect(revision["kind"]).toBe("controlledDispatch");
  expect(revision["target"]).toBe("run-1");
  expect(revision["parameters"]).toMatchObject({
    run_id: "run-1",
    base_snapshot_commit_id: "snap-abc",
    case_changes: [{ case_id: "case-3", record_revision: 2, target_step_ids: ["step-3"] }],
    confirmation_ids: ["confirmation-3"],
    reason: "按新修订执行剩余步骤",
  });
  const beforeRefresh = (await sentMessages(page)).length;
  await reply(page, {
    kind: "outcome", action: "revise_pending_steps", status: "ok",
    response: { result: { affected_step_ids: ["step-3"], preserved_step_ids: [] } },
  }, { action: "revise_pending_steps" });
  await expect(page.getByText("修订已保存", { exact: false }).first()).toBeVisible();
  await waitForSend(page, { action: "read_view", view: "panel" }, 5000, beforeRefresh);
  await replyView(page, "panel", panel);
});

test("narrow driver uses a controlled dispatch with no case changes", async ({ page }) => {
  await mountPanel(page);
  await connectWithRun(page, panel, revisableRun);
  await page.getByRole("button", { name: "步骤", exact: true }).click();
  await page.getByRole("button", { name: "收窄为逐步执行", exact: true }).click();
  await page.getByLabel("收窄原因").fill("先逐步核验剩余步骤");
  const beforeNarrow = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "确认收窄", exact: true }).click();
  const narrow = await waitForSend(page, { action: "narrow_driver" }, 5000, beforeNarrow);
  expect(narrow["kind"]).toBe("controlledDispatch");
  expect(narrow["parameters"]).toMatchObject({
    run_id: "run-1",
    base_snapshot_commit_id: "snap-abc",
    case_changes: [],
    confirmation_ids: [],
    reason: "先逐步核验剩余步骤",
  });
  await reply(page, {
    kind: "outcome", action: "narrow_driver", status: "ok",
    response: { result: { driver: "stepwise" } },
  }, { action: "narrow_driver" });
  await expect(page.getByText("驱动收窄请求已返回", { exact: false }).first()).toBeVisible();
});


test("step authorization prepares unresolved steps and uses the panel confirmation channel", async ({ page }) => {
  await mountPanel(page);
  await connectWithRun(page, panel, revisableRun);
  await page.getByRole("button", { name: "步骤", exact: true }).click();
  const beforePrepare = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "准备并逐项授权", exact: true }).click();
  const prepare = await waitForSend(page, { action: "prepare_execution" }, 5000, beforePrepare);
  expect((prepare["parameters"] as Record<string, unknown>)["step_id"]).toBe("step-3");
  await reply(page, {
    kind: "outcome", action: "prepare_execution", status: "ok",
    response: { result: { execution_action_id: "action-3" } },
  }, { action: "prepare_execution" });
  const authorize = await waitForSend(page, { action: "authorize_step" }, 5000, beforePrepare);
  expect(authorize["kind"]).toBe("controlledDispatch");
  expect(authorize["target"]).toBe("step-3");
  expect((authorize["parameters"] as Record<string, unknown>)["execution_action_id"]).toBe("action-3");
  await reply(page, {
    kind: "outcome", action: "authorize_step", status: "ok",
    response: { result: { authorization_id: "auth-3" } },
  }, { action: "authorize_step" });
  await expect(page.getByText("已确认 1 项；可以逐项执行。", { exact: false }).first()).toBeVisible();
});


const reusableRun = {
  ...revisableRun,
  snapshot_ref: {
    snapshot_commit_id: "snap-abc",
    snapshot_cursor: 12,
    digest: "sha256:target-facts",
  },
};

test("reuse inspection reads the exact source snapshot and registers only after verification", async ({ page }) => {
  await mountPanel(page);
  await connectWithRun(page, panel, reusableRun);
  await page.getByRole("button", { name: "步骤", exact: true }).click();
  await page.getByLabel("来源运行编号").fill("run-source-1");
  const beforeInspect = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "核对复用资格", exact: true }).click();
  await waitForSend(page, { action: "read_view", view: "run" }, 5000, beforeInspect);
  await replyView(page, "run", {
    ...run,
    run_id: "run-source-1",
    snapshot_ref: {
      snapshot_commit_id: "snap-source",
      snapshot_cursor: 3,
      digest: "sha256:source-facts",
    },
  });
  const inspect = await waitForSend(page, { action: "inspect_case_reuse" }, 5000, beforeInspect);
  expect(inspect["parameters"]).toMatchObject({
    case_id: "case-1",
    source_run_id: "run-source-1",
    source_snapshot: {
      snapshot_commit_id: "snap-source",
      snapshot_cursor: 3,
      digest: "sha256:source-facts",
    },
    target_run_id: "run-1",
    target_snapshot: {
      snapshot_commit_id: "snap-abc",
      snapshot_cursor: 12,
      digest: "sha256:target-facts",
    },
  });
  await reply(page, {
    kind: "outcome", action: "inspect_case_reuse", status: "ok",
    response: { result: {
      status: "unverified",
      denial_reasons: ["source_verified_unverified"],
      step_mapping: [{ case_step_index: 0, logical_step_id: "step-1", source_step_id: "src-step-1", target_step_id: "step-1", source_attempt_id: "src-att-1" }],
      source_run_id: "run-source-1",
      target_run_id: "run-1",
      source_snapshot: { snapshot_commit_id: "snap-source", snapshot_cursor: 3, digest: "sha256:source-facts" },
      target_snapshot: { snapshot_commit_id: "snap-abc", snapshot_cursor: 12, digest: "sha256:target-facts" },
    } },
  }, { action: "inspect_case_reuse" });
  await expect(page.getByText("材料未发现不兼容", { exact: false })).toBeVisible();

  const beforeSelect = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "登记复用选择", exact: true }).click();
  const decisionQuery = await waitForSend(page, { action: "query" }, 5000, beforeSelect);
  expect((decisionQuery["parameters"] as Record<string, unknown>)["aggregate_kind"]).toBe("reuse_decision");
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [] } },
  }, { action: "query" });
  const selection = await waitForSend(page, { action: "select_case_reuse" }, 5000, beforeSelect);
  expect(selection["kind"]).toBe("controlledDispatch");
  expect(selection["target"]).toBe("case-1");
  expect(selection["expectedRevision"]).toBe(0);
  expect(selection["parameters"]).toMatchObject({
    case_id: "case-1",
    source_run_id: "run-source-1",
    target_run_id: "run-1",
  });
  await reply(page, {
    kind: "outcome", action: "select_case_reuse", status: "ok",
    response: { result: { aggregate_kind: "reuse_decision", record_id: "reuse-1", revision: 1 } },
  }, { action: "select_case_reuse" });
  await expect(page.getByText("复用选择已保存", { exact: false }).first()).toBeVisible();
});
