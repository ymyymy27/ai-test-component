import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";

import { expect } from "@playwright/test";

import { confirmPlan, test } from "./fixtures";
import {
  cleanupSample,
  hasModelCredentials,
  installHost,
  makeSampleProject,
  registerHostProject,
  type SampleProject,
} from "./hostFixture";

test.describe.configure({ timeout: 300_000 });

interface PersistedRun {
  started_at: string;
  confirmed_by?: string;
}

async function latestRun(workspace: string, projectId: string): Promise<PersistedRun | null> {
  const runsDir = join(workspace, "projects", projectId, "runs");
  const entries = await readdir(runsDir, { withFileTypes: true });
  let latest: PersistedRun | null = null;
  for (const entry of entries.filter((item) => item.isDirectory())) {
    try {
      const parsed = JSON.parse(
        await readFile(join(runsDir, entry.name, "run.json"), "utf8"),
      ) as PersistedRun;
      if (!latest || parsed.started_at > latest.started_at) {
        latest = parsed;
      }
    } catch {
      // 忽略尚未写完的运行目录。
    }
  }
  return latest;
}

test("真实 CLI 宿主模式：分析、任务、计划、确认、运行、结果、报告", async ({ page }) => {
  test.skip(!hasModelCredentials(), "需要 AITEST_MODEL_KEY 或用户配置 model.api_key；不静默跳过分析");
  const sample: SampleProject = await makeSampleProject();
  try {
    await installHost(page, sample);
    const projectId = await registerHostProject(page, sample);
    await expect(page.locator(".candidate-brief")).toBeVisible({ timeout: 180_000 });

    await page.getByRole("button", { name: "开始测试" }).click();
    await page.waitForURL(/tasks\/new/);
    await page.locator(".test-mode").filter({ hasText: "按目标测试" }).click();
    await page.locator("textarea").fill("验证 add(1,2) 返回 3");
    await page.getByRole("button", { name: "创建任务并生成计划" }).click();
    await page.waitForURL(/\/plan/, { timeout: 180_000 });
    await expect(page.locator(".plan-summary")).toContainText("本次");

    await confirmPlan(page);
    await page.getByRole("button", { name: "开始测试" }).click();
    await page.waitForURL(/\/result/, { timeout: 240_000 });
    await expect(page.locator(".result-verdict")).toBeVisible();
    await expect(page.locator(".run-environment")).toContainText("Python");
    await expect(page.locator(".report-strip-title")).toContainText("测试报告", {
      timeout: 60_000,
    });

    const persisted = await latestRun(sample.workspace, projectId);
    expect(persisted?.confirmed_by).toBe("本机用户");

    await page.locator(".report-strip").getByRole("button", { name: "查看报告" }).click();
    await page.waitForURL(/\/reports\//);
    await expect(page.getByRole("heading", { name: "范围与覆盖" })).toBeVisible();
    await expect(page.locator("body")).toContainText("本次");

    await page.goto(`/projects/${projectId}/settings`);
    await page.locator(".settings-index").getByRole("tab", { name: "运行配置" }).click();
    await page.fill("#config-build", "python -m compileall -q app.py");
    await page.fill("#config-entry", "app");
    await page.fill("#config-smoke", "python -m pytest -q");
    await page.fill("#config-evidence-file", "12");
    await page.fill("#config-evidence-media", "300");
    await page.fill("#config-log", "a.log, b.log");
    await page.getByRole("button", { name: "保存运行配置" }).click();
    await expect(page.locator(".toast")).toContainText("运行配置已保存", { timeout: 30_000 });

    const savedConfig = JSON.parse(
      await readFile(join(sample.workspace, "projects", projectId, "project.config.json"), "utf8"),
    ) as {
      evidence?: Record<string, unknown>;
      link?: Record<string, unknown>;
    };
    expect(savedConfig.evidence?.file_cap_mb).toBe(12);
    expect(savedConfig.evidence?.media_budget_mb).toBe(300);
    expect(savedConfig.link?.log_paths).toEqual(["a.log", "b.log"]);

    await page.goto(`/projects/${projectId}/quick-check`);
    await page.locator("#quick-confirm").check();
    await page.getByRole("button", { name: "开始快速检查" }).click();
    await expect(page.locator(".quick-result")).toContainText("完成于", { timeout: 120_000 });
    await expect(page.locator(".check-list")).toContainText("构建");
    await expect(page.locator(".check-list")).toContainText("启动入口");
    await expect(page.locator("body")).toContainText("不进入验收项覆盖");

    await page.goto(`/projects/${projectId}/overview`);
    await page.locator(".side-task-more").first().click();
    await page.getByRole("menuitem", { name: "删除" }).click();
    await expect(page.locator("#confirm-dialog-title")).toBeVisible();
    await page.locator(".dialog-confirm").click();
    await expect(page.locator(".side-task-row")).toHaveCount(0);
  } finally {
    await page.close().catch(() => undefined);
    await cleanupSample(sample);
  }
});

test("宿主模式没有运行记录时不允许登记问题", async ({ page }) => {
  const sample = await makeSampleProject();
  try {
    await installHost(page, sample);
    const projectId = await registerHostProject(page, sample);
    await page.goto(`/projects/${projectId}/issues`);
    const button = page.getByRole("button", { name: "登记问题" });
    await expect(button).toBeDisabled({ timeout: 20_000 });
    await expect(button).toHaveAttribute("title", /先完成一次运行/);
  } finally {
    await page.close().catch(() => undefined);
    await cleanupSample(sample);
  }
});
