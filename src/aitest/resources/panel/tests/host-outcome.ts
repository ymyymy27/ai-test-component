import type { Page } from "@playwright/test";

/** Simulated host echoes the exact request tag, just as the extension does. */
export async function postHostMessage(page: Page, message: Record<string, unknown>): Promise<void> {
  await page.evaluate(payload => {
    const sent = (window as unknown as { __sent?: Array<Record<string, unknown>> }).__sent;
    const request = sent?.at(-1);
    if (payload["kind"] === "outcome" && payload["clientRequestId"] === undefined && request) {
      payload["clientRequestId"] = request["clientRequestId"];
    }
    window.postMessage(payload, "*");
  }, message);
}

/** Close the two extra reads the panel issues after its first run view. */
export async function settleInitialReads(
  page: Page,
  panelView: Record<string, unknown>,
  queryResult: Record<string, unknown> = { items: [] },
): Promise<void> {
  await page.waitForFunction(() => {
    const sent = (window as unknown as { __sent?: Array<Record<string, unknown>> }).__sent;
    const last = sent?.at(-1);
    return last?.["action"] === "read_view"
      && (last["parameters"] as Record<string, unknown> | undefined)?.["view"] === "panel";
  }, undefined, { timeout: 5000 });
  await postHostMessage(page, panelView);
  // Not every supported-action set includes query; only settle it when the
  // panel actually asks for the report list.
  try {
    await page.waitForFunction(() => {
      const sent = (window as unknown as { __sent?: Array<Record<string, unknown>> }).__sent;
      return sent?.at(-1)?.["action"] === "query";
    }, undefined, { timeout: 1000 });
  } catch {
    return;
  }
  await postHostMessage(page, {
    kind: "outcome", action: "query", status: "ok", response: { result: queryResult },
  });
}
