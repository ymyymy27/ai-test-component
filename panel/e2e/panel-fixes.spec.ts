import { expect } from "@playwright/test";

import { createAndRun, test } from "./fixtures";

test("运行记录显示任务数与 S/M，且状态文字不依赖图标", async ({ page }) => {
  await createAndRun(page, "记录修复", "D:/work/panel-fixes-history");
  const projectId = page.url().match(/projects\/([^/]+)/)?.[1];
  if (!projectId) {
    throw new Error("project id missing");
  }
  await page.goto(`/projects/${projectId}/history`);
  const taskSelect = page.getByLabel("按任务筛选历史");
  await expect(taskSelect).toContainText("全部任务（1 个）");
  const row = page.locator(".run-row").first();
  await expect(row).toContainText("/");
  await expect(row).toContainText("通过");
  await expect(row.locator(".status-text")).toHaveText("通过");
  await expect(row.locator(".status-mark .visually-hidden")).toHaveText("通过");
});

test("侧栏搜索保留当前项目并支持任务检索和清除", async ({ page }) => {
  await createAndRun(page, "侧栏修复", "D:/work/panel-fixes-sidebar");
  const taskTitle = (await page.locator("h1").first().textContent())?.trim() ?? "";
  const search = page.getByPlaceholder("搜索项目、路径或任务");
  await search.fill("完全不匹配的关键字");
  await expect(page.locator(".side-project-name")).toContainText("侧栏修复");
  await expect(page.getByRole("button", { name: "清除搜索" })).toBeVisible();
  await search.fill(taskTitle.slice(0, 2));
  await expect(page.locator(".side-task-title").first()).toBeVisible();
  await page.getByRole("button", { name: "清除搜索" }).click();
  await expect(search).toHaveValue("");
});

test("快速检查页只保留一处“不进入覆盖”的完整说明", async ({ page }) => {
  await createAndRun(page, "快速检查修复", "D:/work/panel-fixes-quick");
  const projectId = page.url().match(/projects\/([^/]+)/)?.[1];
  if (!projectId) {
    throw new Error("project id missing");
  }
  await page.goto(`/projects/${projectId}/quick-check`);
  const main = page.locator("main");
  await expect(main.getByText("不进入验收项覆盖")).toHaveCount(1);
  await expect(main.locator("section.quick-result[role='status']")).toHaveCount(1);
});
