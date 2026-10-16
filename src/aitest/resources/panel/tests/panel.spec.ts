import { test, expect } from "@playwright/test";
import { resolve } from "node:path";
import { postHostMessage } from "./host-outcome";

test("successful connection refreshes capabilities without another user click", async ({ page }) => {
  await page.setContent('<ai-test-panel></ai-test-panel>');
  await page.evaluate(() => {
    const host = window as unknown as {__sent: unknown[]; acquireVsCodeApi: () => unknown};
    host.__sent = [];
    host.acquireVsCodeApi = () => ({postMessage: (message: unknown) => host.__sent.push(message)});
  });
  await page.addScriptTag({path: resolve("dist/index.js")});
  await postHostMessage(page, {kind:"outcome", action:"doctor", status:"unavailable",
    code:"CORE_RESULT_UNVERIFIED", message:"not connected"});
  await page.getByRole("button", {name:"更多", exact:true}).click();
  await page.getByRole("button", {name:"连接核心", exact:true}).click();
  await postHostMessage(page, {kind:"outcome", action:"ensure-core", status:"ok",
    response:{status:"READY", workspace_id:"workspace", instance_id:"core"}});
  await expect.poll(() => page.evaluate(() => {
    const sent = (window as unknown as {__sent: Array<Record<string, unknown>>}).__sent;
    return sent.at(-1)?.action;
  })).toBe("doctor");
  await postHostMessage(page, {kind:"outcome", action:"doctor", status:"ok",
    response:{result:{status:"READY", supported_actions:["doctor", "query"]}}});
  await expect(page.getByTestId("panel-status")).toContainText("核心已连接");
  await page.getByRole("button",{name:"更多",exact:true}).click();
  await expect(page.getByTestId("core-capability")).toContainText("query");
});

test("prototype follows current-test panel and same-panel detail flow", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 360, height: 720 });
  await page.setContent('<html lang="zh-CN"><body><ai-test-panel></ai-test-panel></body></html>');
  await page.addScriptTag({ path: resolve("dist/index.js") });

  await expect(page.getByTestId("panel-status")).toContainText("未连接核心");
  // 非宿主环境必须如实降级：不假装已连核心，也不让只读入口可点。
  await page.getByRole("button",{name:"更多",exact:true}).click();
  await expect(page.getByTestId("core-capability")).toContainText("核心未通过");
  const readOnly = page.getByTestId("read-only-actions").getByRole("button");
  await expect(readOnly).toHaveCount(4);
  await expect(readOnly.first()).toBeDisabled();
  // 宿主未声明项目时如实显示"未选择"，且项目作用域查询不可用。
  await expect(page.getByTestId("project-context")).toContainText("选择测试项目");
  // 没有回执时不得伪造结果体：只显示未取得结果的原因。
  await expect(page.getByTestId("last-result")).toContainText("未取得结果");
  await expect(page.getByTestId("last-result-body")).toHaveCount(0);
  await page.getByRole("button",{name:"返回本次测试"}).click();
  await expect(page.getByTestId("phase-summary").getByRole("heading")).toHaveCount(1);
  await expect(page.getByTestId("primary-actions").getByRole("button")).toHaveCount(1);

  // 「计划」必须精确匹配：否则会同时命中 AI 区的「生成计划草稿」（strict mode 冲突）。
  await page.getByRole("button", { name: "查看准备详情", exact: true }).click();
  await expect(page.getByTestId("detail-view").getByRole("heading", { name: "计划", exact:true })).toBeVisible();
  await page.getByRole("button", { name: "返回本次测试" }).click();
  await expect(page.getByText("当前测试", { exact: true })).toBeVisible();

  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test("host outcome is rendered verbatim without recalculating conclusions", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setContent('<html lang="zh-CN"><body><ai-test-panel></ai-test-panel></body></html>');
  await page.addScriptTag({ path: resolve("dist/index.js") });

  // 模拟宿主（扩展）回送一份**真实形状**的 doctor 回执。
  await postHostMessage(page, {
        kind: "outcome",
        action: "doctor",
        status: "ok",
        response: {
          protocol_version: "aitest.local/2.0",
          request_id: "req-1",
          result: {
            status: "READY",
            protocol: "aitest.local/2.0",
            supported_actions: ["doctor", "storage_usage", "events", "query"],
            phase: 1,
          },
          error: null,
        },
      });

  await expect(page.getByTestId("panel-status")).toContainText("核心已连接");
  await expect(page.getByTestId("panel-status")).not.toContainText("可派发动作");
  await page.getByRole("button",{name:"更多",exact:true}).click();
  await expect(page.getByTestId("core-capability")).toContainText("storage_usage");
  const body = page.getByTestId("last-result-body");
  await expect(body).toContainText('"status": "READY"');
  await expect(body).toContainText('"supported_actions"');
  expect(errors).toEqual([]);
});

