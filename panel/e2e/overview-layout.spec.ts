import { expect } from "@playwright/test";

import { createProject, test } from "./fixtures";

test("概览页两栏互换且侧栏入口为纵向列表", async ({ page }) => {
  await createProject(page, "概览布局验证", "D:/work/overview-layout");

  const grid = page.locator(".overview-grid");
  const columns = grid.locator(":scope > *");
  await expect(columns).toHaveCount(2);
  await expect(columns.first()).toHaveClass(/overview-side/);
  await expect(columns.last()).toHaveClass(/overview-feed/);
  await expect(grid.locator(".overview-side .project-map")).toBeVisible();
  await expect(grid.locator(".overview-feed")).toContainText("未关闭问题");
  await expect(grid.locator(".overview-feed")).toContainText("最近活动");

  const nav = page.locator(".side-nav");
  await expect(nav.getByRole("button")).toHaveCount(5);
  await expect(page.locator(".side-task-toggle")).toHaveCount(0);
  await expect(nav).toContainText("概览");
  await expect(nav).toContainText("问题");
  await expect(nav).toContainText("运行记录");
  await expect(nav).toContainText("快速检查");
  await expect(nav).toContainText("设置");
  await expect(nav.locator("button svg")).toHaveCount(5);
  await expect(nav.getByRole("button", { name: /概览/ })).toHaveClass(/is-active/);

  const display = await nav.evaluate((element) => getComputedStyle(element).display);
  expect(display).toBe("flex");
  const direction = await nav.evaluate(
    (element) => getComputedStyle(element).flexDirection,
  );
  expect(direction).toBe("column");
});

test("单个项目可以整体折叠并在刷新后保持", async ({ page }) => {
  const projectId = await createProject(page, "项目折叠验证", "D:/work/project-collapse");

  const row = page.locator(".side-project-row");
  const body = page.locator(`#project-body-${projectId}`);
  await expect(body).toBeVisible();
  await row.click();
  await expect(body).toBeHidden();
  await expect(row).toHaveAttribute("aria-expanded", "false");

  await page.reload();
  await expect(page.locator(`#project-body-${projectId}`)).toBeHidden();
  await page.locator(".side-project-row").click();
  await expect(page.locator(`#project-body-${projectId}`)).toBeVisible();
});
