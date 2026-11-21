import { expect } from "@playwright/test";

import {
  confirmPlan,
  createAndRun,
  createProject,
  createTaskFromCandidate,
  test,
} from "./fixtures";

test("审计：建立项目、侧栏导航、项目理解与模块", async ({ page }) => {
  await page.goto("/setup");
  await page.getByRole("button", { name: "登记并分析项目" }).click();
  await expect(page.locator(".field-error").first()).toBeVisible();

  const projectId = await createProject(page, "交互审计项目", "D:/work/audit-nav");
  await page.getByRole("button", { name: "查看环境自检" }).click();
  await expect(page.locator("#env-popover")).toBeVisible();
  await page.locator(".page-title").click();
  await expect(page.locator("#env-popover")).toBeHidden();
  await page.locator('a[aria-label="打开设置"]').click();
  await page.waitForURL(/\/settings$/);
  await page.goto(`/projects/${projectId}/overview`);

  await page.fill(".sidebar-search input", "不会匹配");
  await expect(page.locator(".side-empty")).toContainText("没有匹配");
  await page.fill(".sidebar-search input", "");

  await page.locator(".side-nav").getByRole("button", { name: /^问题/ }).click();
  await page.waitForURL(/\/issues$/);
  await page.locator(".side-nav").getByRole("button", { name: /^运行记录/ }).click();
  await page.waitForURL(/\/history$/);
  await page.locator(".side-nav").getByRole("button", { name: "设置" }).click();
  await page.waitForURL(/\/settings$/);
  await page.goto(`/projects/${projectId}/overview`);

  const modules = page.locator(".project-map-details").filter({ hasText: "模块" });
  await modules.locator("summary").click();
  await page.getByRole("button", { name: "新增模块" }).click();
  await page.fill("#module-name", "审计模块");
  await page.fill("#module-responsibility", "交互审计使用");
  await page.getByRole("button", { name: "保存模块" }).click();
  await expect(page.locator(".project-map")).toContainText("审计模块");

  const auditModule = page.locator(".project-map-row").filter({ hasText: "审计模块" });
  await auditModule.getByRole("button", { name: "编辑" }).click();
  await page.fill("#module-responsibility", "审计模块已更新");
  await page.getByRole("button", { name: "保存模块" }).click();
  await expect(page.locator(".project-map")).toContainText("审计模块已更新");

  await createTaskFromCandidate(page, 0);
  await page.goto(`/projects/${projectId}/overview`);
  await expect(page.locator(".current-task-strip")).toBeVisible();
  await page.locator(".current-task-strip button").first().click();
  await page.waitForURL(/\/plan/);
});

test("审计：范围模式、计划确认、运行控制与结果筛选", async ({ page }) => {
  const projectId = await createProject(page, "交互审计流程", "D:/work/audit-flow");
  await page.getByRole("button", { name: "开始测试" }).click();
  await page.waitForURL(/tasks\/new/);
  await page.locator(".test-mode").filter({ hasText: "按范围测试" }).click();
  await page.getByRole("button", { name: "选择范围" }).click();
  await page.waitForURL(/\/scope/);
  await expect(page.locator(".scope-summary")).toContainText("/");

  await page.getByRole("button", { name: "清空", exact: true }).click();
  await expect(page.locator(".scope-summary")).toContainText("本次 0 /");
  await expect(page.getByRole("button", { name: "生成计划" })).toBeDisabled();
  await page.getByRole("button", { name: "全量" }).click();
  await expect(page.locator(".scope-summary")).toContainText("本次 4 /");
  await page.selectOption('select[aria-label="按模块筛选验收项"]', { index: 1 });
  await expect(page.locator(".scope-groups .scope-group").first()).toBeVisible();
  await page.selectOption('select[aria-label="按模块筛选验收项"]', "all");
  await page.locator(".scope-group summary input[type=checkbox]").first().uncheck();
  await expect(page.locator(".scope-summary")).toContainText("本次 3 /");
  await page.locator(".scope-group summary input[type=checkbox]").first().check();
  await expect(page.locator(".scope-summary")).toContainText("本次 4 /");

  await page.getByRole("button", { name: "生成计划" }).click();
  await page.waitForURL(/\/plan/);
  await expect(page.locator(".plan-summary")).toContainText("待执行");
  await page.getByRole("button", { name: "调整范围" }).click();
  await page.waitForURL(/\/scope/);
  await page.getByRole("button", { name: "取消调整" }).click();
  await page.waitForURL(/\/plan/);

  await confirmPlan(page);
  await page.getByRole("button", { name: "开始测试" }).click();
  await page.waitForURL(/\/result/, { timeout: 20_000 });
  await expect(page.locator(".result-verdict")).toBeVisible();

  for (const label of ["只看问题", "未完成", "全部"]) {
    await page.getByRole("button", { name: new RegExp(`^${label}`) }).click();
  }
  await expect(page.getByRole("button", { name: /^全部/ })).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("button", { name: /重新生成计划|按未通过项|按未完成项/ }).click();
  await page.waitForURL(/\/plan/);
  await expect(page.locator(".plan-summary")).toBeVisible();
  await expect(page.url()).toContain(projectId);
});

