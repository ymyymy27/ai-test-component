import { expect } from "@playwright/test";

import { confirmPlan, createProject, createTaskFromCandidate, test } from "./fixtures";

test("选中候选会填入目标并自动生成计划", async ({ page }) => {
  const projectId = await createProject(page, "候选创建验证", "D:/work/flow-auto");
  await page.getByRole("button", { name: "新建测试任务" }).click();
  const drawer = page.getByRole("dialog", { name: "新建测试" });
  await expect(drawer).toBeVisible();
  await drawer.getByRole("radio", { name: "粘贴需求 / 自选功能" }).click();
  await expect(drawer.locator("#composer-goal-req")).toHaveValue("");
  const details = drawer.locator("details.details-tech");
  await details.locator("summary").click();
  await details.locator("button").first().click();
  await expect(drawer.locator("#composer-goal-req")).toHaveValue(/验证/);
  await drawer.getByRole("button", { name: "生成测试计划" }).click();
  await page.waitForURL(new RegExp(`/projects/${projectId}/tasks/[^/]+/plan`), {
    timeout: 15_000,
  });
  await expect(page.locator(".page-head")).toContainText("待确认");
  // 不只等 URL：核对本地落盘的任务与计划确实生成了用例。
  const stored = (await page.evaluate(() =>
    JSON.parse(localStorage.getItem("aitest.panel.v1") || "{}"),
  )) as {
    projects?: Array<{
      id: string;
      tasks?: Array<{ plans?: Array<{ cases?: unknown[] }> }>;
    }>;
  };
  const project = stored.projects?.find((item) => item.id === projectId);
  expect(project?.tasks?.length).toBe(1);
  const plans = project?.tasks?.[0]?.plans ?? [];
  expect(plans.length).toBeGreaterThan(0);
  expect(plans[0].cases?.length ?? 0).toBeGreaterThan(0);
});

test("调整范围后取消调整返回计划页", async ({ page }) => {
  const projectId = await createProject(page, "取消调整验证", "D:/work/flow-cancel");
  await createTaskFromCandidate(page, 0);
  await page.getByRole("button", { name: "调整本次纳入范围" }).click();
  await page.waitForURL(new RegExp(`/projects/${projectId}/tasks/[^/]+/scope`));
  await page.getByRole("button", { name: "取消调整", exact: true }).click();
  await page.waitForURL(new RegExp(`/projects/${projectId}/tasks/[^/]+/plan`));
  await expect(page.getByRole("region", { name: "覆盖率与任务摘要" })).toBeVisible();
});