/** 只读项目作用域查询：面板只用宿主声明的项目身份，回执行原样展示。 */
test("project context gates read-only queries and rows render verbatim", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setContent('<html lang="zh-CN"><body><ai-test-panel></ai-test-panel></body></html>');
  // 宿主桩必须在面板脚本加载**之前**注入：面板只在宿主存在时派发。
  await page.evaluate(() => {
    const scope = window as unknown as {
      __sent: unknown[];
      acquireVsCodeApi: () => unknown;
    };
    scope.__sent = [];
    scope.acquireVsCodeApi = () => ({
      postMessage: (message: unknown) => {
        scope.__sent.push(message);
      },
    });
  });
  await page.addScriptTag({ path: resolve("dist/index.js") });

  // 首次连接只派发 doctor（只读）。
  const sent = () => page.evaluate(() => (window as unknown as { __sent: unknown[] }).__sent);
  await expect.poll(async () => (await sent()).length, { timeout: 5000 }).toBeGreaterThan(0);
  const first = (await sent())[0] as Record<string, unknown>;
  expect(first["action"]).toBe("doctor");
  // 等待核心回执期间不派发新动作：面板如实显示"已请求核心"。
  await expect(page.getByTestId("panel-status")).toContainText("正在处理");
  await postHostMessage(page, {
        kind: "outcome",
        action: "doctor",
        status: "ok",
        response: { result: { status: "READY", supported_actions: ["doctor", "query"] } },
      });
  // 有宿主但未声明项目：问题清单不可点，点了也不派发（不猜项目身份）。
  await page.getByRole("button",{name:"更多",exact:true}).click();
  await expect(page.getByTestId("read-only-actions").getByRole("button").first()).toBeDisabled();
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  await expect(page.getByTestId("review-blocked")).toContainText("不猜项目身份");
  await page.getByRole("button", { name: "返回本次测试" }).click();

  await page.evaluate(() => {
    window.postMessage(
      { kind: "projectContext", project_id: "project-ticket", run_id: "run-1" },
      "*",
    );
  });
  await expect(page.getByTestId("project-context")).toContainText("项目编号：project-ticket");
  await page.getByRole("button",{name:"更多",exact:true}).click();

  const issues = page
    .getByTestId("read-only-actions")
    .getByRole("button", { name: "问题清单（未关闭）" });
  await expect(issues).toBeEnabled();
  await issues.click();
  const last = (await sent()).at(-1) as Record<string, unknown>;
  expect(last["kind"]).toBe("dispatch");
  expect(last["action"]).toBe("query");
  expect(last["projectId"]).toBe("project-ticket");
  expect(last["parameters"]).toEqual({ view: "OPEN", limit: 20 });

  // 真实形状的 issues.list 回执：面板只列 record_id@revision，不重算结论。
  await postHostMessage(page, {
        kind: "outcome",
        action: "query",
        status: "ok",
        response: {
          result: {
            items: [
              { aggregate_kind: "issue", record_id: "issue-1", revision: 3 },
              { aggregate_kind: "issue", record_id: "issue-2", revision: 1 },
            ],
            next_cursor: null,
          },
        },
      });
  await page.getByRole("button", { name: "问题与报告", exact: true }).click();
  const issuesBlock = page.getByTestId("review-issues");
  await expect(issuesBlock.locator("li")).toHaveCount(2);
  await expect(issuesBlock).toContainText("issue-1@3（issue）");
  await expect(issuesBlock).toContainText("issue-2@1（issue）");
  // 报告块尚未读取：如实说明，不补造。
  await expect(page.getByTestId("review-reports")).toContainText("尚未读取");
  expect(errors).toEqual([]);
});
