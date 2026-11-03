import {test, expect, type Page} from "@playwright/test";
import {resolve} from "node:path";
import {postHostMessage} from "./host-outcome";

const runView = {
  kind: "outcome",
  action: "read_view",
  status: "ok",
  response: {result: {
    view_type: "run", commit_id: 0, event_cursor: "cursor-0",
    view: {project_id: "p", run_id: "r", snapshot_commit_id: "snapshot-0", steps: []},
  }},
};

const panelView = {
  kind: "outcome",
  action: "read_view",
  status: "ok",
  response: {result: {
    view_type: "panel", commit_id: 1, event_cursor: "cursor-1",
    view: {project_id: "p", run_id: "r", stage: "runtime", control_state: "running",
      business_outcome: "incomplete", evidence_level: null},
  }},
};

async function mountReady(page: Page): Promise<void> {
  await page.setContent("<html lang=\"zh-CN\"><body><ai-test-panel></ai-test-panel></body></html>");
  await page.evaluate(() => {
    const scope = window as unknown as {__sent: Record<string, unknown>[]; acquireVsCodeApi: () => unknown};
    scope.__sent = [];
    scope.acquireVsCodeApi = () => ({
      postMessage: (message: Record<string, unknown>) => scope.__sent.push(message),
    });
  });
  await page.addScriptTag({path: resolve("dist/index.js")});
  await postHostMessage(page, {
    kind: "outcome", action: "doctor", status: "ok",
    response: {result: {status: "READY", supported_actions: ["doctor", "query", "read_view"]}},
  });
  await postHostMessage(page, {kind: "projectContext", project_id: "p", run_id: "r"});
  await expect.poll(async () => {
    const sent = await page.evaluate(() => (window as unknown as {__sent: Record<string, unknown>[]}).__sent);
    return sent.at(-1)?.["action"];
  }).toBe("read_view");
  await postHostMessage(page, runView);
  await expect.poll(async () => {
    const sent = await page.evaluate(() => (window as unknown as {__sent: Record<string, unknown>[]}).__sent);
    return sent.at(-1)?.["parameters"];
  }).toEqual({view: "panel", run_id: "r"});
  await postHostMessage(page, panelView);
  await expect.poll(async () => {
    const sent = await page.evaluate(() => (window as unknown as {__sent: Record<string, unknown>[]}).__sent);
    return sent.at(-1)?.["action"];
  }).toBe("query");
  await postHostMessage(page, {
    kind: "outcome", action: "query", status: "ok",
    response: {result: {items: []}},
  });
}

async function openIssueWithRefs(page: Page, refs: unknown[]): Promise<void> {
  await page.getByRole("button", {name: "问题与报告", exact: true}).click();
  await page.getByRole("button", {name: "读取问题清单", exact: true}).click();
  await postHostMessage(page, {
    kind: "outcome", action: "query", status: "ok",
    response: {result: {items: [{aggregate_kind: "issue", record_id: "issue-1", revision: 3}]}},
  });
  await page.getByRole("button", {name: "issue-1@3（issue）"}).click();
  await postHostMessage(page, {
    kind: "outcome", action: "query", status: "ok",
    response: {result: {
      aggregate_kind: "issue", record_id: "issue-1", revision: 3,
      record: {description: "证据正文验证", evidence_refs: refs},
    }},
  });
}

function evidenceOutcome(
  body: string | undefined,
  omittedReason?: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    kind: "outcome", action: "read_view", status: "ok",
    response: {result: {
      view_type: "evidence", commit_id: 12, event_cursor: "cursor-12",
      view: {
        project_id: "p", record_id: "evidence-1", record_revision: 2,
        record_kind: "evidence", historical_snapshot: true,
        record: {
          evidence_id: "evidence-1", evidence_revision: 2,
          evidence_kind: "command_output", capture_source: "stdout",
          redaction_state: "not_required", object_digest: "sha256:abc",
          object_size: 128, media_type: "text/plain",
        },
        ...(body === undefined ? {} : {body}),
        ...(omittedReason === undefined ? {} : {body_omitted_reason: omittedReason}),
        ...overrides,
      },
    }},
  };
}

