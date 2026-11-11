import { test, expect, type Page } from "@playwright/test";
import { connect, connectWithRun, mountPanel, reply, replyDoctor, replyView, sentMessages, waitForSend } from "./support";

function captureErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  return errors;
}

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
  gap_codes: ["assertion_basis_unconfirmed", "environment_evidence_incomplete"],
  manual_pending_step_ids: ["step-3"],
  pending_verification_step_ids: ["step-2"],
};

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
  selected_case_ids: ["case-1", "case-2", "case-3", "case-4"],
  required_case_ids: ["case-1", "case-2"],
  selected_summary: { denominator: 4, executed_count: 2, verified_count: 1, failed_count: 0, unverified_count: 3 },
  required_summary: { denominator: 2, executed_count: 1, verified_count: 1, failed_count: 0, unverified_count: 1 },
  automatic_step_count: 4,
  automatic_completed_count: 2,
  manual_pending_step_ids: ["step-3"],
  pending_verification_step_ids: ["step-2"],
  steps: [
    { step_id: "step-1", case_id: "case-1", state: "completed", current_attempt_id: "att-1", attempt_state: "succeeded", adapter_kind: "command", evidence_ids: ["ev-1"], evidence_revisions: [1] },
    { step_id: "step-2", case_id: "case-2", state: "pending_verification", current_attempt_id: "att-2", attempt_state: "succeeded", adapter_kind: "command", evidence_ids: ["ev-2"], evidence_revisions: [1] },
    { step_id: "step-3", case_id: "case-3", state: "pending", current_attempt_id: null, attempt_state: null, adapter_kind: "manual_evidence", evidence_ids: [], evidence_revisions: [] },
    { step_id: "step-4", case_id: "case-4", state: "not_started", current_attempt_id: null, attempt_state: null, adapter_kind: "command", evidence_ids: [], evidence_revisions: [] },
  ],
};

const report = {
  record_id: "report-1",
  record_revision: 2,
  record_kind: "report",
  historical_snapshot: true,
  record: {
    report_id: "report-1",
    content_revision: 2,
    run_id: "run-1",
    business_outcome: "incomplete",
    evidence_grade: "C",
    decision: {
      business_outcome: "incomplete",
      evidence_grade: "C",
      selected_summary: { denominator: 4, executed_count: 2, verified_count: 1, unverified_count: 3 },
      gaps: [{ code: "assertion_basis_unconfirmed", safe_reason: "断言依据尚未确认" }],
    },
    evidence_refs: ["ev-1", "ev-2"],
    evidence_revisions: { "ev-1": 1, "ev-2": 1 },
    issue_revisions: {},
    blocking_issue_ids: [],
    created_at: "2026-11-10T12:00:00Z",
  },
};

const planView = {
  record_id: "plan-1",
  record_revision: 1,
  record_kind: "plan",
  historical_snapshot: true,
  record: {
    plan_id: "plan-1",
    revision: 1,
    status: "published",
    run_tier: "full",
    initial_driver: "planned",
    scope: { scope_id: "scope-1", name: "工单状态修改", objective: "验证状态修改后持久化正确", required_case_ids: ["case-1"] },
    required_case_ids: ["case-1"],
    template_versions: [{ template_id: "python-library", version: "1.0.0" }],
    cases: [
      { case_id: "case-1", layer: "L1", objective: "加载入口与语法", importance: "required", verification_method: "pure-function-stdout", assertion_basis: { state: "confirmed" } },
      { case_id: "case-2", layer: "L2", objective: "正常输入返回预期", importance: "required", verification_method: "pure-function-stdout", assertion_basis: { state: "present_unconfirmed" } },
    ],
  },
};

