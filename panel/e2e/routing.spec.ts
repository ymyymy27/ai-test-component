import { expect } from "@playwright/test";

import { createAndRun, test } from "./fixtures";

test("直接刷新项目、任务、问题、历史、设置和报告路由都不 404", async ({ page }) => {
  const projectId = await createAndRun(page, "路由验证", "D:/work/flow-routes");
  const taskId = page.url().match(/tasks\/([^/]+)\/result/)?.[1];
  if (!taskId) {
    throw new Error("task id missing");
  }
  await page.locator(".report-strip").getByRole("button", { name: "查看报告" }).click();
  await page.waitForURL(/\/reports\//);
  const reportId = page.url().match(/reports\/([^/]+)/)?.[1];
  if (!reportId) {
    throw new Error("report id missing");
  }

  const routes = [
    `/projects/${projectId}/overview`,
    `/projects/${projectId}/tasks/new`,
    `/projects/${projectId}/tasks/${taskId}/scope`,
    `/projects/${projectId}/tasks/${taskId}/plan`,
    `/projects/${projectId}/tasks/${taskId}/run`,
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

test("未知路由进入 404 页面", async ({ page }) => {
  await page.goto("/projects/does-not-exist/overview");
  await expect(page.locator("body")).toContainText("页面不存在");
  await expect(page.getByRole("link", { name: "项目入口" })).toBeVisible();

  await page.goto("/this/route/does/not/exist");
  await expect(page.locator("body")).toContainText("页面不存在");
});
