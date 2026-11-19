import { expect } from "@playwright/test";

import { confirmPlan, createProject, createTaskFromCandidate, startRun, test } from "./fixtures";

test("失败用例自动归并问题，重跑通过后关闭门禁生效", async ({ page }) => {
  const projectId = await createProject(page, "失败流程", "D:/work/flow-failure");
  await createTaskFromCandidate(page, 2);
  await startRun(page);

  await expect(page.locator(".result-verdict")).toHaveText("失败");
  await expect(page.locator(".case-message").first()).toContainText("断言失败");

  await page.goto(`/projects/${projectId}/issues`);
  await expect(page.locator(".issue-row")).toHaveCount(1);
  await page.locator(".issue-row").first().click();
  await expect(page.locator(".page-subtitle").first()).toContainText("待确认");

  await page.getByRole("button", { name: "更新状态" }).click();
  await page.locator("#issue-next-status").selectOption("in_progress");
  await page.locator("#issue-reason").fill("开始处理");
  await page.getByRole("button", { name: "保存状态" }).click();
  await expect(page.locator(".page-subtitle").first()).toContainText("处理中");

  await page.getByRole("button", { name: "更新状态" }).click();
  await page.locator("#issue-next-status").selectOption("retest");
  await page.locator("#issue-reason").fill("已修复，提交复测");
  await page.getByRole("button", { name: "保存状态" }).click();
  await expect(page.locator(".page-subtitle").first()).toContainText("待复测");

  await page.getByRole("button", { name: "去重跑这一项" }).click();
  await page.waitForURL(/\/plan/);
  await confirmPlan(page);
  await page.getByRole("button", { name: "开始测试" }).click();
  await page.waitForURL(/\/result/, { timeout: 15_000 });
  await expect(page.locator(".result-verdict")).toHaveText("未完成");

  await page.goto(`/projects/${projectId}/issues`);
  await expect(page.locator(".issue-row")).toHaveCount(1);
  await page.locator(".issue-row").first().click();
  await expect(page.locator("body")).toContainText("已找到包含该用例且通过的新运行");

  const closeButtons = page.getByRole("button", { name: "关闭问题" });
  await closeButtons.last().click();
  await page.locator("#confirm-dialog-text").fill("复测通过，关闭问题");
  await page.locator(".dialog-confirm").click();
  await expect(page.locator(".page-subtitle").first()).toContainText("已关闭");
  await expect(page.locator("body")).toContainText("关闭依据");
});

test("关闭门禁未满足时禁用并说明缺什么", async ({ page }) => {
  const projectId = await createProject(page, "门禁流程", "D:/work/flow-gate");
  await createTaskFromCandidate(page, 2);
  await startRun(page);
  await page.goto(`/projects/${projectId}/issues`);
  await page.locator(".issue-row").first().click();

  await page.getByRole("button", { name: "更新状态" }).click();
  await page.locator("#issue-next-status").selectOption("in_progress");
  await page.locator("#issue-reason").fill("开始处理");
  await page.getByRole("button", { name: "保存状态" }).click();
  await page.getByRole("button", { name: "更新状态" }).click();
  await page.locator("#issue-next-status").selectOption("retest");
  await page.locator("#issue-reason").fill("提交复测");
  await page.getByRole("button", { name: "保存状态" }).click();

  await expect(page.locator("body")).toContainText("还没有满足条件的新运行");
  await expect(page.getByRole("button", { name: "关闭问题" }).last()).toBeDisabled();
});