test("审计：报告操作与历史筛选", async ({ page }) => {
  const projectId = await createAndRun(page, "交互审计报告", "D:/work/audit-report");
  await page.locator(".report-strip").getByRole("button", { name: "查看报告" }).click();
  await page.waitForURL(/\/reports\//);
  const firstReportUrl = page.url();

  await page.locator("#opinion-text").fill("交互审计：结论与范围一致。");
  await page.getByRole("button", { name: "保存意见" }).click();
  await expect(page.locator(".notice").first()).toContainText("交互审计");

  await page.getByRole("button", { name: "重新生成" }).click();
  await page.waitForURL((url) => url.toString() !== firstReportUrl);
  await expect(page.locator(".toolbar").filter({ hasText: "版本" })).toBeVisible();
  await page.getByRole("button", { name: "第 1 版" }).click();
  await expect(page.locator(".notice.is-old")).toContainText("旧版本");

  await page.getByRole("button", { name: "返回运行结果" }).click();
  await page.waitForURL(/\/result/);
  await page.goto(`/projects/${projectId}/history`);
  await page.selectOption('select[aria-label="按任务筛选历史"]', { index: 0 });
  await page.selectOption('select[aria-label="按结果筛选历史"]', "failed");
  await expect(page.locator(".history-list, .empty-state").first()).toBeVisible();
  await page.selectOption('select[aria-label="按结果筛选历史"]', "all");
  await page.locator(".run-row").first().click();
  await expect(page.locator("#confirm-dialog-title, .dialog-title").first()).toBeVisible();
  await page.getByRole("button", { name: "查看报告" }).click();
  await page.waitForURL(/\/reports\//);
});

test("审计：问题登记、筛选与状态流转", async ({ page }) => {
  const projectId = await createProject(page, "交互审计问题", "D:/work/audit-issue");
  await createTaskFromCandidate(page, 0);
  await page.goto(`/projects/${projectId}/issues`);
  await page.getByRole("button", { name: "登记问题" }).click();
  await page.fill("#issue-title", "审计登记的问题");
  await page.fill("#issue-actual", "列表没有刷新");
  await page.fill("#issue-expected", "保存后列表刷新");
  await page.fill("#issue-steps", "打开列表\n保存\n观察");
  await page.getByRole("button", { name: "保存问题" }).click();
  await page.waitForURL(/\/issues\//);
  await expect(page.locator("h1").first()).toContainText("审计登记的问题");

  await page.getByRole("button", { name: "更新状态" }).click();
  await page.selectOption("#issue-next-status", "in_progress");
  await page.fill("#issue-reason", "开始处理");
  await page.getByRole("button", { name: "保存状态" }).click();
  await expect(page.locator("body")).toContainText("处理中");

  await page.getByRole("button", { name: "全部问题" }).click();
  await page.waitForURL(/\/issues$/);
  await page.locator(".tabs").getByRole("button", { name: /待确认/ }).click();
  await page.locator(".tabs").getByRole("button", { name: /处理中/ }).click();
  await page.fill('input[aria-label="搜索问题"]', "审计登记");
  await expect(page.locator(".issue-row")).toHaveCount(1);
  await page.fill('input[aria-label="搜索问题"]', "不存在");
  await expect(page.locator(".empty-state")).toBeVisible();
  await page.getByRole("button", { name: "清空筛选" }).click();
  await expect(page.locator(".issue-row")).toHaveCount(1);
});

test("审计：任务资料、规则、主题与数据清空", async ({ page }) => {
  const projectId = await createProject(page, "交互审计设置", "D:/work/audit-settings");
  await createTaskFromCandidate(page, 0);
  const taskId = page.url().match(/tasks\/([^/]+)/)?.[1] ?? "";
  await page.goto(`/projects/${projectId}/tasks/${taskId}/details`);
  await page.fill("#task-title", "审计任务改名");
  await page.fill("#task-executor", "审计员");
  await page.getByRole("button", { name: "保存基本信息" }).click();
  await expect(page.locator(".toast")).toContainText("任务资料已保存");
  await expect(page.locator("h1").first()).toContainText("审计任务改名");

  await page.getByRole("button", { name: "新增验收项" }).click();
  await page.locator('input[aria-label="验收项文本"]').last().fill("审计新增的验收项");
  await page.getByRole("button", { name: "保存验收项" }).click();
  await expect(page.locator(".toast")).toContainText("验收项已保存");
  await page.locator(".acceptance-edit-row").last().getByRole("button", { name: "删除" }).click();
  await page.getByRole("button", { name: "保存验收项" }).click();
  await expect(page.locator(".toast")).toContainText("验收项已保存");

  await page.getByRole("button", { name: "起草交付说明" }).click();
  await expect(page.locator(".toast")).toContainText(/交付草稿已填入|未配置模型密钥/);
  await page.fill("#delivery-done", "完成审计");
  await page.fill("#delivery-commands", "pnpm test");
  await page.fill("#delivery-commit", "abc123");
  await page.getByRole("button", { name: "保存交付说明" }).click();
  await expect(page.locator(".toast")).toContainText("交付说明已保存");
  await page.getByRole("button", { name: "应用" }).first().click();
  await expect(page.locator(".template-results")).toBeVisible();

  await page.goto(`/projects/${projectId}/settings`);

  await page.locator(".settings-index").getByRole("tab", { name: "项目规则" }).click();
  await page.fill("#rule-name", "审计规则");
  await page.fill("#rule-version", "3");
  await page.fill("#rule-steps", "按审计步骤执行。");
  await page.fill("#rule-ai", "引用验收项原文");
  await page.fill("#rule-evidence", "审计日志");
  await page.getByRole("button", { name: "保存规则版本" }).click();
  const ruleRow = page.locator(".rule-row").filter({ hasText: "审计规则" });
  await expect(ruleRow).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await ruleRow.getByRole("button", { name: "导出" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain("审计规则");
  await expect(page.locator(".toast")).toContainText("规则已导出");
  await ruleRow.getByRole("button", { name: "绑定" }).click();
  await expect(page.locator("#settings-rules")).toContainText("当前绑定：审计规则 v3");

  await page
    .locator('input[type="file"][accept=".md,.txt"]')
    .setInputFiles({
      name: "导入规则.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("导入规则步骤"),
    });
  await expect(page.locator("#rule-name")).toHaveValue("导入规则");
  await page.getByRole("button", { name: "保存规则版本" }).click();
  await expect(page.locator(".rule-row").filter({ hasText: "导入规则" })).toBeVisible();

  await page.locator(".settings-index").getByRole("tab", { name: "环境" }).click();
  await page.getByRole("button", { name: "重新检查" }).click();
  await expect(page.locator(".env-list .env-row").first()).toBeVisible();

  await page.locator(".settings-index").getByRole("tab", { name: "外观" }).click();
  await page.getByRole("button", { name: "深色" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "浅色" }).click();
  await page.getByRole("button", { name: "跟随系统" }).click();

  await page.locator(".settings-index").getByRole("tab", { name: "备份" }).click();
  await page.getByRole("button", { name: "清空本地数据" }).click();
  await page.locator(".dialog-confirm").click();
  await page.waitForURL(/\/setup/);
  await expect(page.locator("h1").first()).toContainText("建立项目");

  await page.goto("/does-not-exist");
  await expect(page.locator("h1").first()).toContainText("页面不存在");
});


test("审计：运行停止与快速检查重跑", async ({ page }) => {
  const projectId = await createProject(page, "交互审计运行", "D:/work/audit-run");
  await page.getByRole("button", { name: "开始测试" }).click();
  await page.locator(".test-mode").filter({ hasText: "按范围测试" }).click();
  await page.getByRole("button", { name: "选择范围" }).click();
  await page.waitForURL(/\/scope/);
  await page.getByRole("button", { name: "生成计划" }).click();
  await page.waitForURL(/\/plan/);
  await confirmPlan(page);
  await page.getByRole("button", { name: "开始测试" }).click();
  await page.waitForURL(/\/run/);
  await page.getByRole("button", { name: "停止测试" }).click();
  await page.locator(".dialog-confirm").click();
  await page.waitForURL(/\/result/, { timeout: 20_000 });
  await expect(page.locator(".result-verdict")).toContainText(/未完成|失败/);

  await page.goto(`/projects/${projectId}/quick-check`);
  await page.locator("#quick-confirm").check();
  await page.getByRole("button", { name: "开始快速检查" }).click();
  await expect(page.locator(".quick-result")).toContainText("完成于", { timeout: 10_000 });
  await page.locator("#quick-confirm").check();
  await page.getByRole("button", { name: "再检查一次" }).click();
  await expect(page.locator(".quick-result")).toContainText("完成于", { timeout: 10_000 });
});

test("审计：报告导出、链路导入与断言", async ({ page }) => {
  await createAndRun(page, "交互审计链路", "D:/work/audit-link");
  await page.locator(".report-strip").getByRole("button", { name: "查看报告" }).click();
  await page.waitForURL(/\/reports\//);

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 Markdown" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain("测试报告");
  await page.locator("summary").filter({ hasText: "Markdown 预览" }).click();
  await expect(page.locator("textarea.markdown-preview")).toBeVisible();

  await page.locator(".report-details > summary").click();
  await page.locator(".link-tools > summary").filter({ hasText: "导入链路 JSON" }).click();
  await page.fill(
    "#link-json",
    JSON.stringify({
      nodes: [
        { id: "request", name: "请求到达", state: "passed", detail: "HTTP 200" },
        { id: "db", name: "写入数据库", state: "failed", detail: "唯一键冲突" },
      ],
      gaps: [{ node: "queue", reason: "没有队列日志" }],
      location: { known: true, path: "app/orders.py", line: 42, reason: "异常位置" },
    }),
  );
  await page.getByRole("button", { name: "导入并重新生成报告" }).click();
  await page.waitForURL(/\/reports\//);
  await expect(page.locator(".chain")).toContainText("请求到达");
  await expect(page.locator(".chain")).toContainText("写入数据库");

  await page.locator(".link-tools > summary").filter({ hasText: "对节点补充断言" }).click();
  await page.selectOption("#link-node", "db");
  await page.fill("#link-expected", "unique constraint");
  await page.fill("#link-actual", "duplicate key");
  await page.getByRole("button", { name: "写入断言并重新生成报告" }).click();
  await page.waitForURL(/\/reports\//);
});