test("home view renders the current run without recomputing", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await expect(page.getByText("工单状态修改", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("本轮范围")).toBeVisible();
  await expect(page.getByText("还差什么")).toBeVisible();
  await expect(page.getByText("需要你处理")).toBeVisible();
  await page.screenshot({ path: "test-results/home-narrow.png", fullPage: true });
  await page.setViewportSize({ width: 1080, height: 800 });
  await page.waitForTimeout(150);
  await page.screenshot({ path: "test-results/home-wide.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("workbench view lists steps and evidence", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.getByRole("button", { name: "步骤", exact: true }).click();
  await expect(page.getByText("按步骤执行与核验")).toBeVisible();
  await expect(page.locator(".card-title").filter({ hasText: "第 2 步 · case-2" })).toBeVisible();
  await expect(page.getByText("证据", { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: "test-results/workbench.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("plan view reads an exact plan revision", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.getByRole("button", { name: "计划", exact: true }).click();
  await reply(page, {    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [{ aggregate_kind: "plan", record_id: "plan-1", revision: 1 }] } },
  }, { action: "query" });
  await expect(page.getByText("已发布的检查计划")).toBeVisible();
  await page.getByText("计划 plan-1", { exact: false }).click();
  await replyView(page, "plan", planView);
  await expect(page.getByText("用例与依据")).toBeVisible();
  await expect(page.getByText("已发布", { exact: true })).toBeVisible();
  await page.waitForTimeout(150);
  await page.screenshot({ path: "test-results/plan.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("report view separates outcome, gaps and evidence", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [{ aggregate_kind: "report", record_id: "report-1", revision: 2 }] } },
  }, { action: "query" });
  await expect(page.getByText("报告修订", { exact: true })).toBeVisible();
  await page.getByText("报告 report-1", { exact: false }).click();
  await replyView(page, "report", report);
  await expect(page.getByText("证据索引")).toBeVisible();
  await expect(page.getByText("业务结论：未完成")).toBeVisible();
  await page.waitForTimeout(150);
  await page.screenshot({ path: "test-results/report.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("settings view loads only host-owned model profiles", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.getByRole("button", { name: "设置", exact: true }).first().click();
  await reply(page, {
    kind: "wizardResult",
    modelSettings: { endpoint: "https://api.deepseek.com", model: "deepseek-flash", timeout: 60, reference: "cred-1" },
    modelProfiles: [{ profile_id: "p1", name: "默认 DeepSeek", settings: { endpoint: "https://api.deepseek.com", model: "deepseek-flash", timeout: 60, reference: "cred-1" } }],
    activeProfileId: "p1",
  }, { kind: "wizardModelLoad" });
  await expect(page.getByText("模型 API", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("已保存的模型配置")).toBeVisible();
  await expect(page.getByText("核心连接")).toBeVisible();
  await page.getByRole("button", { name: "保存默认配置", exact: true }).click();
  const save = await waitForSend(page, { kind: "wizardModelSave" });
  expect(save["endpoint"]).toBe("https://api.deepseek.com");
  expect(save["model"]).toBe("deepseek-flash");
  expect(save["timeout"]).toBe(60);
  expect(save["reference"]).toBe("cred-1");
  expect(save["profile_id"]).toBe("p1");
  await reply(page, {
    kind: "wizardResult",
    modelSaved: true,
    modelSettings: { endpoint: "https://api.deepseek.com", model: "deepseek-flash", timeout: 60, reference: "cred-1" },
    modelProfiles: [{ profile_id: "p1", name: "默认 DeepSeek", settings: { endpoint: "https://api.deepseek.com", model: "deepseek-flash", timeout: 60, reference: "cred-1" } }],
    activeProfileId: "p1",
  }, { kind: "wizardModelSave" });
  await expect(page.getByText("默认模型配置已保存", { exact: false })).toBeVisible();

  const beforeRestart = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "重启核心", exact: true }).click();
  await waitForSend(page, { kind: "restartCore" }, 5000, beforeRestart);
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
  await expect(page.getByText("核心已受控重启", { exact: false })).toBeVisible();

  await page.screenshot({ path: "test-results/settings.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("diagnostics view shows dependency states and blocked actions", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run, {
    dependencies: {
      dependencies: [
        { key: "connection", state: "ready", reason: "" },
        { key: "model", state: "not_configured", reason: "未配置模型" },
      ],
      action_dependencies: { verify_pending: ["model"], query: [] },
    },
  });
  await page.getByRole("button", { name: "诊断", exact: true }).first().click();
  await expect(page.getByText("连接、依赖与高级操作")).toBeVisible();
  await expect(page.getByText("依赖状态")).toBeVisible();
  await expect(page.getByText("未配置", { exact: true })).toBeVisible();
  await expect(page.getByText("高级动作")).toBeVisible();
  await page.screenshot({ path: "test-results/diagnostics.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("report save and export stay on the write channel", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [] } },
  }, { action: "query" });
  await expect(page.getByText("还没有报告修订")).toBeVisible();

  const beforeCreate = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "保存本次报告", exact: true }).click();
  const create = await waitForSend(page, { action: "create_report" }, 5000, beforeCreate);
  expect(create["kind"]).toBe("writeDispatch");
  expect(create["expectedRevision"]).toBe(0);
  const createParameters = create["parameters"] as Record<string, unknown>;
  expect(createParameters["run_id"]).toBe("run-1");
  await reply(page, {
    kind: "outcome", action: "create_report", status: "ok",
    response: { result: { record_id: createParameters["report_id"], revision: 1 } },
  }, { action: "create_report" });

  await waitForSend(page, { action: "query" }, 5000, beforeCreate);
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [{ aggregate_kind: "report", record_id: createParameters["report_id"], revision: 1 }] } },
  }, { action: "query" });
  await waitForSend(page, { action: "read_view", view: "report" }, 5000, beforeCreate);
  await replyView(page, "report", {
    ...report,
    record: { ...report.record, report_id: createParameters["report_id"], content_revision: 1 },
  });
  await expect(page.getByText("证据索引")).toBeVisible();

  const beforeExport = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "导出 Markdown 摘要", exact: true }).click();
  const exported = await waitForSend(page, { action: "export_report" }, 5000, beforeExport);
  expect(exported["kind"]).toBe("writeDispatch");
  expect((exported["parameters"] as Record<string, unknown>)["report_revision"]).toBe(1);
  await reply(page, {
    kind: "outcome", action: "export_report", status: "ok",
    response: { result: { record_id: "export-1", revision: 1 } },
  }, { action: "export_report" });
  await expect(page.getByText("导出请求已返回")).toBeVisible();
  expect(errors).toEqual([]);
});

