import { test, expect } from "@playwright/test";
import { resolve } from "node:path";
import { postHostMessage } from "./host-outcome";

/** 已提交事件增量续读（设计 §12「已提交事件增量刷新」）：游标只前移、拒绝不静默重置。 */
async function mount(page: import("@playwright/test").Page): Promise<void> {
  await page.setContent('<html lang="zh-CN"><body><ai-test-panel></ai-test-panel></body></html>');
  await page.evaluate(() => {
    const scope = window as unknown as { __sent: unknown[]; acquireVsCodeApi: () => unknown };
    scope.__sent = [];
    scope.acquireVsCodeApi = () => ({
      postMessage: (message: unknown) => {
        scope.__sent.push(message);
      },
    });
  });
  await page.addScriptTag({ path: resolve("dist/index.js") });
}

function send(page: import("@playwright/test").Page, message: Record<string, unknown>) {
  return postHostMessage(page, message);
}

function sent(page: import("@playwright/test").Page): Promise<Array<Record<string, unknown>>> {
  return page.evaluate(
    () => (window as unknown as { __sent: Array<Record<string, unknown>> }).__sent,
  );
}

function eventOutcome(next: string | null, sequences: number[]) {
  return {
    kind: "outcome",
    action: "events",
    status: "ok",
    response: {
      result: {
        status: "ok",
        events: sequences.map((sequence, index) => ({
          event_sequence: sequence,
          commit_sequence: sequence + 100,
          event_type: "record_written",
          record_id: `record-${sequence}`,
          revision: index + 1,
        })),
        next_cursor: next,
      },
    },
  };
}

test("event stream threads the cursor and appends only new committed events", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mount(page);
  await send(page, {
    kind: "outcome",
    action: "doctor",
    status: "ok",
    response: { result: { status: "READY", supported_actions: ["doctor", "events"] } },
  });

  // 首次：无游标 → 不带 cursor 派发。
  await page.getByTestId("read-only-actions").getByRole("button", { name: "事件续读" }).click();
  const first = (await sent(page)).at(-1)!;
  expect(first["action"]).toBe("events");
  expect(first["parameters"]).toEqual({ limit: 20 });
  await expect(page.getByTestId("event-cursor")).toContainText("尚未取得游标");

  await send(page, eventOutcome("cursor-1", [1, 2]));
  const stream = page.getByTestId("event-stream");
  await expect(stream.locator("li")).toHaveCount(2);
  await expect(stream).toContainText("#1 · 提交 101 · record_written · record-1@1");
  await expect(stream).toContainText("#2 · 提交 102 · record_written · record-2@2");
  await expect(page.getByTestId("event-cursor")).toContainText("当前游标：cursor-1");
  await expect(page.getByTestId("event-note")).toContainText("已追加 2 条事件");

  // 二次：带上上次回执给出的游标（不重头读），只追加新事件。
  await page.getByTestId("read-only-actions").getByRole("button", { name: "事件续读" }).click();
  const second = (await sent(page)).at(-1)!;
  expect(second["parameters"]).toEqual({ limit: 20, cursor: "cursor-1" });

  await send(page, eventOutcome("cursor-2", [3]));
  await expect(stream.locator("li")).toHaveCount(3);
  await expect(page.getByTestId("event-cursor")).toContainText("当前游标：cursor-2");

  // 空回执：如实说明没有新事件，且不动既有事件行。
  await page.getByTestId("read-only-actions").getByRole("button", { name: "事件续读" }).click();
  await send(page, {
    kind: "outcome",
    action: "events",
    status: "ok",
    response: { result: { status: "ok", events: [], next_cursor: null } },
  });
  await expect(page.getByTestId("event-note")).toContainText("没有新事件");
  await expect(stream.locator("li")).toHaveCount(3);
  expect(errors).toEqual([]);
});

test("a refused cursor keeps the previous cursor and never rewinds silently", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mount(page);
  await send(page, {
    kind: "outcome",
    action: "doctor",
    status: "ok",
    response: { result: { status: "READY", supported_actions: ["doctor", "events"] } },
  });
  await page.getByTestId("read-only-actions").getByRole("button", { name: "事件续读" }).click();
  await send(page, eventOutcome("cursor-9", [7]));
  await expect(page.getByTestId("event-cursor")).toContainText("当前游标：cursor-9");
  await expect(page.getByTestId("event-stream").locator("li")).toHaveCount(1);

  // 游标非法：显示代码原文、保持原游标与既有事件行。
  await page.getByTestId("read-only-actions").getByRole("button", { name: "事件续读" }).click();
  await send(page, {
    kind: "outcome",
    action: "events",
    status: "refused",
    code: "invalid_cursor",
    message: "游标无法解码",
  });
  await expect(page.getByTestId("event-cursor")).toContainText("当前游标：cursor-9");
  await expect(page.getByTestId("event-stream")).toContainText("核心未通过 · invalid_cursor");
  await expect(page.getByTestId("event-stream")).toContainText("游标与既有事件行保持不变");
  await expect(page.getByTestId("event-stream").locator("li")).toHaveCount(1);

  // 真实 `events` 形状：`invalid_cursor` 在 result.status 里（宿主层 status 仍是 ok）。
  await page.getByTestId("read-only-actions").getByRole("button", { name: "事件续读" }).click();
  await send(page, {
    kind: "outcome",
    action: "events",
    status: "ok",
    response: { result: { status: "invalid_cursor", events: [], next_cursor: null } },
  });
  await expect(page.getByTestId("event-stream")).toContainText("核心未通过 · invalid_cursor");
  await expect(page.getByTestId("event-cursor")).toContainText("当前游标：cursor-9");
  await expect(page.getByTestId("event-stream").locator("li")).toHaveCount(1);

  // 再读仍带原游标（没有被重置成"从头开始"）。
  await page.getByTestId("read-only-actions").getByRole("button", { name: "事件续读" }).click();
  const last = (await sent(page)).at(-1)!;
  expect(last["parameters"]).toEqual({ limit: 20, cursor: "cursor-9" });
  expect(errors).toEqual([]);
});
