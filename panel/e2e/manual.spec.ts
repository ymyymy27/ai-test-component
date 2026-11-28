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
  await expect(page.getByRole("tab", { name: /验收报告/ })).toContainText("v2");
  await expect(manualCard).toContainText("通过");

  await page.getByRole("button", { name: "+ 补充人工证据" }).click();
  await expect(page.locator("#evidence-dialog-title")).toBeVisible();
  await page.fill("#evidence-note", "补充运行日志");
  await page.setInputFiles("#evidence-files", {
    name: "run.log",
    mimeType: "text/plain",
    buffer: Buffer.from("evidence body"),
  });
  await page.getByRole("button", { name: "登记证据" }).click();
  await expect(page.locator(".toast")).toContainText("报告已更新");
  await expect(page.getByRole("tab", { name: /验收报告/ })).toContainText("v3");

  await page.getByRole("button", { name: "独立打开报告" }).click();
  await page.waitForURL(/\/reports\//);
  await expect(page.locator("body")).toContainText("run.log");
  await expect(page.locator("body")).toContainText("补充运行日志");
  await expect(page.locator("body")).toContainText("人工确认错误提示可读");
});

test("任务资料可以保存交付说明", async ({ page }) => {
  const projectId = await createProject(page, "资料验证", "D:/work/flow-details");
  await createTaskFromCandidate(page, 0);
  const taskId = page.url().match(/tasks\/([^/]+)/)?.[1] ?? "";
  await page.goto(`/projects/${projectId}/tasks/${taskId}/details`);
  await page.waitForURL(/\/details/);

  await page.fill("#delivery-commands", "pnpm test");
  await page.fill("#delivery-commit", "abc123");
  await page.getByRole("button", { name: "保存交付说明" }).click();
  await expect(page.locator(".toast")).toContainText("交付说明已保存");
  await expect(page.locator("#delivery-commit")).toHaveValue("abc123");

  await page.fill("#task-title", "资料验证-改名");
  await page.getByRole("button", { name: "保存基本信息" }).click();
  await expect(page.locator(".page-title")).toContainText("资料验证-改名");
});
