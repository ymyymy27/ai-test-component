import { test, expect } from "@playwright/test";
import { resolve } from "node:path";

/** 复测简报（只读 `copy_repair_brief`）：按当前问题身份读取，正文与元数据原样展示。 */
async function mount(page: import("@playwright/test").Page): Promise<void> {
  await page.setContent('<html lang="zh-CN"><body><ai-test-panel></ai-test-panel></body></html>');
  await page.evaluate(() => {
    const scope = window as unknown as {
      __sent: unknown[];
      __copied: string[];
      acquireVsCodeApi: () => unknown;
    };
    scope.__sent = [];
    scope.__copied = [];
    scope.acquireVsCodeApi = () => ({
      postMessage: (message: unknown) => {
        scope.__sent.push(message);
      },
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (value: string) => {
          scope.__copied.push(value);
        },
      },
    });
  });
  await page.addScriptTag({ path: resolve("dist/index.js") });
}

function send(page: import("@playwright/test").Page, message: Record<string, unknown>) {
  return page.evaluate(payload => {
    window.postMessage(payload, "*");
  }, message);
}

function sent(page: import("@playwright/test").Page): Promise<Array<Record<string, unknown>>> {
  return page.evaluate(
    () => (window as unknown as { __sent: Array<Record<string, unknown>> }).__sent,
  );
}

async function openIssueDetail(page: import("@playwright/test").Page): Promise<void> {
  await page.getByTestId("read-only-actions").getByRole("button", { name: "问题清单（未关闭）" })
    .click();
  await send(page, {
    kind: "outcome",
    action: "query",
    status: "ok",
    response: { result: { items: [{ aggregate_kind: "issue", record_id: "issue-7", revision: 2 }] } },
  });
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await page.getByRole("button", { name: "issue-7@2（issue）" }).click();
  // 详情查询的回执（真实 B query 形状：只回记录行），面板据此结束 pending。
  await send(page, {
    kind: "outcome",
    action: "query",
    status: "ok",
    response: { result: { items: [{ aggregate_kind: "issue", record_id: "issue-7", revision: 2 }] } },
  });
}

test("repair brief reads by the open issue identity and renders it verbatim", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mount(page);
  await send(page, {
    kind: "outcome",
    action: "doctor",
    status: "ok",
    response: { result: { status: "READY", supported_actions: ["doctor", "query"] } },
  });
  await send(page, { kind: "projectContext", project_id: "project-ticket", run_id: "run-3" });
  await openIssueDetail(page);

  const brief = page.getByTestId("repair-brief");
  await expect(page.getByTestId("repair-brief-empty")).toContainText("尚未读取复测简报");
  await page.getByRole("button", { name: "读取复测简报" }).click();
  const call = (await sent(page)).at(-1)!;
  expect(call["action"]).toBe("copy_repair_brief");
  expect(call["projectId"]).toBe("project-ticket");
  // 身份来自回执（issue-7）与宿主声明的 run（run-3），面板不猜。
  expect(call["parameters"]).toEqual({ issue_id: "issue-7", run_id: "run-3" });

  await send(page, {
    kind: "outcome",
    action: "copy_repair_brief",
    status: "ok",
    response: {
      result: {
        brief: "# 复测简报 issue-7\n\n- 标题：金额错误\n- 状态：open",
        issue_id: "issue-7",
        issue_revision: 2,
        run_id: "run-3",
        attempt_id: null,
        facts_state: "verified",
        unknown_positions: ["预期值"],
        excluded: ["原始日志正文"],
        redaction_policy_version: "aitest.redaction/1.0",
      },
    },
  });
  await expect(page.getByTestId("repair-brief-body")).toContainText("# 复测简报 issue-7");
  await expect(page.getByTestId("repair-brief-body")).toContainText("标题：金额错误");
  await expect(brief.locator('dd[data-brief-field="unknown_positions"]')).toHaveText('["预期值"]');
  await expect(brief.locator('dd[data-brief-field="excluded"]')).toHaveText('["原始日志正文"]');
  await expect(brief.locator('dd[data-brief-field="attempt_id"]')).toHaveCount(0);

  // 复制简报：只写剪贴板，不派发核心动作。
  const before = (await sent(page)).length;
  await page.getByRole("button", { name: "复制简报" }).click();
  await expect(page.getByTestId("brief-note")).toContainText("未派发核心动作");
  expect(await page.evaluate(() => (window as unknown as { __copied: string[] }).__copied))
    .toEqual(["# 复测简报 issue-7\n\n- 标题：金额错误\n- 状态：open"]);
  expect((await sent(page)).length).toBe(before);
  expect(errors).toEqual([]);
});

test("a refused brief shows the code and offers no body to copy", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mount(page);
  await send(page, {
    kind: "outcome",
    action: "doctor",
    status: "ok",
    response: { result: { status: "READY", supported_actions: ["doctor", "query"] } },
  });
  await send(page, { kind: "projectContext", project_id: "project-ticket", run_id: "run-3" });
  await openIssueDetail(page);
  await page.getByRole("button", { name: "读取复测简报" }).click();
  await send(page, {
    kind: "outcome",
    action: "copy_repair_brief",
    status: "refused",
    code: "ISSUE_NOT_FOUND",
    message: "issue-7 未保存",
  });
  await expect(page.getByTestId("repair-brief-error")).toContainText("ISSUE_NOT_FOUND");
  await expect(page.getByTestId("repair-brief-error")).toContainText("未取得简报正文");
  await expect(page.getByTestId("repair-brief-body")).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __copied: string[] }).__copied))
    .toEqual([]);
  expect(errors).toEqual([]);
});
