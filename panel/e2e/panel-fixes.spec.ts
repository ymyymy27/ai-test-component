import { expect } from "@playwright/test";

import { createAndRun, createTaskFromCandidate, test } from "./fixtures";

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
  const taskTitle = (await page.locator(".side-task-title").first().textContent())?.trim() ?? "";
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

test("历史 ?run= 初次直接打开详情，后退时同步关闭", async ({ page }) => {
  const projectId = await createAndRun(page, "历史深链修复", "D:/work/panel-fixes-history-deep");
  await page.goto(`/projects/${projectId}/history`);
  await page.locator(".run-row").first().click();
  await expect(page.locator("#run-dialog-title")).toBeVisible();
  const deepLink = page.url();
  expect(deepLink).toContain("run=");

  await page.goto(deepLink);
  await expect(page.locator("#run-dialog-title")).toBeVisible({ timeout: 10_000 });

  await page.goBack();
  await expect(page.locator("#run-dialog-title")).toBeHidden();
});

test("任务资料在 taskId 变化时重填，不把任务 A 的草稿带到任务 B", async ({ page }) => {
  const projectId = await createAndRun(page, "任务资料切换", "D:/work/panel-fixes-task-details");
  const task1Id = page.url().match(/tasks\/([^/]+)/)?.[1];
  const task1Title = (await page.locator(".side-task-title").first().textContent())?.trim() ?? "";
  if (!task1Id) {
    throw new Error("task id missing");
  }

  await page.goto(`/projects/${projectId}/overview`);
  await createTaskFromCandidate(page, 1);
  const task2Id = page.url().match(/tasks\/([^/]+)/)?.[1];
  if (!task2Id || task2Id === task1Id) {
    throw new Error("second task missing");
  }

  await page.goto(`/projects/${projectId}/tasks/${task2Id}/details`);
  await expect(page.locator("#task-title")).not.toHaveValue(task1Title);

  // 同一路由组件内切到任务 A 的详情：必须重新填入任务 A 的标题。
  await page
    .locator(".side-task-row")
    .filter({ hasText: task1Title })
    .locator(".side-task-more")
    .click();
  await page.getByRole("menuitem", { name: "编辑资料" }).click();
  await page.waitForURL(new RegExp(`/tasks/${task1Id}/details`));
  await expect(page.locator("#task-title")).toHaveValue(task1Title);
});

test("环境浮层 Esc 关闭并把焦点还给触发按钮", async ({ page }) => {
  const projectId = await createAndRun(page, "环境浮层修复", "D:/work/panel-fixes-env");
  await page.goto(`/projects/${projectId}/overview`);
  const envButton = page.getByRole("button", { name: "查看环境自检" });
  await envButton.click();
  await expect(page.locator("#env-popover")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator("#env-popover")).toBeHidden();
  await expect(envButton).toBeFocused();
});

test("设置、任务资料、问题、历史与结果页在 320/768/1200 下都不横向滚动", async ({ page }) => {
  const projectId = await createAndRun(page, "全页响应式", "D:/work/panel-fixes-responsive");
  const taskId = page.url().match(/tasks\/([^/]+)/)?.[1];
  if (!taskId) {
    throw new Error("task id missing");
  }
  const urls = [
    `/projects/${projectId}/overview`,
    `/projects/${projectId}/settings`,
    `/projects/${projectId}/tasks/${taskId}/details`,
    `/projects/${projectId}/tasks/${taskId}/plan`,
    `/projects/${projectId}/tasks/${taskId}/result`,
    `/projects/${projectId}/issues`,
    `/projects/${projectId}/history`,
  ];
  for (const width of [320, 768, 1200]) {
    await page.setViewportSize({ width, height: 900 });
    for (const url of urls) {
      await page.goto(url);
      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      expect(scrollWidth, `${url} 在 ${width}px 不应横向滚动`).toBeLessThanOrEqual(width);
    }
  }
});

test("暗色、减少动态与高对比度媒体设置下页面仍可用", async ({ page }) => {
  const projectId = await createAndRun(page, "媒体设置", "D:/work/panel-fixes-media");
  await page.goto(`/projects/${projectId}/overview`);

  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.emulateMedia({ reducedMotion: "reduce" });
  const reduced = await page.evaluate(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  expect(reduced).toBe(true);

  await page.emulateMedia({ forcedColors: "active" });
  await expect(page.locator(".side-project-row").first()).toBeVisible();
});

test("任务菜单 Esc 返回触发按钮，窄屏表格容器可聚焦", async ({ page }) => {
  const projectId = await createAndRun(page, "焦点闭环", "D:/work/panel-fixes-focus-loop");
  await page.goto(`/projects/${projectId}/overview`);
  const more = page.locator(".side-task-more").first();
  await more.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".side-task-menu")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".side-task-menu")).toBeHidden();
  await expect(more).toBeFocused();

  await page.goto(`/projects/${projectId}/history`);
  await page.locator(".run-row").first().click();
  const tableWrap = page.locator(".table-wrap").first();
  await expect(tableWrap).toHaveAttribute("tabindex", "0");
  await tableWrap.focus();
  await expect(tableWrap).toBeFocused();
});
