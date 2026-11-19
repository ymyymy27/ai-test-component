import { expect } from "@playwright/test";

import { createAndRun, createProject, createTaskFromCandidate, test } from "./fixtures";

test("跳转链接、aria-current 和主内容焦点可用", async ({ page }) => {
  const projectId = await createAndRun(page, "无障碍验证", "D:/work/flow-a11y");
  await page.goto(`/projects/${projectId}/overview`);

  const skip = page.locator(".skip-link");
  await skip.focus();
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();

  const currentProject = page.locator('.side-project-row[aria-current="page"]');
  await expect(currentProject).toContainText("无障碍验证");
  await expect(page.locator(".breadcrumb-current")).toContainText("项目概览");
});

test("确认对话框 Esc 关闭并返回触发按钮", async ({ page }) => {
  const projectId = await createAndRun(page, "对话框验证", "D:/work/flow-dialog");
  await page.goto(`/projects/${projectId}/settings`);
  const trigger = page.getByRole("button", { name: "清空本地数据" });
  await trigger.click();
  const dialog = page.locator("#confirm-dialog-title");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("运行中概览的新建任务按钮被禁用", async ({ page }) => {
  const projectId = await createProject(page, "运行锁验证", "D:/work/flow-lock");
  await createTaskFromCandidate(page, 0);
  await page.getByRole("button", { name: "全选全部" }).click();
  await page.getByRole("button", { name: "生成计划" }).click();
  await page.waitForURL(/\/plan/);
  await page.locator("#confirm-execution").check();
  await page.getByRole("button", { name: "开始测试" }).click();
  await page.waitForURL(/\/run/);
  await page.locator(".side-project-row").first().click();
  await page.waitForURL(new RegExp(`/projects/${projectId}/overview`));
  await expect(page.getByRole("button", { name: "新建任务" })).toBeDisabled();
});

test("键盘可以完成建立项目到运行的主流程", async ({ page }) => {
  await page.goto("/setup");
  await page.locator("#setup-root").focus();
  await page.keyboard.type("D:/work/flow-keyboard");
  await page.keyboard.press("Tab");
  await page.keyboard.type("键盘验证");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/overview/, { timeout: 10_000 });
  await page.locator(".candidate-brief").waitFor({ timeout: 10_000 });

  await page.getByRole("button", { name: "查看候选" }).focus();
  await page.keyboard.press("Enter");
  await page.waitForURL(/tasks\/new/);
  await page.locator('input[type="radio"][name="target-candidate"]').first().focus();
  await page.keyboard.press("Space");
  await page.getByRole("button", { name: "用选中候选创建任务" }).focus();
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/scope/);

  await page.getByRole("button", { name: "生成计划" }).focus();
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/plan/);
  await page.locator("#confirm-execution").focus();
  await page.keyboard.press("Space");
  if ((await page.locator("#confirm-warnings").count()) > 0) {
    await page.locator("#confirm-warnings").focus();
    await page.keyboard.press("Space");
  }
  await page.getByRole("button", { name: "开始测试" }).focus();
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/result/, { timeout: 15_000 });
  await expect(page.locator(".result-verdict")).toHaveText("通过");
});
