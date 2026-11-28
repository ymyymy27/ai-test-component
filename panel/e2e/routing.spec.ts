import { expect } from "@playwright/test";

import { createAndRun, test } from "./fixtures";

test("直接刷新项目、任务、问题、历史、设置和报告路由都不 404", async ({ page }) => {
  const projectId = await createAndRun(page, "路由验证", "D:/work/flow-routes");
  const taskId = page.url().match(/tasks\/([^/]+)\/result/)?.[1];
  if (!taskId) {
    throw new Error("task id missing");
  }
  await page.getByRole("button", { name: "独立打开报告" }).click();
  await page.waitForURL(/\/reports\//);
  const reportId = page.url().match(/reports\/([^/]+)/)?.[1];
  if (!reportId) {
    throw new Error("report id missing");
  }

  const redirectedRoutes = [
    `/projects/${projectId}/tasks/${taskId}/scope`,
    `/projects/${projectId}/tasks/${taskId}/plan`,
    `/projects/${projectId}/tasks/${taskId}/run`,
  ];
  // 运行结束后阶段守卫会把这些路由送到结果页；这里验证的是“守卫生效且不 404”，
  // 不再把它们当成三页各自可刷新的覆盖。
  for (const route of redirectedRoutes) {
    await page.goto(route);
    await expect(page).toHaveURL(/\/result$/);
    await expect(page.locator("h1").first()).toBeVisible();
  }

  const routes = [
    `/projects/${projectId}/overview`,
    `/projects/${projectId}/tasks/new`,
    `/projects/${projectId}/tasks/${taskId}/result`,
    `/projects/${projectId}/tasks/${taskId}/details`,
    `/projects/${projectId}/quick-check`,
    `/projects/${projectId}/issues`,
    `/projects/${projectId}/history`,
    `/projects/${projectId}/settings`,
    `/projects/${projectId}/reports/${reportId}`,
  ];

  for (const route of routes) {
    await page.goto(route);
    await expect(page.locator("body")).not.toContainText("页面不存在");
    await expect(page.locator("h1").first()).toBeVisible();
  }
});

test("未知路由进入 404 页面并保留原地址", async ({ page }) => {
  await page.goto("/projects/does-not-exist/overview");
  await expect(page.locator("body")).toContainText("页面不存在");
  await expect(page.getByRole("link", { name: "建立项目" })).toBeVisible();
  await expect(page).toHaveURL(/projects\/does-not-exist\/overview/);

  await page.goto("/this/route/does/not/exist?filter=open&x=1#note");
  await expect(page.locator("body")).toContainText("页面不存在");
  await expect(page).toHaveURL(/this\/route\/does\/not\/exist\?filter=open&x=1#note/);
});