test("issue evidence refs open the exact revision and render body as inert text", async ({page}) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mountReady(page);
  await openIssueWithRefs(page, [
    "evidence-1@2",
    {evidence_id: "evidence-2", evidence_revision: 1},
    "evidence-without-revision",
  ]);

  const refs = page.getByTestId("issue-evidence-refs");
  await expect(refs.getByRole("button")).toHaveCount(2);
  await expect(page.getByTestId("issue-detail")).toContainText("证据引用缺少可核对的证据编号或修订号");
  await refs.getByRole("button", {name: "查看证据 evidence-1 · 修订 2"}).click();
  const sent = await page.evaluate(() =>
    (window as unknown as {__sent: Record<string, unknown>[]}).__sent.at(-1));
  expect(sent).toMatchObject({
    action: "read_view", projectId: "p",
    parameters: {view: "evidence", record_id: "evidence-1", revision: 2},
  });

  await postHostMessage(page, evidenceOutcome(
    '<img src=x onerror="window.__evidenceXss=1">\n第二行保持原样',
  ));
  const body = page.getByTestId("evidence-body");
  await expect(body).toHaveText('<img src=x onerror="window.__evidenceXss=1">\n第二行保持原样');
  await expect(body.locator("img")).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as {__evidenceXss?: number}).__evidenceXss))
    .toBeUndefined();
  await expect(page.getByTestId("evidence-metadata")).toContainText("command_output");
  await expect(page.getByTestId("evidence-metadata")).toContainText("sha256:abc");
  expect(errors).toEqual([]);
});

test("all omission reasons are shown in Chinese and contradictory receipts fail closed", async ({page}) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mountReady(page);
  await openIssueWithRefs(page, ["evidence-1@2"]);
  const open = page.getByRole("button", {name: "查看证据 evidence-1 · 修订 2"});
  const cases: ReadonlyArray<readonly [string, string]> = [
    ["redaction_unsafe", "未通过脱敏安全检查"],
    ["object_unavailable", "证据对象不可用"],
    ["body_too_large", "超过 4 MB"],
    ["object_unreadable", "读取或摘要校验失败"],
    ["object_corrupted", "大小与登记不一致"],
    ["binary_content", "二进制材料"],
  ];
  for (const [reason, copy] of cases) {
    await open.click();
    await postHostMessage(page, evidenceOutcome(undefined, reason));
    await expect(page.getByTestId("evidence-omitted")).toContainText(copy);
    await expect(page.getByTestId("evidence-body")).toHaveCount(0);
  }

  await open.click();
  await postHostMessage(page, evidenceOutcome("不应展示", "binary_content"));
  await expect(page.getByTestId("evidence-body")).toHaveCount(0);
  await expect(page.getByTestId("evidence-detail")).toContainText("形状冲突");
  expect(errors).toEqual([]);
});

test("foreign evidence receipts never replace the selected reference", async ({page}) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mountReady(page);
  await openIssueWithRefs(page, ["evidence-1@2"]);
  await page.getByRole("button", {name: "查看证据 evidence-1 · 修订 2"}).click();
  const foreign = evidenceOutcome("foreign body");
  foreign.response.result.view.record_id = "evidence-other";
  await postHostMessage(page, foreign);
  await expect(page.getByTestId("evidence-detail")).toContainText("身份与当前证据引用不一致");
  await expect(page.getByTestId("evidence-body")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("report evidence refs open the exact evidence body", async ({page}) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mountReady(page);
  await page.getByRole("button", {name: "问题与报告", exact: true}).click();
  await page.getByRole("button", {name: "读取报告修订", exact: true}).click();
  await postHostMessage(page, {
    kind: "outcome", action: "query", status: "ok",
    response: {result: {items: [{aggregate_kind: "report", record_id: "report-1", revision: 2}]}},
  });
  await page.getByRole("button", {name: "report-1 · 修订 2"}).click();
  let sent = await page.evaluate(() =>
    (window as unknown as {__sent: Record<string, unknown>[]}).__sent.at(-1));
  expect(sent).toMatchObject({
    action: "read_view", projectId: "p",
    parameters: {view: "report", record_id: "report-1", revision: 2},
  });
  await postHostMessage(page, {
    kind: "outcome", action: "read_view", status: "ok",
    response: {result: {
      view_type: "report", commit_id: 10, event_cursor: "cursor-10",
      view: {
        project_id: "p", record_id: "report-1", record_revision: 2,
        record_kind: "report", historical_snapshot: true,
        record: {
          report_id: "report-1", business_outcome: "passed",
          decision: {coverage: {selected: [], required: [], executed: [], reused: [], verified: [], passed: []}},
          context: {}, evidence_refs: ["evidence-1@2"],
        },
      },
    }},
  });
  const refs = page.getByTestId("record-evidence-refs");
  await refs.getByRole("button", {name: "查看证据 evidence-1 · 修订 2"}).click();
  sent = await page.evaluate(() =>
    (window as unknown as {__sent: Record<string, unknown>[]}).__sent.at(-1));
  expect(sent).toMatchObject({
    action: "read_view", projectId: "p",
    parameters: {view: "evidence", record_id: "evidence-1", revision: 2},
  });
  await postHostMessage(page, evidenceOutcome("报告关联的证据正文"));
  await expect(page.getByTestId("evidence-body")).toHaveText("报告关联的证据正文");
  expect(errors).toEqual([]);
});
