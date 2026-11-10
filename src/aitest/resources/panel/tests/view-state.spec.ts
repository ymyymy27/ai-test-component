import { test, expect, type Page } from "@playwright/test";
import { resolve } from "node:path";
import { postHostMessage } from "./host-outcome";

async function mount(page: Page): Promise<void> {
  await page.setViewportSize({ width: 360, height: 320 });
  await page.setContent('<html lang="zh-CN"><body><ai-test-panel></ai-test-panel></body></html>');
  // 宿主桩必须在面板脚本加载前注入：面板只在宿主存在时派发写查询。
  await page.evaluate(() => {
    const host = window as unknown as {__sent: unknown[]; acquireVsCodeApi: () => unknown};
    host.__sent = [];
    host.acquireVsCodeApi = () => ({postMessage: (message: unknown) => host.__sent.push(message)});
  });
  await page.addScriptTag({path: resolve("dist/index.js")});
}

const projectContext = {
  kind: "projectContext",
  project_id: "project-1",
  run_id: null,
  scope_name: "样例",
  context_id: "ctx-1",
} as const;

test("full re-render keeps the focused field and its draft value", async ({ page }) => {
  await mount(page);
  await postHostMessage(page, projectContext);
  await page.getByRole("button",{name:"设置",exact:true}).click();
  const field = page.locator('[data-field="model"]');
  await expect(field).toBeVisible();

  // 首次进入设置页会请求模型配置；回执到达后表单才可编辑。
  const request = await page.evaluate(() => {
    const sent = (window as unknown as {__sent: Array<Record<string, unknown>>}).__sent;
    return sent.find(message => message["kind"] === "wizardModelLoad")?.["clientRequestId"] as string | undefined;
  });
  expect(request).toBeTruthy();
  await postHostMessage(page, {
    kind: "wizardResult",
    clientRequestId: request,
    modelSettings: {
      endpoint: "https://api.deepseek.com",
      model: "deepseek-flash",
      timeout: "60",
      reference: "cred-ref",
    },
  });
  await expect(field).toBeEnabled();
  await field.fill("draft-model");
  await expect(field).toBeFocused();

  // projectContext 会触发整页 render；焦点、选区与草稿都必须保留。
  await postHostMessage(page, projectContext);
  await expect(field).toBeFocused();
  await expect(field).toHaveValue("draft-model");
});

test("full re-render keeps the scroll position in a long detail view", async ({ page }) => {
  await mount(page);
  await postHostMessage(page, projectContext);
  await page.getByRole("button",{name:"更多",exact:true}).click();
  await expect(page.getByTestId("detail-view")).toBeVisible();

  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  const before = await page.evaluate(() => window.scrollY);
  expect(before).toBeGreaterThan(0);

  await postHostMessage(page, projectContext);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(before);
});
