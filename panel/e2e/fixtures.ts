import { expect, test as base, type Page } from "@playwright/test";

export const test = base.extend<{ consoleErrors: string[] }>({
  consoleErrors: [
    async ({ page }, use) => {
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
      page.on("console", (message) => {
        if (message.type() === "error") {
          errors.push(`console: ${message.text()}`);
        }
      });
      await use(errors);
      expect(errors, "页面不应出现 console error 或未捕获异常").toEqual([]);
    },
    { auto: true },
  ],
});

export async function createProject(
  page: Page,
  name = "流程验证",
  root = "D:/work/flow",
): Promise<string> {
  await page.goto("/setup");
  await page.fill("#setup-root", root);
  await page.fill("#setup-name", name);
  await page.getByRole("button", { name: "登记并分析项目" }).click();
  await page.waitForURL(/\/overview/, { timeout: 10_000 });
  await expect(page.locator(".candidate-brief")).toBeVisible({ timeout: 10_000 });
  const match = page.url().match(/projects\/([^/]+)/);
  if (!match?.[1]) {
    throw new Error("project id not found in url");
  }
  return match[1];
}

export async function createTaskFromCandidate(page: Page, candidateIndex = 0): Promise<void> {
  await page.getByRole("button", { name: "查看候选" }).click();
  await page.waitForURL(/tasks\/new/);
  await expect(page.locator(".candidate").nth(candidateIndex)).toBeVisible();
  await page.locator(".candidate").nth(candidateIndex).click();
  await page.getByRole("button", { name: "用选中候选创建任务" }).click();
  await page.waitForURL(/\/scope/);
}

export async function startRun(page: Page): Promise<void> {
  await page.getByRole("button", { name: "生成计划" }).click();
  await page.waitForURL(/\/plan/);
  await page.locator("#confirm-execution").check();
  await page.getByRole("button", { name: "开始测试" }).click();
  await page.waitForURL(/\/result/, { timeout: 15_000 });
}

export async function createAndRun(
  page: Page,
  name = "流程验证",
  root = "D:/work/flow",
  candidateIndex = 0,
): Promise<string> {
  const projectId = await createProject(page, name, root);
  await createTaskFromCandidate(page, candidateIndex);
  await startRun(page);
  return projectId;
}
