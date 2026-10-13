import { test, expect } from "@playwright/test";
import { resolve } from "node:path";
import { postHostMessage } from "./host-outcome";

/** 宿主桩：记录面板派发的消息；剪贴板用桩，避免依赖浏览器权限。 */
async function mountPanel(page: import("@playwright/test").Page): Promise<void> {
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

function outcome(
  page: import("@playwright/test").Page,
  payload: Record<string, unknown>,
): Promise<void> {
  return postHostMessage(page, payload);
}

/** 问题详情（设计 §10/§12）：只读回执原样展示 + 复制说明不执行修复。 */
test("issue detail renders authoritative fields and never executes a fix", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mountPanel(page);
  await outcome(page, {
    kind: "outcome",
    action: "doctor",
    status: "ok",
    response: { result: { status: "READY", supported_actions: ["doctor", "query"] } },
  });
  await outcome(page, {
    kind: "projectContext",
    project_id: "project-ticket",
    run_id: "run-1",
  });

  // 只读入口在"当前测试"页：先读清单，再进"问题与报告"看行。
  await page
    .getByTestId("read-only-actions")
    .getByRole("button", { name: "问题清单（未关闭）" })
    .click();

  const sent = (): Promise<unknown[]> =>
    page.evaluate(() => (window as unknown as { __sent: unknown[] }).__sent);
  const listCall = (await sent()).at(-1) as Record<string, unknown>;
  expect(listCall["action"]).toBe("query");
  expect(listCall["parameters"]).toEqual({ view: "OPEN", limit: 20 });

  await outcome(page, {
    kind: "outcome",
    action: "query",
    status: "ok",
    response: {
      result: {
        items: [
          { aggregate_kind: "issue", record_id: "issue-1", revision: 3 },
        ],
        next_cursor: null,
      },
    },
  });

  // 打开详情：必须按**回执行给出的身份与修订**查询，不猜 id、不猜修订。
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await page.getByRole("button", { name: "issue-1@3（issue）" }).click();
  const detailCall = (await sent()).at(-1) as Record<string, unknown>;
  expect(detailCall["action"]).toBe("query");
  expect(detailCall["projectId"]).toBe("project-ticket");
  expect(detailCall["parameters"]).toEqual({
    aggregate_kind: "issue",
    record_id: "issue-1",
    revision: 3,
    limit: 1,
  });
  await expect(page.getByTestId("issue-detail")).toContainText("尚未读取该问题详情。");

  // 真实形状的详情回执：字段原样展示，缺失字段如实说明，不按状态推断。
  await outcome(page, {
    kind: "outcome",
    action: "query",
    status: "ok",
    response: {
      result: {
        aggregate_kind: "issue",
        record_id: "issue-1",
        revision: 3,
        record: {
          description: "下单后金额显示错误",
          reproduction: "打开结算页并提交订单",
          expected: "金额等于商品单价乘数量",
          actual: "金额少了优惠金额",
          evidence_refs: ["evidence-1"],
          status: "open",
        },
      },
    },
  });

  const detail = page.getByTestId("issue-detail");
  await expect(detail).toContainText("问题详情 issue-1@3");
  await expect(detail.locator('dd[data-field="reproduction"]')).toHaveText("打开结算页并提交订单");
  await expect(detail.locator('dd[data-field="expected"]')).toHaveText("金额等于商品单价乘数量");
  await expect(detail.locator('dd[data-field="fix_history"]')).toContainText("回执未提供该字段");
  await expect(detail.locator('dd[data-field="severity"]')).toContainText("回执未提供该字段");
  await expect(page.getByTestId("issue-detail-body")).toContainText('"record_id": "issue-1"');

  // 复制说明：只写剪贴板，**不派发任何核心动作**。
  const before = (await sent()).length;
  await page.getByRole("button", { name: "复制说明" }).click();
  await expect(page.getByTestId("copy-note")).toContainText("未执行修复");
  await expect(page.getByTestId("copy-note")).toContainText("未派发核心动作");
  expect(await page.evaluate(() => (window as unknown as { __copied: string[] }).__copied))
    .toEqual(["下单后金额显示错误"]);
  expect((await sent()).length).toBe(before);
  expect(errors).toEqual([]);
});