test("侧边栏可以重命名、编辑资料并新建任务", async ({ page }) => {
  const projectId = await createProject(page, "侧边栏验证", "D:/work/flow-sidebar");
  await createTaskFromCandidate(page, 0);
  await page.goto(`/projects/${projectId}/overview`);
  await page.locator(".side-nav").getByRole("button", { name: /^问题/ }).click();
  await page.waitForURL(/\/issues$/);
  await expect(page.locator("#main-content")).toContainText("问题清单");
  await page.locator(".side-nav").getByRole("button", { name: /^运行记录/ }).click();
  await page.waitForURL(/\/history$/);
  await expect(page.locator("#main-content")).toContainText("运行记录");
  await page.getByRole("button", { name: "项目设置" }).click();
  await page.waitForURL(/\/settings$/);
  await expect(page.locator("#main-content")).toContainText("项目资料");
  await page.goto(`/projects/${projectId}/overview`);
  await expect(page.locator(".side-task-body")).toBeVisible();
  const more = page.locator(".side-task-more").first();
  await more.click();
  await expect(page.getByRole("menuitem", { name: "重命名" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toBeHidden();
  await expect(more).toBeFocused();
  await more.click();
  await page.getByRole("menuitem", { name: "重命名" }).click();
  await page.fill(".side-task-rename input", "侧边栏改名任务");
  await page.press(".side-task-rename input", "Enter");
  await expect(page.locator(".side-task-title").first()).toHaveText("侧边栏改名任务");
  await page.locator(".side-task-more").first().click();
  await page.getByRole("menuitem", { name: "编辑资料" }).click();
  await page.waitForURL(/\/details/);
  await page.goto(`/projects/${projectId}/overview`);
  await page.locator(".side-add").click();
  await expect(page.getByRole("dialog", { name: "新建测试" })).toBeVisible();
});

test("删除任务需要确认并从侧栏移除", async ({ page }) => {
  const projectId = await createProject(page, "删除验证", "D:/work/flow-delete");
  await createTaskFromCandidate(page, 0);
  await page.goto(`/projects/${projectId}/overview`);
  await page.locator(".side-task-more").first().click();
  await page.getByRole("menuitem", { name: "删除" }).click();
  await expect(page.locator("#confirm-dialog-title")).toBeVisible();
  await expect(page.locator("#confirm-dialog-title")).toContainText("删除任务");
  await page.locator(".dialog-confirm").click();
  await expect(page.locator(".side-task-row")).toHaveCount(0);
  await expect(page.locator(".toast")).toContainText("任务已删除");
});

test("建立项目 → 目标 → 计划 → 确认 → 运行 → 结果 → 报告", async ({ page }) => {
  await createProject(page, "流程验证", "D:/work/flow-a");
  await expect(page.locator(".stats-bar")).toContainText("业务功能");

  await createTaskFromCandidate(page, 0);
  await expect(page.getByRole("region", { name: "覆盖率与任务摘要" })).toContainText("本次纳入");
  const start = page.getByRole("button", { name: "确认并开始测试" });
  await expect(start).toBeDisabled();
  await confirmPlan(page);
  await expect(start).toBeEnabled();
  await start.click();

  await page.waitForURL(/\/result/, { timeout: 15_000 });
  await expect(page.locator(".result-verdict")).toHaveText("通过");
  await expect(page.locator(".guidance").first()).toContainText("本次范围通过");
  await expect(page.locator(".guidance").first()).not.toContainText("全部通过");

  await page.getByRole("button", { name: "独立打开报告" }).click();
  await page.waitForURL(/\/reports\//);
  await expect(page.locator("h1").first()).toHaveText("测试报告");
  await expect(page.getByRole("button", { name: "返回运行结果" })).toBeVisible();
  await expect(page.locator(".report-hero-title")).toHaveText("本范围通过");
  await expect(page.getByRole("heading", { name: "范围与覆盖" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "项目级检查" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "分层计数" })).toHaveCount(0);

  await page.locator(".row-filter").first().click();
  await expect(page.locator(".case-filter")).toContainText("仅看");
  await expect(page.locator("#case-details .section-note")).toHaveText("1 / 1 条");
  await page.locator(".case-filter-clear").click();
  await expect(page.locator(".case-filter")).toHaveCount(0);

  await page.locator(".row-toggle").first().click();
  await expect(page.locator(".check-detail")).toContainText("本地模拟");

  await page.locator(".scope-note-btn").first().click();
  await expect(page.locator(".scope-note-panel")).toBeVisible();
  await page.locator(".scope-note-btn").first().click();
  await expect(page.locator(".scope-note-panel")).toHaveCount(0);

  await page.locator(".method-open").first().click();
  await expect(page.locator("dialog[open] .dialog-title")).toHaveText("测试命令");
  await expect(page.locator(".command-block")).toContainText("启动项目");
  await page.locator(".dialog-actions .btn").last().click();

  await page.getByRole("button", { name: "查看证据" }).first().click();
  await expect(page.locator("dialog[open] .dialog-title")).toHaveText("证据详情");
  await expect(page.locator("dialog[open]")).toContainText("可粘贴到问题记录");
  await page.locator(".dialog-actions .btn").last().click();

  await page.locator("#opinion-text").fill("本次范围通过，可以进入下一轮。");
  await page.getByRole("button", { name: "保存意见" }).click();
  await expect(page.locator(".opinion-saved-hint")).toContainText("上次保存");
  await expect(page.locator("#opinion-text")).toHaveValue("本次范围通过，可以进入下一轮。");

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
  await page.locator("summary").filter({ hasText: "功能模块" }).click();
  await expect(page.locator(".project-map-stats")).toContainText("3 个业务功能");
  await page.getByRole("button", { name: "忽略业务功能 启动与主流程" }).click();
  await expect(page.locator(".project-map-stats")).toContainText("2 个业务功能");
  const ignored = page.locator(".project-map-details").filter({ hasText: "已忽略 1 个" });
  await expect(ignored).toBeVisible();
  await ignored.getByRole("button", { name: "恢复" }).click();
  await expect(page.locator(".project-map-stats")).toContainText("3 个业务功能");
});