test("wizard view asks for project and goal before any write", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connect(page);
  await page.getByRole("button", { name: "继续准备测试", exact: true }).click();
  await expect(page.getByText("选择一个本地项目与测试入口")).toBeVisible();
  await page.screenshot({ path: "test-results/wizard-choose.png", fullPage: true });
  await page.getByRole("button", { name: "选择项目目录", exact: true }).click();
  await reply(page, {
    kind: "wizardResult",
    session: {
      project_id: "guided-0123456789abcdef0123456789abcdef",
      root: "C:/data/guided",
      source: "C:/samples/orders",
      entry: "main.py",
      name: "订单计算",
      endpoint: "https://api.deepseek.com",
      model: "deepseek-flash",
      timeout: 60,
      reference: "cred-1",
    },
  }, { kind: "wizardChoose" });
  await expect(page.getByText("这次要检查什么")).toBeVisible();
  await expect(page.getByText("模型辅助")).toBeVisible();
  await page.screenshot({ path: "test-results/wizard-configure.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("issue detail confirms through the controlled channel", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [] } },
  }, { action: "query" });
  await page.getByRole("button", { name: "问题", exact: true }).click();
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [{ aggregate_kind: "issue", record_id: "issue-1", revision: 1 }] } },
  }, { action: "query" });
  await page.getByText("问题 issue-1", { exact: false }).click();
  const issueView = {
    record_id: "issue-1",
    record_revision: 1,
    record_kind: "issue",
    historical_snapshot: true,
    record: {
      issue_id: "issue-1",
      revision: 1,
      title: "状态未持久化",
      description: "提交后再次查询仍为旧状态",
      severity: "P1",
      status: "open",
      disposition: "active",
      expected: "状态应为 resolved",
      actual: "状态仍为 pending",
      run_id: "run-1",
      case_id: "case-2",
      step_id: "step-2",
      attempt_id: "att-2",
      facts: ["查询返回旧状态"],
      hypotheses: ["写入未提交"],
      evidence_refs: [],
      evidence_revisions: {},
    },
  };
  await replyView(page, "issue", issueView);
  await page.getByText("展开处理表单", { exact: true }).click();
  await page.getByLabel("责任人").fill("张三");
  const beforeSubmit = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "提交问题操作", exact: true }).click();
  const update = await waitForSend(page, { action: "update_issue" }, 5000, beforeSubmit);
  expect(update["kind"]).toBe("controlledDispatch");
  expect(update["target"]).toBe("issue-1");
  expect(update["expectedRevision"]).toBe(1);
  expect(update["parameters"]).toMatchObject({
    issue_id: "issue-1",
    confirm: { severity: "P1", owner: "张三" },
  });
  const beforeReply = (await sentMessages(page)).length;
  await reply(page, {
    kind: "outcome", action: "update_issue", status: "ok",
    response: { result: { aggregate_kind: "issue", record_id: "issue-1", revision: 2 } },
  }, { action: "update_issue" });
  await waitForSend(page, { action: "query" }, 5000, beforeReply);
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [{ aggregate_kind: "issue", record_id: "issue-1", revision: 2 }] } },
  }, { action: "query" });
  await waitForSend(page, { action: "read_view", view: "issue" }, 5000, beforeReply);
  await replyView(page, "issue", { ...issueView, record_revision: 2, record: { ...issueView.record, revision: 2 } });
  await expect(page.getByText("问题操作已保存", { exact: false })).toBeVisible();
  expect(errors).toEqual([]);
});