/** 只回记录行的详情回执（B `query` 的真实形状）：字段一律如实说明"未提供"，不补造。 */
test("rows-only detail outcome never fabricates fields or copyable text", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mountPanel(page);
  await outcome(page, {
    kind: "outcome",
    action: "doctor",
    status: "ok",
    response: { result: { status: "READY", supported_actions: ["doctor", "query"] } },
  });
  await outcome(page, {
    kind: "projectContext",
    project_id: "project-ticket",
    run_id: "run-1",
  });
  await page
    .getByTestId("read-only-actions")
    .getByRole("button", { name: "问题清单（未关闭）" })
    .click();
  await outcome(page, {
    kind: "outcome",
    action: "query",
    status: "ok",
    response: {
      result: { items: [{ aggregate_kind: "issue", record_id: "issue-5", revision: 1 }] },
    },
  });
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await page.getByRole("button", { name: "issue-5@1（issue）" }).click();
  // 真实 B query 只回记录行：详情块必须说明"只含记录行"，字段全部"未提供"。
  await outcome(page, {
    kind: "outcome",
    action: "query",
    status: "ok",
    response: {
      result: { items: [{ aggregate_kind: "issue", record_id: "issue-5", revision: 1 }] },
    },
  });
  const detail = page.getByTestId("issue-detail");
  await expect(page.getByTestId("issue-detail-rows-only")).toContainText("只含记录行");
  await expect(detail.locator('dd[data-field="description"]')).toContainText("回执未提供该字段");
  await expect(detail.locator('dd[data-field="status"]')).toContainText("回执未提供该字段");
  // 没有详情载荷时不得复制分页对象冒充"说明"。
  await expect(page.getByRole("button", { name: "复制说明" })).toBeDisabled();
  expect(await page.evaluate(() => (window as unknown as { __copied: string[] }).__copied))
    .toEqual([]);
  expect(errors).toEqual([]);
});

/** 未选择项目时不派发详情查询；核心拒绝时如实显示代码，不补造字段。 */
test("issue detail stays blocked without a project and shows refusals verbatim", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mountPanel(page);
  await outcome(page, {
    kind: "outcome",
    action: "doctor",
    status: "ok",
    response: { result: { status: "READY", supported_actions: ["doctor", "query"] } },
  });
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await expect(page.getByTestId("review-blocked")).toContainText("不猜项目身份");
  await page.getByRole("button", { name: "返回本次测试" }).click();

  await outcome(page, {
    kind: "projectContext",
    project_id: "project-ticket",
    run_id: "run-1",
  });
  await page
    .getByTestId("read-only-actions")
    .getByRole("button", { name: "问题清单（未关闭）" })
    .click();
  await outcome(page, {
    kind: "outcome",
    action: "query",
    status: "ok",
    response: {
      result: { items: [{ aggregate_kind: "issue", record_id: "issue-9", revision: 2 }] },
    },
  });
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await page.getByRole("button", { name: "issue-9@2（issue）" }).click();
  await outcome(page, {
    kind: "outcome",
    action: "query",
    status: "refused",
    code: "RECORD_NOT_FOUND",
    message: "issue-9@2 不存在",
  });
  const detail = page.getByTestId("issue-detail");
  await expect(detail).toContainText("核心未通过 · RECORD_NOT_FOUND");
  await expect(detail).toContainText("issue-9@2 不存在");
  // 没有详情回执时不显示字段表，也不让复制按钮可用（不伪造内容）。
  await expect(detail.locator("dd")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "复制说明" })).toBeDisabled();
  expect(errors).toEqual([]);
});
