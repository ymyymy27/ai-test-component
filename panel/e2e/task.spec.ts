import { expect, type Page } from "@playwright/test";

import { createProject, createTaskFromCandidate, test } from "./fixtures";

async function openDetails(page: Page): Promise<string> {
  const projectId = await createProject(page, "任务资料验证", "D:/work/flow-task-details");
  await createTaskFromCandidate(page, 0);
  const match = page.url().match(/tasks\/([^/]+)\/plan/);
  if (!match?.[1]) {
    throw new Error("task id not found");
  }
  const taskId = match[1];
  await page.goto(`/projects/${projectId}/tasks/${taskId}/details`);
  return taskId;
}

test("保存验收项不会重置其他区块的未保存编辑", async ({ page }) => {
  await openDetails(page);
  await page.fill("#task-title", "尚未保存的标题");
  const item = page.locator('input[aria-label="验收项文本"]').first();
  await item.fill(`${await item.inputValue()}（补充）`);
  await page.getByRole("button", { name: "保存验收项" }).click();
  await expect(page.locator(".toast")).toContainText("验收项已保存");
  await expect(page.locator("#task-title")).toHaveValue("尚未保存的标题");
});

test("计划过期后可以从资料页重新进入范围选择", async ({ page }) => {
  await openDetails(page);
  const item = page.locator('input[aria-label="验收项文本"]').first();
  await item.fill(`${await item.inputValue()}（补充）`);
  await page.getByRole("button", { name: "保存验收项" }).click();
  await expect(page.locator(".notice.is-warn")).toBeVisible();
  await page.getByRole("button", { name: "重新选范围" }).click();
  await page.waitForURL(/\/scope$/);
  await expect(page.locator(".scope-summary")).toBeVisible();
});

test("未保存的多个区块可以一次保存", async ({ page }) => {
  await openDetails(page);
  await page.fill("#task-title", "一次保存全部");
  await page.fill("#task-goal", "统一保存的验证目标");
  await expect(page.locator(".task-savebar")).toContainText("2 处未保存");
  await page.getByRole("button", { name: "保存全部修改" }).click();
  await expect(page.locator(".toast")).toContainText("任务资料已保存");
  await page.reload();
  await expect(page.locator("#task-title")).toHaveValue("一次保存全部");
  await expect(page.locator("#task-goal")).toHaveValue("统一保存的验证目标");
});

test("计划过期后可以在计划页原地重新生成", async ({ page }) => {
  await openDetails(page);
  const item = page.locator('input[aria-label="验收项文本"]').first();
  await item.fill(`${await item.inputValue()}（再补充）`);
  await page.getByRole("button", { name: "保存验收项" }).click();
  await expect(page.locator(".notice.is-warn")).toBeVisible();
  await page.goto(page.url().replace(/\/details$/, "/plan"));
  await expect(page.locator(".page-head .badge").first()).toHaveText("已过期");
  await page.getByRole("button", { name: "重新生成计划" }).click();
  await expect(page.locator(".page-head .badge").first()).toHaveText("待确认");
});
