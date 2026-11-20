import { randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import type { Page } from "@playwright/test";

export const repoRoot = resolve(process.cwd(), "..");

export interface HostCallResult {
  ok: boolean;
  data?: unknown;
  error?: { code: string; message: string; next_step?: string };
}

export interface SampleProject {
  base: string;
  root: string;
  workspace: string;
}

export async function makeSampleProject(): Promise<SampleProject> {
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

export function callCli(
  workspace: string,
  command: string,
  payload: object,
  confirm = false,
): HostCallResult {
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

export async function installHost(page: Page, sample: SampleProject): Promise<void> {
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
}

export async function registerHostProject(page: Page, sample: SampleProject): Promise<string> {
  await page.goto("/setup");
  await page.fill("#setup-root", sample.root);
  await page.fill("#setup-name", "Host E2E");
  await page.getByRole("button", { name: "登记并分析项目" }).click();
  await page.waitForURL(/\/overview/, { timeout: 20_000 });
  const projectId = page.url().match(/projects\/([^/]+)/)?.[1];
  if (!projectId) {
    throw new Error("project id missing");
  }
  return projectId;
}

export async function cleanupSample(sample: SampleProject): Promise<void> {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      await rm(sample.base, { recursive: true, force: true });
      return;
    } catch {
      await new Promise((resolve) => {
        setTimeout(resolve, 500);
      });
    }
  }
}
