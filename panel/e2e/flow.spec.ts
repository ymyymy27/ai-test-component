import { expect } from "@playwright/test";

import { confirmPlan, createProject, createTaskFromCandidate, test } from "./fixtures";

test("候选目标可以按预计范围直接生成计划", async ({ page }) => {
  const projectId = await createProject(page, "自动计划验证", "D:/work/flow-auto");
  await page.getByRole("button", { name: "查看候选" }).click();
  await page.waitForURL(/tasks\/new/);
  await page.locator(".candidate").first().click();
  await page.getByRole("button", { name: "按预计范围生成计划" }).click();
  await page.waitForURL(new RegExp(`/projects/${projectId}/tasks/[^/]+/plan`), {
    timeout: 15000,
  });
  await expect(page.locator(".plan-summary")).toContainText("待执行");
});

test("调整范围后取消调整返回计划页", async ({ page }) => {
  const projectId = await createProject(page, "取消调整验证", "D:/work/flow-cancel");
  await createTaskFromCandidate(page, 0);
  await page.getByRole("button", { name: "生成计划" }).click();
  await page.waitForURL(new RegExp(`/projects/${projectId}/tasks/[^/]+/plan`));
  await page.getByRole("button", { name: "调整范围" }).click();
  await page.waitForURL(new RegExp(`/projects/${projectId}/tasks/[^/]+/scope`));
  await page.getByRole("button", { name: "取消调整", exact: true }).click();
  await page.waitForURL(new RegExp(`/projects/${projectId}/tasks/[^/]+/plan`));
  await expect(page.locator(".plan-summary")).toBeVisible();
});

test("侧边栏可以新建任务并打开任务资料", async ({ page }) => {
  const projectId = await createProject(page, "侧边栏验证", "D:/work/flow-sidebar");
  await createTaskFromCandidate(page, 0);
  await page.goto(`/projects/${projectId}/overview`);
  await page.locator(".side-nav").getByRole("button", { name: /^问题/ }).click();
  await page.waitForURL(/\/issues$/);
  await page.locator(".side-nav").getByRole("button", { name: /^记录/ }).click();
  await page.waitForURL(/\/history$/);
  await page.locator(".side-nav").getByRole("button", { name: "设置" }).click();
  await page.waitForURL(/\/settings$/);
  await page.goto(`/projects/${projectId}/overview`);
  await page.locator(".side-task-more").first().click();
  await page.getByRole("menuitem", { name: "任务资料" }).click();
  await page.waitForURL(/\/details/);
  await page.goto(`/projects/${projectId}/overview`);
  await page.locator(".side-add").click();
  await page.waitForURL(/\/tasks\/new/);
});

test("建立项目 → 目标 → 范围 → 计划 → 确认 → 运行 → 结果 → 报告", async ({ page }) => {
  const projectId = await createProject(page, "流程验证", "D:/work/flow-a");
  await expect(page.locator(".stats-bar")).toContainText("业务功能");
  await expect(page.locator(".candidate-brief")).toContainText("已整理 3 条候选");

  await createTaskFromCandidate(page, 0);
  await expect(page.locator(".scope-summary")).toContainText("本次 1 / 当前理解 4 项");
  await expect(page.locator(".scope-unselected")).toContainText("未选 3 项");

  await page.getByRole("button", { name: "生成计划" }).click();
  await page.waitForURL(new RegExp(`/projects/${projectId}/tasks/[^/]+/plan`));
  await expect(page.locator(".plan-summary")).toContainText("本次 1 项 / 共 4 项");
  const start = page.getByRole("button", { name: "开始测试" });
  await expect(start).toBeDisabled();
  await confirmPlan(page);
  await expect(start).toBeEnabled();
  await start.click();

  await page.waitForURL(/\/result/, { timeout: 15_000 });
  await expect(page.locator(".result-verdict")).toHaveText("通过");
  await expect(page.locator(".guidance").first()).toContainText("本次范围通过");
  await expect(page.locator(".guidance").first()).not.toContainText("全部通过");
  await expect(page.locator(".section-title").filter({ hasText: "项目级检查" })).toBeVisible();

  await page.locator(".report-strip").getByRole("button", { name: "查看报告" }).click();
  await page.waitForURL(/\/reports\//);
  await expect(page.locator("h1").first()).toHaveText("测试报告");
  await expect(page.getByRole("button", { name: "返回运行结果" })).toBeVisible();
  await expect(page.locator(".result-verdict")).toHaveText("通过");
  await expect(page.getByRole("heading", { name: "范围与覆盖" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "项目级检查" })).toBeVisible();

  await page.locator("#opinion-text").fill("本次范围通过，可以进入下一轮。");
  await page.getByRole("button", { name: "保存意见" }).click();
  await expect(page.locator(".notice").first()).toContainText("本次范围通过");

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 Markdown" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain("测试报告");
  await expect(page.locator("text=导出结果")).toBeVisible();
  await expect(page.locator(".markdown-preview")).toHaveValue(/测试报告/);
});

test("概览不出现目标输入，当前任务条只在有任务时出现", async ({ page }) => {
  const projectId = await createProject(page, "概览验证", "D:/work/flow-overview");
  await expect(page.locator("#testGoalInput")).toHaveCount(0);
  await expect(page.locator(".current-task-strip")).toHaveCount(0);
  await createTaskFromCandidate(page, 0);
  await page.goto(`/projects/${projectId}/overview`);
  await expect(page.locator(".current-task-strip")).toContainText("验证 概览验证");
});

test("项目理解可以忽略和恢复业务功能", async ({ page }) => {
  await createProject(page, "理解验证", "D:/work/flow-map");
  await expect(page.locator(".project-map-stats")).toContainText("3 个业务功能");
  await page.getByRole("button", { name: "忽略业务功能 启动与主流程" }).click();
  await expect(page.locator(".project-map-stats")).toContainText("2 个业务功能");
  const ignored = page.locator(".project-map-details").filter({ hasText: "已忽略 1 个" });
  await expect(ignored).toBeVisible();
  await ignored.locator("summary").click();
  await ignored.getByRole("button", { name: "恢复" }).click();
  await expect(page.locator(".project-map-stats")).toContainText("3 个业务功能");
});
