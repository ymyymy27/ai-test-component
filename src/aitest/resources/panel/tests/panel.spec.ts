import { test, expect } from "@playwright/test";
import { resolve } from "node:path";

test("prototype follows current-test panel and same-panel detail flow", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 360, height: 720 });
  await page.setContent('<html lang="zh-CN"><body><ai-test-panel></ai-test-panel></body></html>');
  await page.addScriptTag({ path: resolve("dist/index.js") });

  await expect(page.getByTestId("panel-status")).toContainText("未连接核心");
  // 非宿主环境必须如实降级：不假装已连核心，也不让只读入口可点。
  await expect(page.getByTestId("core-capability")).toContainText("核心未通过");
  const readOnly = page.getByTestId("read-only-actions").getByRole("button");
  await expect(readOnly).toHaveCount(2);
  await expect(readOnly.first()).toBeDisabled();
  // 没有回执时不得伪造结果体：只显示未取得结果的原因。
  await expect(page.getByTestId("last-result")).toContainText("未取得结果");
  await expect(page.getByTestId("last-result-body")).toHaveCount(0);
  await expect(page.getByTestId("phase-summary").getByRole("heading")).toHaveCount(3);
  await expect(page.getByText("运行前", { exact: true })).toBeVisible();
  await expect(page.getByText("运行时", { exact: true })).toBeVisible();
  await expect(page.getByText("运行后", { exact: true })).toBeVisible();
  await expect(page.getByTestId("primary-actions").getByRole("button")).toHaveCount(3);

  // 「计划」必须精确匹配：否则会同时命中 AI 区的「生成计划草稿」（strict mode 冲突）。
  await page.getByRole("button", { name: "计划", exact: true }).click();
  await expect(page.getByTestId("detail-view").getByRole("heading", { name: "计划" })).toBeVisible();
  await page.getByRole("button", { name: "返回本次测试" }).click();
  await expect(page.getByText("当前测试", { exact: true })).toBeVisible();

  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test("host outcome is rendered verbatim without recalculating conclusions", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setContent('<html lang="zh-CN"><body><ai-test-panel></ai-test-panel></body></html>');
  await page.addScriptTag({ path: resolve("dist/index.js") });

  // 模拟宿主（扩展）回送一份**真实形状**的 doctor 回执。
  await page.evaluate(() => {
    window.postMessage(
      {
        kind: "outcome",
        action: "doctor",
        status: "ok",
        response: {
          protocol_version: "aitest.local/2.0",
          request_id: "req-1",
          result: {
            status: "READY",
            protocol: "aitest.local/2.0",
            supported_actions: ["doctor", "storage_usage", "events", "query"],
            phase: 1,
          },
          error: null,
        },
      },
      "*",
    );
  });

  await expect(page.getByTestId("panel-status")).toContainText("READY");
  await expect(page.getByTestId("panel-status")).toContainText("可派发动作 4");
  await expect(page.getByTestId("core-capability")).toContainText("storage_usage");
  const body = page.getByTestId("last-result-body");
  await expect(body).toContainText('"status": "READY"');
  await expect(body).toContainText('"supported_actions"');
  expect(errors).toEqual([]);
});