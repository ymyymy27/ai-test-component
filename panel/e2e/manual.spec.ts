import { expect } from "@playwright/test";

import { createProject, createTaskFromCandidate, startRun, test } from "./fixtures";

test("记录人工结果并补充证据会更新报告版本", async ({ page }) => {
  await createProject(page, "人工验证", "D:/work/flow-manual");
  await createTaskFromCandidate(page, 2);
  await startRun(page);

  await expect(page.locator(".result-verdict")).toHaveText("失败");
  const manualCard = page.locator(".case-block").filter({ hasText: "人工确认错误提示可读" });
  await expect(manualCard).toContainText("待人工");
  await manualCard.getByRole("button", { name: "记录人工结果" }).click();

  await expect(page.locator("#manual-dialog-title")).toBeVisible();
  await page.getByRole("radio", { name: "通过" }).check();
  await page.fill("#manual-note", "人工确认错误提示可读");
  await page.setInputFiles("#manual-files", {
    name: "manual-note.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("manual evidence"),
  });
  await page.getByRole("button", { name: "保存人工结果" }).click();
  await expect(page.locator(".toast")).toContainText("人工结果已记录");
  await expect(page.locator(".report-strip-title")).toContainText("第 2 版");
  await expect(manualCard).toContainText("通过");

  await page.getByRole("button", { name: "补充证据" }).click();
  await expect(page.locator("#evidence-dialog-title")).toBeVisible();
  await page.fill("#evidence-note", "补充运行日志");
  await page.setInputFiles("#evidence-files", {
    name: "run.log",
    mimeType: "text/plain",
    buffer: Buffer.from("evidence body"),
  });
  await page.getByRole("button", { name: "登记证据" }).click();
  await expect(page.locator(".toast")).toContainText("报告已更新");
  await expect(page.locator(".report-strip-title")).toContainText("第 3 版");

  await page.locator(".report-strip").getByRole("button", { name: "查看报告" }).click();
  await page.waitForURL(/\/reports\//);
  await expect(page.locator("body")).toContainText("run.log");
  await expect(page.locator("body")).toContainText("补充运行日志");
  await expect(page.locator("body")).toContainText("人工确认错误提示可读");
});

test("任务资料可以保存交付说明并应用检查模板", async ({ page }) => {
  await createProject(page, "资料验证", "D:/work/flow-details");
  await createTaskFromCandidate(page, 0);
  await page.getByRole("button", { name: "任务资料" }).click();
  await page.waitForURL(/\/details/);

  await page.fill("#delivery-commands", "pnpm test");
  await page.fill("#delivery-commit", "abc123");
  await page.getByRole("button", { name: "保存交付说明" }).click();
  await expect(page.locator(".toast")).toContainText("交付说明已保存");
  await expect(page.locator("#delivery-commit")).toHaveValue("abc123");

  await page
    .locator(".template-card")
    .filter({ hasText: "Python 服务" })
    .getByRole("button", { name: "应用" })
    .click();
  await expect(page.locator(".toast")).toContainText("检查模板已应用");
  await expect(page.locator(".template-results")).toContainText("适用");

  await page.fill("#task-title", "资料验证-改名");
  await page.getByRole("button", { name: "保存基本信息" }).click();
  await expect(page.locator(".page-title")).toContainText("资料验证-改名");
});
