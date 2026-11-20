import { expect } from "@playwright/test";

import { createProject, test } from "./fixtures";

test("快速检查只做项目级 L1，不建任务、不进入验收项覆盖", async ({ page }) => {
  await createProject(page, "快速检查验证", "D:/work/flow-quick");
  await page.getByRole("button", { name: "快速检查", exact: true }).click();
  await page.waitForURL(/quick-check/);

  await expect(page.locator("body")).toContainText("不进入验收项覆盖");
  await expect(page.locator(".check-row")).toHaveCount(3);
  await expect(page.locator(".check-list")).toContainText("构建");
  await expect(page.locator(".check-list")).toContainText("启动入口");
  await expect(page.locator(".check-list")).toContainText("最小测试");
  await expect(page.locator("body")).not.toContainText("验收项 01");

  await page.locator("#quick-confirm").check();
  await page.getByRole("button", { name: "开始快速检查" }).click();
  await expect(page.locator(".quick-result")).toContainText("完成于", { timeout: 5000 });
  await expect(page.locator(".quick-result")).toContainText("通过");

  await page.goto(page.url().replace(/\/quick-check$/, "/overview"));
  await expect(page.locator(".stat").first()).toContainText("0");
  await expect(page.locator(".stat").first()).toContainText("任务");

  await page.goto(page.url().replace(/\/overview$/, "/quick-check"));
  await expect(page.locator(".quick-result")).toContainText("完成于");
  await expect(page.getByRole("button", { name: "再检查一次" })).toBeVisible();
});