test("advanced action catalog explains the task and prefills current context", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.getByRole("button", { name: "诊断", exact: true }).first().click();
  const advanced = page.getByTestId("advanced-actions");
  await advanced.locator("select").selectOption("revise_pending_steps");
  await expect(page.getByText("修订后续步骤", { exact: true })).toBeVisible();
  await expect(page.getByText("何时使用：运行中需要按新的已保存用例修订未执行步骤。", { exact: false })).toBeVisible();
  await advanced.getByRole("button", { name: "填入当前上下文", exact: true }).click();
  await expect(advanced.locator("textarea")).toHaveValue(/"run_id": "run-1"/);
  await expect(advanced.locator("textarea")).toHaveValue(/"base_snapshot_commit_id": "snap-abc"/);
  await advanced.locator("textarea").fill(JSON.stringify({
    run_id: "run-1",
    base_snapshot_commit_id: "snap-abc",
    case_changes: [],
    confirmation_ids: [],
    reason: "高级动作通道核对",
  }));
  const beforeAdvanced = (await sentMessages(page)).length;
  await advanced.getByRole("button", { name: "提交动作", exact: true }).click();
  const advancedWrite = await waitForSend(page, { action: "revise_pending_steps" }, 5000, beforeAdvanced);
  expect(advancedWrite["kind"]).toBe("controlledDispatch");
  await reply(page, {
    kind: "outcome", action: "revise_pending_steps", status: "ok",
    response: { result: { accepted: true } },
  }, { action: "revise_pending_steps" });
  await expect(page.getByText("\"accepted\": true", { exact: false })).toBeVisible();
  expect(errors).toEqual([]);
});

