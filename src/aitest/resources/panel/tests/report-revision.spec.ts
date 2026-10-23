import { test, expect, type Page } from "@playwright/test";
import { resolve } from "node:path";
import { postHostMessage } from "./host-outcome";

const sent = (page: Page) =>
  page.evaluate(() => (window as unknown as { __sent: Record<string, unknown>[] }).__sent.at(-1)!);

const runView = () => ({ kind: "outcome", action: "read_view", status: "ok",
  response: { result: { view_type: "run", commit_id: 5, event_cursor: "cursor-5",
    view: { project_id: "p", run_id: "r", snapshot_commit_id: "snapshot-5",
      selected_case_ids: ["case"], required_case_ids: ["case"], steps: [] } } } });

const reportRows = () => ({ kind: "outcome", action: "query", status: "ok",
  response: { result: { items: [
    { record_id: "report-old", revision: 3, aggregate_kind: "report" },
  ], next_cursor: null, commit_id: 5, event_cursor: "cursor-5" } } });

async function mount(page: Page, actions: string[]) {
  await page.setContent("<ai-test-panel></ai-test-panel>");
  await page.evaluate(() => {
    const host = window as unknown as { __sent: Record<string, unknown>[]; acquireVsCodeApi: () => unknown };
    host.__sent = [];
    host.acquireVsCodeApi = () => ({ postMessage: (message: Record<string, unknown>) => host.__sent.push(message) });
  });
  await page.addScriptTag({ path: resolve("dist/index.js") });
  await postHostMessage(page, { kind: "outcome", action: "doctor", status: "ok",
    response: { result: { status: "READY", supported_actions: actions,
      transport_capabilities: ["native-confirmation/1"] } } });
  await postHostMessage(page, { kind: "projectContext", project_id: "p", run_id: "r", context_id: "ctx" });
}

test("existing run is read once automatically after connecting", async ({ page }) => {
  await mount(page, ["doctor", "query", "read_view", "create_report"]);

  const automatic = await sent(page);
  expect(automatic).toMatchObject({ kind: "dispatch", action: "read_view",
    projectId: "p", parameters: { view: "run", run_id: "r" } });

  await postHostMessage(page, runView());
  await expect(page.getByRole("button", { name: "刷新本次视图" })).toBeEnabled();
  const messages = await page.evaluate(() =>
    (window as unknown as { __sent: Record<string, unknown>[] }).__sent
      .filter(message => message["action"] === "read_view").length);
  expect(messages).toBe(1);
});

test("report save reuses the run identity and increments the content revision", async ({ page }) => {
  await mount(page, ["doctor", "query", "read_view", "create_report"]);
  await postHostMessage(page, runView());
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();

  await page.getByRole("button", { name: "保存本次测试报告", exact: true }).click();
  const lookup = await sent(page);
  expect(lookup).toMatchObject({ kind: "dispatch", action: "query", parameters: { run_id: "r" } });
  await postHostMessage(page, reportRows());

  const first = await sent(page);
  expect(first).toMatchObject({ kind: "controlledDispatch", action: "create_report",
    expectedRevision: 3, parameters: { run_id: "r", report_id: "report-old", issue_ids: [] } });
  await postHostMessage(page, { kind: "outcome", action: "create_report", status: "ok",
    response: { result: { aggregate_kind: "report", record_id: "report-old", revision: 4 } } });

  await expect(page.getByRole("button", { name: "保存本次测试报告", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "保存本次测试报告", exact: true }).click();
  const second = await sent(page);
  expect(second).toMatchObject({ kind: "controlledDispatch", action: "create_report",
    expectedRevision: 4, parameters: { run_id: "r", report_id: "report-old", issue_ids: [] } });
  expect(second["intentId"]).not.toBe(first["intentId"]);
});
