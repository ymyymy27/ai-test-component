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
  // 面板检测到既有运行会自动读取一次（#108）；先按真实回执闭合这次只读请求。
  await postHostMessage(page, runView());
  await expect(page.getByRole("button", { name: "刷新本次视图" })).toBeEnabled();
}

const runView = (project = "p", run = "r") => ({ kind: "outcome", action: "read_view", status: "ok",
  response: { result: { view_type: "run", commit_id: 0, event_cursor: "cursor-0",
    generation: 1, index_version: 1,
    view: { project_id: project, run_id: run, snapshot_commit_id: "snapshot-0" } } } });

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
  await expect(page.getByTestId("phase-summary").locator('[aria-current="step"]')).toContainText("运行中");
  await page.getByRole("button",{name:"更多",exact:true}).click();
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
  await page.getByRole("button",{name:"更多",exact:true}).click();
  await page.getByRole("button", { name: "问题清单（未关闭）" }).click();
  const old = await page.evaluate(() => (window as unknown as { __sent: Record<string, unknown>[] }).__sent.at(-1)!);
  await postHostMessage(page, { kind: "projectContext", project_id: "new-project", run_id: "new-run" });
  await expect(page.getByTestId("project-context")).toContainText("new-project");
  await postHostMessage(page, { kind: "outcome", action: "query", status: "ok", clientRequestId: old["clientRequestId"],
    response: { result: { items: [{ record_id: "old-issue", revision: 1, aggregate_kind: "issue" }] } } });
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await expect(page.getByTestId("review-issues")).not.toContainText("old-issue");
});

test("delivery separates self report and verified scope and clears stale results", async ({page})=>{
  await mount(page);
  await page.getByRole("button",{name:"问题与报告",exact:true}).click();
  const delivery=page.getByRole("textbox",{name:"读取交付编号"});
  await delivery.fill("delivery-1");
  await page.getByRole("textbox",{name:"正式提测编号（可留空）"}).fill("submission-1");
  await page.getByRole("button",{name:"读取交付验证",exact:true}).click();
  const sent=await page.evaluate(()=>(window as unknown as {__sent:Record<string,unknown>[]}).__sent.at(-1));
  expect(sent).toMatchObject({action:"read_view",projectId:"p",parameters:{view:"delivery",
    delivery_id:"delivery-1",submission_id:"submission-1"}});
  const outcome={kind:"outcome",action:"read_view",status:"ok",response:{result:{
    view_type:"delivery",commit_id:10,event_cursor:"cursor-10",view:{project_id:"p",
      delivery_id:"delivery-1",submitted:true,submission:{submission_id:"submission-1"},
      self_report_completed:["developer says complete"],self_report_incomplete:[],
      verified_in_scope:["acceptance-1"],unverified_scope:["acceptance-2"],
      verification_state:"verification_valid",verification_business_outcome:"failed",
      verification_report_id:"report-1"}}}};
  await postHostMessage(page,outcome);
  const result=page.getByTestId("delivery-result");
  await expect(result).toContainText("开发自述完成：developer says complete");
  await expect(result).toContainText("实际验证范围：acceptance-1");
  await expect(result).toContainText("仍未验证范围：acceptance-2");
  await expect(result).toContainText("业务结论：失败");
  await page.getByRole("button",{name:"读取交付验证",exact:true}).click();
  outcome.response.result.view.verification_state="material_invalid";
  outcome.response.result.view.verified_in_scope=[];
  await postHostMessage(page,outcome);
  await expect(result).toContainText("核验状态：材料无效");
  await expect(result).toContainText("实际验证范围：无");
  await delivery.fill("delivery-2");
  await expect(result).toHaveCount(0);
  await page.getByRole("button",{name:"读取交付验证",exact:true}).click();
  await postHostMessage(page,outcome);
  await expect(result).toHaveCount(0);
  await postHostMessage(page,{kind:"projectContext",project_id:"other"});
  await expect(delivery).toHaveValue("");
});