test("local review is recorded against the exact report and carried into export", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [{ aggregate_kind: "report", record_id: "report-1", revision: 2 }] } },
  }, { action: "query" });
  await page.getByText("报告 report-1", { exact: false }).click();
  await replyView(page, "report", report);
  await page.getByLabel("复核结论").fill("通过");
  await page.getByLabel("复核说明").fill("已核对报告范围与证据引用");
  const beforeReview = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "保存本地复核", exact: true }).click();
  const review = await waitForSend(page, { action: "record_local_review" }, 5000, beforeReview);
  expect(review["kind"]).toBe("controlledDispatch");
  const reviewParameters = review["parameters"] as Record<string, unknown>;
  expect(reviewParameters).toMatchObject({
    report_id: "report-1",
    report_revision: 2,
    conclusion: "通过",
    explanation: "已核对报告范围与证据引用",
  });
  const reviewId = reviewParameters["review_id"];
  expect(typeof reviewId).toBe("string");
  await reply(page, {
    kind: "outcome", action: "record_local_review", status: "ok",
    response: { result: { aggregate_kind: "local_review", record_id: reviewId, revision: 1 } },
  }, { action: "record_local_review" });
  await expect(page.getByText("本次会话已保存复核", { exact: false })).toBeVisible();

  const beforeExport = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "导出 Markdown 摘要", exact: true }).click();
  const exported = await waitForSend(page, { action: "export_report" }, 5000, beforeExport);
  expect((exported["parameters"] as Record<string, unknown>)["review_ids"]).toEqual([reviewId]);
  await reply(page, {
    kind: "outcome", action: "export_report", status: "ok",
    response: { result: { record_id: "export-1", revision: 1 } },
  }, { action: "export_report" });
  await expect(page.getByText("导出请求已返回")).toBeVisible();
  expect(errors).toEqual([]);
});


test("local review history loads on demand and is carried into export", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [{ aggregate_kind: "report", record_id: "report-1", revision: 2 }] } },
  }, { action: "query" });
  await page.getByText("报告 report-1", { exact: false }).click();
  await replyView(page, "report", report);

  const beforeHistory = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "读取历史复核", exact: true }).click();
  const historyQuery = await waitForSend(page, { action: "query" }, 5000, beforeHistory);
  expect((historyQuery["parameters"] as Record<string, unknown>)["aggregate_kind"]).toBe("local_review");
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [{ aggregate_kind: "local_review", record_id: "review-history-1", revision: 1 }] } },
  }, { action: "query" });
  await waitForSend(page, { action: "read_view", view: "local_review" }, 5000, beforeHistory);
  await replyView(page, "local_review", {
    record_id: "review-history-1",
    record_revision: 1,
    record_kind: "local_review",
    historical_snapshot: true,
    record: {
      review_id: "review-history-1",
      report_id: "report-1",
      report_revision: 2,
      reviewer: "复核人",
      conclusion: "通过",
      explanation: "历史结论",
      recorded_at: "2026-11-11T00:00:00+00:00",
    },
  });
  await expect(page.getByText("复核人 · 通过：历史结论", { exact: false })).toBeVisible();

  const beforeExport = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "导出 Markdown 摘要", exact: true }).click();
  const exported = await waitForSend(page, { action: "export_report" }, 5000, beforeExport);
  expect((exported["parameters"] as Record<string, unknown>)["review_ids"]).toEqual(["review-history-1"]);
  expect(errors).toEqual([]);
});


