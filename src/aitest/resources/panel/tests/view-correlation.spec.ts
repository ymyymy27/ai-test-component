import { test, expect, type Page } from "@playwright/test";
import { resolve } from "node:path";
import { postHostMessage } from "./host-outcome";

async function mount(page: Page) {
  await page.setContent("<ai-test-panel></ai-test-panel>");
  await page.evaluate(() => {
    const scope = window as unknown as { __sent: Record<string, unknown>[]; acquireVsCodeApi: () => unknown };
    scope.__sent = [];
    scope.acquireVsCodeApi = () => ({ postMessage: (message: Record<string, unknown>) => scope.__sent.push(message) });
  });
  await page.addScriptTag({ path: resolve("dist/index.js") });
  await postHostMessage(page, { kind: "outcome", action: "doctor", status: "ok",
    response: { result: { status: "READY", supported_actions: ["doctor", "query", "read_view"] } } });
  await postHostMessage(page, { kind: "projectContext", project_id: "p", run_id: "r" });
  await expect(page.getByRole("button", { name: "刷新本次视图" })).toBeEnabled();
}

const viewOutcome = (project = "p") => ({ kind: "outcome", action: "read_view", status: "ok",
  response: { result: { view_type: "panel", commit_id: 9, event_cursor: "cursor-9",
    generation: 1, index_version: 1, view: { project_id: project, run_id: "r", stage: "runtime",
      control_state: "running", business_outcome: "incomplete", evidence_level: null } } } });

test("owned core view and outer boundary are rendered without changing doctor capability", async ({ page }) => {
  await mount(page);
  await page.getByRole("button", { name: "刷新本次视图" }).click();
  await postHostMessage(page, viewOutcome());
  await expect(page.getByTestId("panel-view-boundary")).toContainText("9");
  await expect(page.getByTestId("panel-view-boundary")).toContainText("cursor-9");
  await expect(page.getByTestId("phase-summary").locator('[aria-current="step"]')).toContainText("running");
  await expect(page.getByTestId("core-capability")).toContainText("read_view");
});

test("wrong correlation and foreign view never replace the current scoped view", async ({ page }) => {
  await mount(page);
  const button = page.getByRole("button", { name: "刷新本次视图" });
  await button.click();
  await postHostMessage(page, { ...viewOutcome(), clientRequestId: "old-request" });
  await expect(button).toBeDisabled();
  await expect(page.getByTestId("panel-view-boundary")).not.toContainText("cursor-9");
  await postHostMessage(page, viewOutcome("foreign"));
  await expect(button).toBeEnabled();
  await expect(page.getByTestId("panel-view-boundary")).not.toContainText("cursor-9");
});

test("project switch clears pending scope and rejects its late reply", async ({ page }) => {
  await mount(page);
  await page.getByRole("button", { name: "问题清单（未关闭）" }).click();
  const old = await page.evaluate(() => (window as unknown as { __sent: Record<string, unknown>[] }).__sent.at(-1)!);
  await postHostMessage(page, { kind: "projectContext", project_id: "new-project", run_id: "new-run" });
  await expect(page.getByTestId("project-context")).toContainText("new-project");
  await postHostMessage(page, { kind: "outcome", action: "query", status: "ok", clientRequestId: old["clientRequestId"],
    response: { result: { items: [{ record_id: "old-issue", revision: 1, aggregate_kind: "issue" }] } } });
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await expect(page.getByTestId("review-issues")).not.toContainText("old-issue");
});
