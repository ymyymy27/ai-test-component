import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { Page } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
export const repoRoot = resolve(here, "../..");

/** Use the repository venv by default; PATH `python` is not a stable target. */
export const cliPython =
  process.env.AITEST_E2E_PYTHON ??
  (process.platform === "win32"
    ? join(repoRoot, ".venv", "Scripts", "python.exe")
    : join(repoRoot, ".venv", "bin", "python"));

/** Whether the CLI child will have a model to call (env or user config). */
export function hasModelCredentials(): boolean {
  if ((process.env.AITEST_MODEL_KEY ?? "").trim()) {
    return true;
  }
  const appdata = process.env.APPDATA;
  if (!appdata) {
    return false;
  }
  try {
    const config = JSON.parse(
      readFileSync(join(appdata, "aitest", "config.json"), "utf8"),
    ) as { model?: { api_key?: string } };
    return Boolean((config.model?.api_key ?? "").trim());
  } catch {
    return false;
  }
}

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
): Promise<HostCallResult> {
  const args = ["-m", "aitest.cli", command, "--workspace", workspace];
  if (confirm) {
    args.push("--confirm");
  }
  return new Promise((resolvePromise) => {
    const child = spawn(cliPython, args, {
      env: { ...process.env, PYTHONPATH: join(repoRoot, "src") },
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const finish = (result: HostCallResult): void => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      resolvePromise(result);
    };
    const timer = setTimeout(() => {
      child.kill();
      finish({ ok: false, error: { code: "CLI_TIMEOUT", message: "CLI 调用超过 240 秒" } });
    }, 240_000);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });
    child.on("error", (error: Error) => {
      finish({ ok: false, error: { code: "CLI_SPAWN_FAILED", message: String(error) } });
    });
    child.on("close", (status: number | null) => {
      const lines = stdout
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
              finish({ ok: true, data: parsed.data });
              return;
            }
            finish({
              ok: false,
              error: parsed.error ?? {
                code: "CLI_FAILED",
                message: stderr || "CLI 返回失败",
              },
            });
            return;
          }
        } catch {
          // 继续尝试上一行。
        }
      }
      finish({
        ok: false,
        error: {
          code: "CLI_NO_OUTPUT",
          message: stderr || `CLI 没有返回 JSON（退出码 ${status ?? "null"}）`,
        },
      });
    });
    child.stdin.end(JSON.stringify({ command, request_id: randomUUID(), payload }));
  });
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
