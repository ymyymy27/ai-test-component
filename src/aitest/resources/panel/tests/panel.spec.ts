import { test, expect } from "@playwright/test";
import { resolve } from "node:path";

test("prototype follows current-test panel and same-panel detail flow", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 360, height: 720 });
  await page.setContent('<html lang="zh-CN"><body><ai-test-panel></ai-test-panel></body></html>');
  await page.addScriptTag({ path: resolve("dist/index.js") });

  await expect(page.getByTestId("panel-status")).toContainText("未连接核心");
  await expect(page.getByTestId("phase-summary").getByRole("heading")).toHaveCount(3);
  await expect(page.getByText("运行前", { exact: true })).toBeVisible();
  await expect(page.getByText("运行时", { exact: true })).toBeVisible();
  await expect(page.getByText("运行后", { exact: true })).toBeVisible();
  await expect(page.getByTestId("primary-actions").getByRole("button")).toHaveCount(3);

  await page.getByRole("button", { name: "计划" }).click();
  await expect(page.getByTestId("detail-view").getByRole("heading", { name: "计划" })).toBeVisible();
  await page.getByRole("button", { name: "返回本次测试" }).click();
  await expect(page.getByText("当前测试", { exact: true })).toBeVisible();

  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});