test("delivery tab saves task and delivery, then submits exact references", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  const beforeReports = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await waitForSend(page, { action: "query" }, 5000, beforeReports);
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [] } },
  }, { action: "query" });
  const beforeDeliveryTab = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "提测", exact: true }).click();
  await waitForSend(page, { action: "query" }, 5000, beforeDeliveryTab);
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [] } },
  }, { action: "query" });
  await expect(page.getByText("还没有交付说明", { exact: false })).toBeVisible();

  await page.getByText("填写任务", { exact: true }).click();
  await page.getByLabel("任务编号").first().fill("task-1");
  await page.getByLabel("目标", { exact: true }).fill("验证状态修改");
  await page.getByLabel("范围", { exact: true }).fill("工单模块");
  await page.getByLabel("验收项").fill("AC-1 | 修改后状态持久化 | true");
  const beforeTask = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "保存任务草稿", exact: true }).click();
  const task = await waitForSend(page, { action: "save_task" }, 5000, beforeTask);
  expect(task["kind"]).toBe("writeDispatch");
  expect(task["parameters"]).toMatchObject({
    task: { task_id: "task-1", project_id: "project-1", goal: "验证状态修改", scope: "工单模块" },
  });
  await reply(page, {
    kind: "outcome", action: "save_task", status: "ok",
    response: { result: { aggregate_kind: "task", record_id: "task-1", revision: 1 } },
  }, { action: "save_task" });
  await expect(page.getByText("任务已保存", { exact: false })).toBeVisible();

  await page.getByText("填写交付说明", { exact: true }).click();
  await page.getByLabel("交付编号").fill("delivery-1");
  await page.getByLabel("任务编号").last().fill("task-1");
  await page.getByLabel("版本").fill("1.0.0");
  await page.getByLabel("运行方式").fill("人工执行");
  await page.getByLabel("自述已完成（每行一项）").fill("状态修改");
  await page.getByLabel("未验证范围（每行一项）").fill("并发场景");
  const beforeDelivery = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "保存交付说明", exact: true }).click();
  const delivery = await waitForSend(page, { action: "save_delivery" }, 5000, beforeDelivery);
  expect(delivery["kind"]).toBe("writeDispatch");
  expect(delivery["parameters"]).toMatchObject({
    task_revision: 1,
    delivery: {
      delivery_id: "delivery-1",
      task_id: "task-1",
      version: "1.0.0",
      run_method: "人工执行",
      self_report: { completed: ["状态修改"], incomplete: [] },
      verified_in_scope: [],
      unverified_scope: ["并发场景"],
    },
  });
  await reply(page, {
    kind: "outcome", action: "save_delivery", status: "ok",
    response: { result: { aggregate_kind: "delivery", record_id: "delivery-1", revision: 1 } },
  }, { action: "save_delivery" });
  await waitForSend(page, { action: "read_view", view: "delivery" }, 5000, beforeDelivery);
  const deliveryView = {
    delivery_id: "delivery-1",
    revision: 1,
    version: "1.0.0",
    task_id: "task-1",
    run_method: "人工执行",
    self_report_completed: ["状态修改"],
    self_report_incomplete: [],
    verified_in_scope: [],
    unverified_scope: ["并发场景"],
    verification_state: "required_verification_incomplete",
    submitted: false,
  };
  await replyView(page, "delivery", deliveryView);
  await expect(page.getByText("未提测", { exact: true })).toBeVisible();

  const beforeMaterials = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "准备提交材料", exact: true }).click();
  await waitForSend(page, { action: "query" }, 5000, beforeMaterials);
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [{ aggregate_kind: "project", record_id: "project-1", revision: 3 }] } },
  }, { action: "query" });
  await waitForSend(page, { action: "query" }, 5000, beforeMaterials);
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [{ aggregate_kind: "source_snapshot", record_id: "snap-1", revision: 2 }] } },
  }, { action: "query" });
  const beforeSubmit = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "确认正式提测", exact: true }).click();
  const submission = await waitForSend(page, { action: "submit_delivery" }, 5000, beforeSubmit);
  expect(submission["kind"]).toBe("controlledDispatch");
  expect(submission["parameters"]).toMatchObject({
    project_revision: 3,
    expected_revision: 0,
    delivery_ref: { delivery_id: "delivery-1", record_revision: 1 },
    source_ref: { snapshot_id: "snap-1", record_revision: 2 },
  });
  await reply(page, {
    kind: "outcome", action: "submit_delivery", status: "ok",
    response: { result: { aggregate_kind: "delivery_submission", record_id: "submission-1", revision: 1 } },
  }, { action: "submit_delivery" });
  await waitForSend(page, { action: "read_view", view: "delivery" }, 5000, beforeSubmit);
  await replyView(page, "delivery", {
    ...deliveryView,
    submitted: true,
    submission: { submission_id: "submission-1", revision: 1, version: "1.0.0", content_identity: "sha256:source", acceptance_item_ids: ["AC-1"] },
  });
  await expect(page.getByText("已提测", { exact: true }).first()).toBeVisible();
  expect(errors).toEqual([]);
});


