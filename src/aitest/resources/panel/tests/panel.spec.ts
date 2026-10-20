import { test, expect } from "@playwright/test";
import { resolve } from "node:path";

const scriptPath = resolve("dist/index.js");

async function mount(page: import("@playwright/test").Page, view = "prepare"): Promise<void> {
  await page.addInitScript(() => {
    (window as Window & { __AITEST_DEMO__?: boolean }).__AITEST_DEMO__ = true;
  });
  await page.setContent(`<html lang="zh-CN"><body><aitest-mockup view="${view}"></aitest-mockup></body></html>`);
  await page.addScriptTag({ path: scriptPath });
}

test("prepare view matches the reference shell and sidebar hierarchy", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 1110 });
  await mount(page, "prepare");
  const shell = page.locator("aitest-mockup");
  await expect(shell).toContainText("交互草图 · 示例数据");
  await expect(shell).toContainText("工单状态修改");
  await expect(shell).toContainText("快速检查 · 计划驱动");
  await expect(shell).toContainText("查看准备详情");
  await expect(shell).toContainText("L1 加载");
  await expect(shell).toContainText("ticket_service.py");
  await expect(shell).toContainText("PS ticket-service> git status --short");
});

test("all reference detail states render their own headings and tabs", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 1110 });
  await mount(page, "ai-records");
  const shell = page.locator("aitest-mockup");
  const states: ReadonlyArray<readonly [string, string]> = [
    ["ai-records", "AI 生成记录"],
    ["driver", "驱动与运行修订"],
    ["environment", "环境与执行来源"],
    ["issue", "问题草稿 · IS-017"],
    ["records", "工单服务 · 报告"],
    ["regression", "变更与回归建议"],
    ["result", "覆盖与结论"],
    ["running", "核验最终持久化"],
  ];
  for (const [view, heading] of states) {
    await shell.evaluate((element, nextView) => { element.setAttribute("view", nextView); }, view);
    await expect(shell).toContainText(heading);
    await expect(shell).toContainText("AI 测试 › 工单服务");
  }
});

test("sidebar remains interactive and never emits a write bypass", async ({ page }) => {
  await page.addInitScript(() => {
    (window as Window & { __messages?: unknown[] }).__messages = [];
    (window as Window & { acquireVsCodeApi?: () => unknown }).acquireVsCodeApi = () => ({
      postMessage(message: unknown) { (window as Window & { __messages?: unknown[] }).__messages?.push(message); },
    });
  });
  await page.setContent('<html lang="zh-CN"><body><ai-test-panel demo state="prepare"></ai-test-panel></body></html>');
  await page.addScriptTag({ path: scriptPath });
  const panel = page.locator("ai-test-panel");
  await panel.getByRole("button", { name: "设置" }).click();
  await expect(panel).toContainText("模型 API 设置");
  await panel.getByRole("button", { name: /返回本次测试/ }).click();
  await expect(panel).toContainText("快速检查 · 计划驱动");
  const messages = await page.evaluate(() => (window as Window & { __messages?: unknown[] }).__messages ?? []);
  expect(messages).toContainEqual(expect.objectContaining({ action: "doctor" }));
  expect(messages.every((message) => (message as { action?: string }).action === "doctor")).toBe(true);
});