import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";

import { expect } from "@playwright/test";

import { confirmPlan, test } from "./fixtures";

test.describe.configure({ timeout: 300_000 });

const repoRoot = resolve(process.cwd(), "..");

interface HostCallResult {
  ok: boolean;
  data?: unknown;
  error?: { code: string; message: string; next_step?: string };
}

async function makeSampleProject(): Promise<{ base: string; root: string; workspace: string }> {
  const base = await mkdtemp(join(tmpdir(), "aitest-host-"));
  const root = join(base, "sample");
  const workspace = join(base, "workspace");
  await mkdir(root, { recursive: true });
  await mkdir(join(root, "tests"), { recursive: true });
  await mkdir(workspace, { recursive: true });
  await writeFile(
    join(root, "README.md"),
    "# Sample Calculator\n\nFeatures:\n- add two numbers\n\nRun: python -m pytest -q\n",
    "utf8",
  );
  await writeFile(
    join(root, "app.py"),
    "def add(a: int, b: int) -> int:\n    return a + b\n",
    "utf8",
  );
  await writeFile(
    join(root, "tests", "test_app.py"),
    "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    "utf8",
  );
  await writeFile(
    join(root, "pyproject.toml"),
    '[project]\nname = "sample-calculator"\nversion = "0.1.0"\n',
    "utf8",
  );
  return { base, root, workspace };
}

function callCli(workspace: string, command: string, payload: object, confirm = false): HostCallResult {
  const args = ["-m", "aitest.cli", command, "--workspace", workspace];
  if (confirm) {
    args.push("--confirm");
  }
  const child = spawnSync("python", args, {
    input: JSON.stringify({ command, request_id: randomUUID(), payload }),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: join(repoRoot, "src") },
    timeout: 240_000,
    windowsHide: true,
  });
  const lines = (child.stdout ?? "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  for (const line of lines.reverse()) {
    try {
      const parsed = JSON.parse(line) as {
        ok?: boolean;
        data?: unknown;
        error?: { code: string; message: string; next_step?: string };
      };
      if (typeof parsed.ok === "boolean") {
        if (parsed.ok) {
          return { ok: true, data: parsed.data };
        }
        return {
          ok: false,
          error: parsed.error ?? {
            code: "CLI_FAILED",
            message: child.stderr || "CLI 返回失败",
          },
        };
      }
    } catch {
      // 继续尝试上一行。
    }
  }
  return {
    ok: false,
    error: {
      code: "CLI_NO_OUTPUT",
      message: child.stderr || `CLI 没有返回 JSON（退出码 ${child.status ?? "null"}）`,
    },
  };
}

test("真实 CLI 宿主模式：分析、任务、计划、确认、运行、结果、报告", async ({ page }) => {
  const sample = await makeSampleProject();
  try {
    await page.exposeFunction(
      "__hostCall",
      (command: string, payload: object, options?: { confirm?: boolean }) =>
        callCli(sample.workspace, command, payload, options?.confirm === true),
    );
    await page.addInitScript(
      ({ workspace, projectRoot }) => {
        const hostWindow = window as unknown as {
          __hostCall: (
            command: string,
            payload: object,
            options?: { confirm?: boolean },
          ) => Promise<unknown>;
        };
        (window as unknown as { aitestHost: unknown }).aitestHost = {
          call: (command: string, payload: object, options?: { confirm?: boolean }) =>
            hostWindow.__hostCall(command, payload, options),
          getContext: () => ({
            workspacePath: workspace,
            projectRoot,
            projectId: "",
            pythonPath: "",
            workspaceFolderCount: 1,
          }),
          getState: () => null,
          setState: () => undefined,
          openPath: async () => ({ ok: true, data: true }),
        };
      },
      { workspace: sample.workspace, projectRoot: sample.root },
    );

    await page.goto("/setup");
    await page.fill("#setup-root", sample.root);
    await page.fill("#setup-name", "Host E2E");
    await page.getByRole("button", { name: "登记并分析项目" }).click();
    await page.waitForURL(/\/overview/, { timeout: 20_000 });
    await expect(page.locator(".candidate-brief")).toBeVisible({ timeout: 180_000 });

    await page.getByRole("button", { name: "新建任务" }).click();
    await page.waitForURL(/tasks\/new/);
    await page.locator("textarea").fill("验证 add(1,2) 返回 3");
    await page.getByRole("button", { name: "开始测试" }).click();
    await page.waitForURL(/\/scope/, { timeout: 120_000 });
    await expect(page.locator(".scope-summary")).toContainText("/");

    await page.getByRole("button", { name: "生成计划" }).click();
    await page.waitForURL(/\/plan/, { timeout: 180_000 });
    await expect(page.locator(".plan-summary")).toContainText("本次");

    await confirmPlan(page);
    await page.getByRole("button", { name: "开始测试" }).click();
    await page.waitForURL(/\/result/, { timeout: 240_000 });
    await expect(page.locator(".result-verdict")).toBeVisible();
    await expect(page.locator(".report-strip-title")).toContainText("测试报告", {
      timeout: 60_000,
    });

    await page.locator(".report-strip").getByRole("button", { name: "查看报告" }).click();
    await page.waitForURL(/\/reports\//);
    await expect(page.getByRole("heading", { name: "范围与覆盖" })).toBeVisible();
    await expect(page.locator("body")).toContainText("本次");

    const projectId = page.url().match(/projects\/([^/]+)/)?.[1];
    if (!projectId) {
      throw new Error("project id missing");
    }
    await page.goto(`/projects/${projectId}/settings`);
    await page.locator(".settings-index").getByRole("button", { name: "运行配置" }).click();
    await page.fill("#config-build", "python -m compileall -q app.py");
    await page.fill("#config-entry", "app");
    await page.fill("#config-smoke", "python -m pytest -q");
    await page.getByRole("button", { name: "保存运行配置" }).click();
    await expect(page.locator(".toast")).toContainText("运行配置已保存", { timeout: 30_000 });

    await page.goto(`/projects/${projectId}/quick-check`);
    await page.locator("#quick-confirm").check();
    await page.getByRole("button", { name: "开始快速检查" }).click();
    await expect(page.locator(".quick-result")).toContainText("完成于", { timeout: 120_000 });
    await expect(page.locator(".check-list")).toContainText("构建");
    await expect(page.locator(".check-list")).toContainText("启动入口");
    await expect(page.locator("body")).toContainText("不进入验收项覆盖");
  } finally {
    await page.close().catch(() => undefined);
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        await rm(sample.base, { recursive: true, force: true });
        break;
      } catch {
        await new Promise((resolve) => {
          setTimeout(resolve, 500);
        });
      }
    }
  }
});
