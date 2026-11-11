import { expect, type Page } from "@playwright/test";
import { resolve } from "node:path";

export type SendMatch = { action?: string; kind?: string; view?: string; stage?: string };

export async function mountPanel(page: Page, withHost = true): Promise<void> {
  await page.setViewportSize({ width: 390, height: 780 });
  await page.setContent('<html lang="zh-CN"><body><ai-test-panel></ai-test-panel></body></html>');
  if (withHost) {
    await page.evaluate(() => {
      const host = window as unknown as { __sent: Array<Record<string, unknown>>; acquireVsCodeApi: () => unknown };
      host.__sent = [];
      host.acquireVsCodeApi = () => ({
        postMessage: (message: unknown) => host.__sent.push(message as Record<string, unknown>),
      });
    });
  }
  await page.addScriptTag({ path: resolve("dist/index.js") });
}

export async function sentMessages(page: Page): Promise<Array<Record<string, unknown>>> {
  return page.evaluate(() => (window as unknown as { __sent?: Array<Record<string, unknown>> }).__sent ?? []);
}

export async function waitForSend(
  page: Page,
  match: SendMatch,
  timeout = 5000,
  afterIndex = 0,
): Promise<Record<string, unknown>> {
  let found: Record<string, unknown> | undefined;
  await expect.poll(async () => {
    const sent = await sentMessages(page);
    found = [...sent]
      .map((message, index) => ({ message, index }))
      .reverse()
      .find(({ message, index }) => index >= afterIndex && matches(message, match))?.message;
    return found !== undefined;
  }, { timeout }).toBe(true);
  if (!found) throw new Error("message not found");
  return found;
}

function matches(message: Record<string, unknown>, match: SendMatch): boolean {
  const parameters = (message["parameters"] ?? {}) as Record<string, unknown>;
  if (match.action && message["action"] !== match.action) return false;
  if (match.kind && message["kind"] !== match.kind) return false;
  if (match.view && parameters["view"] !== match.view) return false;
  if (match.stage && message["stage"] !== match.stage) return false;
  return true;
}

export async function reply(
  page: Page,
  payload: Record<string, unknown>,
  match?: SendMatch,
): Promise<void> {
  await page.evaluate(({ payload, matchSource }) => {
    const sent = (window as unknown as { __sent?: Array<Record<string, unknown>> }).__sent ?? [];
    const matchesSource = (message: Record<string, unknown>): boolean => {
      if (!matchSource) return true;
      const parameters = (message["parameters"] ?? {}) as Record<string, unknown>;
      if (matchSource["action"] && message["action"] !== matchSource["action"]) return false;
      if (matchSource["kind"] && message["kind"] !== matchSource["kind"]) return false;
      if (matchSource["view"] && parameters["view"] !== matchSource["view"]) return false;
      if (matchSource["stage"] && message["stage"] !== matchSource["stage"]) return false;
      return true;
    };
    const target = [...sent].reverse().find(matchesSource);
    const message = { ...payload };
    if ((message["kind"] === "outcome" || message["kind"] === "wizardResult")
        && message["clientRequestId"] === undefined && target) {
      message["clientRequestId"] = target["clientRequestId"];
    }
    window.postMessage(message, "*");
  }, { payload, matchSource: match ?? null });
}

export async function replyView(page: Page, view: string, result: Record<string, unknown>): Promise<void> {
  await reply(
    page,
    {
      kind: "outcome",
      action: "read_view",
      status: "ok",
      response: { result: { view_type: view, view: result } },
    },
    { action: "read_view", view },
  );
}

export async function replyDoctor(page: Page, result: Record<string, unknown> = {}): Promise<void> {
  await reply(
    page,
    {
      kind: "outcome",
      action: "doctor",
      status: "ok",
      response: { result: { status: "READY", protocol: "aitest.local/2.0", ...result } },
    },
    { action: "doctor" },
  );
}

export async function connect(page: Page, doctor: Record<string, unknown> = {}): Promise<void> {
  await waitForSend(page, { kind: "ensureCore" });
  await page.evaluate(() => {
    window.postMessage({
      kind: "projectContext",
      project_id: "project-1",
      run_id: null,
      scope_name: "样例项目",
      context_id: "ctx-1",
    }, "*");
  });
  await reply(page, {
    kind: "outcome",
    action: "ensure-core",
    status: "ok",
    response: { status: "READY", workspace_id: "workspace-1", instance_id: "core-1" },
  }, { kind: "ensureCore" });
  await replyDoctor(page, {
    workspace_id: "workspace-1",
    supported_actions: ["doctor", "query", "read_view", "events", "storage_usage", "prepare_execution", "authorize_step", "execute_step", "verify_pending", "create_report", "export_report", "start_run"],
    ...doctor,
  });
  await expect(page.locator(".footer-status")).toContainText("核心已连接");
}

export async function connectWithRun(
  page: Page,
  panelView: Record<string, unknown>,
  runView: Record<string, unknown>,
  doctor: Record<string, unknown> = {},
): Promise<void> {
  await waitForSend(page, { kind: "ensureCore" });
  await page.evaluate(() => {
    window.postMessage({
      kind: "projectContext",
      project_id: "project-1",
      run_id: "run-1",
      scope_name: "样例项目",
      context_id: "ctx-1",
    }, "*");
  });
  await reply(page, {
    kind: "outcome",
    action: "ensure-core",
    status: "ok",
    response: { status: "READY", workspace_id: "workspace-1", instance_id: "core-1" },
  }, { kind: "ensureCore" });
  await replyDoctor(page, {
    workspace_id: "workspace-1",
    supported_actions: ["doctor", "query", "read_view", "events", "storage_usage", "prepare_execution", "authorize_step", "execute_step", "verify_pending", "create_report", "export_report", "start_run"],
    ...doctor,
  });
  await replyView(page, "panel", panelView);
  await replyView(page, "run", runView);
  await expect(page.locator(".footer-status")).toContainText("核心已连接");
}