test("rule editor saves a draft and publishes it through native confirmation", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  const beforeRules = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "规则", exact: true }).click();
  await waitForSend(page, { action: "query", aggregateKind: "rule_draft" }, 5000, beforeRules);
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [] } },
  }, { action: "query", aggregateKind: "rule_draft" });
  await waitForSend(page, { action: "query", aggregateKind: "rule_version" }, 5000, beforeRules);
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [] } },
  }, { action: "query", aggregateKind: "rule_version" });
  await expect(page.getByText("还没有规则草稿", { exact: false })).toBeVisible();

  await page.getByText("填写规则", { exact: true }).click();
  await page.getByLabel("规则编号").fill("rule-order-api");
  await page.getByLabel("适用范围").fill("订单模块");
  await page.getByLabel("正文说明").fill("订单接口必须核对持久化结果");
  await page.getByLabel("步骤（每行一项）").fill("创建订单\n查询订单");
  await page.getByLabel("证据要求（每行一项）").fill("请求与响应\n关键日志");
  const beforeSave = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "保存新草稿", exact: true }).click();
  const saved = await waitForSend(page, { action: "save_rules" }, 5000, beforeSave);
  expect(saved["kind"]).toBe("writeDispatch");
  expect(saved["parameters"]).toMatchObject({
    draft: {
      rule_id: "rule-order-api",
      revision: 1,
      scope: "订单模块",
      steps: ["创建订单", "查询订单"],
      evidence_requirements: ["请求与响应", "关键日志"],
      confirmed: false,
      enablement: "disabled",
    },
  });
  await reply(page, {
    kind: "outcome", action: "save_rules", status: "ok",
    response: { result: { saved: { aggregate_kind: "rule_draft", record_id: "rule-order-api", revision: 1 } } },
  }, { action: "save_rules" });
  await waitForSend(page, { action: "query", aggregateKind: "rule_draft" }, 5000, beforeSave);
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [{ aggregate_kind: "rule_draft", record_id: "rule-order-api", revision: 1 }] } },
  }, { action: "query", aggregateKind: "rule_draft" });
  await waitForSend(page, { action: "query", aggregateKind: "rule_version" }, 5000, beforeSave);
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [] } },
  }, { action: "query", aggregateKind: "rule_version" });
  await waitForSend(page, { action: "read_view", view: "rule_draft" }, 5000, beforeSave);
  await replyView(page, "rule_draft", {
    record_id: "rule-order-api",
    record_revision: 1,
    record_kind: "rule_draft",
    historical_snapshot: true,
    record: {
      rule_id: "rule-order-api",
      revision: 1,
      scope: "订单模块",
      text: "订单接口必须核对持久化结果",
      source: "manual",
      steps: ["创建订单", "查询订单"],
      evidence_requirements: ["请求与响应", "关键日志"],
      unknown_extension_fields: [],
    },
  });
  await expect(page.getByText("规则草稿 rule-order-api", { exact: false })).toBeVisible();

  const beforePublish = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "确认并发布", exact: true }).click();
  await waitForSend(page, { action: "query", aggregateKind: "project" }, 5000, beforePublish);
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [{ aggregate_kind: "project", record_id: "project-1", revision: 2 }] } },
  }, { action: "query", aggregateKind: "project" });
  const publish = await waitForSend(page, { action: "publish_rules" }, 5000, beforePublish);
  expect(publish["kind"]).toBe("controlledDispatch");
  expect(publish["parameters"]).toMatchObject({
    project_revision: 2,
    expected_revision: 0,
    draft: { rule_id: "rule-order-api", confirmed: true },
    context_gaps: [],
  });
  await reply(page, {
    kind: "outcome", action: "publish_rules", status: "ok",
    response: { result: { record_id: "rule-order-api", revision: 1 } },
  }, { action: "publish_rules" });
  await waitForSend(page, { action: "query", aggregateKind: "rule_draft" }, 5000, beforePublish);
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [] } },
  }, { action: "query", aggregateKind: "rule_draft" });
  await waitForSend(page, { action: "query", aggregateKind: "rule_version" }, 5000, beforePublish);
  await reply(page, {
    kind: "outcome", action: "query", status: "ok",
    response: { result: { items: [{ aggregate_kind: "rule_version", record_id: "rule-order-api", revision: 1 }] } },
  }, { action: "query", aggregateKind: "rule_version" });
  await expect(page.getByText("规则已发布为不可变版本", { exact: false })).toBeVisible();
  const beforeVersion = (await sentMessages(page)).length;
  await page.getByText("规则 rule-order-api", { exact: false }).click();
  await waitForSend(page, { action: "read_view", view: "rule_version" }, 5000, beforeVersion);
  await replyView(page, "rule_version", {
    record_id: "rule-order-api",
    record_revision: 1,
    record_kind: "rule_version",
    historical_snapshot: true,
    record: {
      rule_id: "rule-order-api",
      revision: 1,
      scope: "订单模块",
      text: "订单接口必须核对持久化结果",
      source: "manual",
      steps: ["创建订单"],
      evidence_requirements: ["请求与响应"],
      confirmation_id: "confirmation-rule-1",
      digest: "sha256:rule",
      status: "published",
    },
  });
  const beforeExport = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "导出 Markdown", exact: true }).click();
  const exported = await waitForSend(page, { action: "export_rules_markdown" }, 5000, beforeExport);
  expect(exported["kind"]).toBe("dispatch");
  expect(exported["parameters"]).toMatchObject({
    rule_versions: [{ rule_id: "rule-order-api", revision: 1 }],
  });
  await reply(page, {
    kind: "outcome", action: "export_rules_markdown", status: "ok",
    response: { result: { documents: [{ rule_id: "rule-order-api", revision: 1, markdown: "# 规则：订单接口" }] } },
  }, { action: "export_rules_markdown" });
  await expect(page.getByText("# 规则：订单接口", { exact: false })).toBeVisible();
  expect(errors).toEqual([]);
});


test("panel confirmation shows the exact prompt and returns the click on the host channel", async ({ page }) => {
  const errors = captureErrors(page);
  await mountPanel(page);
  await connectWithRun(page, panel, run);
  await page.evaluate(() => {
    window.postMessage({
      kind: "confirmationPrompt",
      clientRequestId: "confirm-1",
      prompt: {
        title: "发布核对后的测试计划",
        note: "发布后计划冻结，后续修改会产生新的修订。",
        facts: [["项目", "project-1"], ["计划", "plan-1"], ["运行档位", "完整验证"]],
        details: "exact frozen material",
      },
    }, "*");
  });
  await expect(page.getByText("发布核对后的测试计划", { exact: true })).toBeVisible();
  await expect(page.getByText("发布后计划冻结", { exact: false })).toBeVisible();
  const beforeConfirm = (await sentMessages(page)).length;
  await page.getByRole("button", { name: "确认并继续", exact: true }).click();
  const result = await waitForSend(page, { kind: "confirmationResult" }, 5000, beforeConfirm);
  expect(result["clientRequestId"]).toBe("confirm-1");
  expect(result["confirmed"]).toBe(true);
  await expect(page.getByText("发布核对后的测试计划", { exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});
