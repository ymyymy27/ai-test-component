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
