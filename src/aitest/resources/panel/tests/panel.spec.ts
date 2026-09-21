import { test, expect } from "@playwright/test";
import { resolve } from "node:path";

test("built panel keeps scope/status visible and navigation works at narrow widths", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 360, height: 720 });
  await page.setContent('<html lang="zh-CN"><body><ai-test-panel></ai-test-panel></body></html>');
  await page.addScriptTag({ path: resolve("dist/index.js") });
  await expect(page.getByText("一期工程骨架 · 本地核心尚不可用 · 未进行产品验收")).toBeVisible();
  await expect(page.getByRole("button")).toHaveCount(5);
  await page.getByRole("button", { name: "项目与计划" }).click();
  await expect(page.getByRole("heading", { name: "项目与计划" })).toBeVisible();
  await expect(page.getByRole("button", { name: "项目与计划" })).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "工作台" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});
