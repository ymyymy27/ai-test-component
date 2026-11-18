/** 用开发服务器的静态 mock 宿主截取任务台账的主要状态。
 *
 * 用法：先 `npm run dev`，再 `npm run screenshot -- <输出目录> [baseUrl] [width]`。
 * 默认输出到 tests/acceptance/p1/evidence/frontend，640×900。
 */
import { mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";

import { chromium } from "playwright";

const outDir = resolve(process.argv[2] ?? "../tests/acceptance/p1/evidence/frontend");
const base = (process.argv[3] ?? "http://localhost:5173").replace(/\/$/, "");
const widthArg = Number(process.argv[4] ?? 640);
const width = Number.isFinite(widthArg) && widthArg > 0 ? widthArg : 640;

await mkdir(outDir, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const page = await browser.newPage({
    viewport: { width, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    reducedMotion: "reduce",
  });

  async function shot(name, query, selector) {
    await page.goto(`${base}/?${query}`, { waitUntil: "networkidle" });
    await page.waitForSelector(selector, { timeout: 10_000 });
    await page.waitForTimeout(200);
    await page.screenshot({ path: join(outDir, name), fullPage: false });
    console.log(`saved ${join(outDir, name)}`);
  }

  await shot("plan.png", "mock=proposal", ".test");
  await shot("running.png", "mock=running", ".test");
  await shot("result.png", "mock=result", ".test");
  await shot("issues.png", "mock=result&view=issues", ".issues-page");
  await shot("history.png", "mock=result&view=history", ".history-page");
  await shot("settings.png", "mock=result&view=settings", ".settings");

  const narrow = await browser.newPage({
    viewport: { width: 420, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    reducedMotion: "reduce",
  });
  await narrow.goto(`${base}/?mock=result`, { waitUntil: "networkidle" });
  await narrow.waitForSelector(".test");
  await narrow.waitForTimeout(200);
  await narrow.screenshot({ path: join(outDir, "result-420.png"), fullPage: false });
  console.log(`saved ${join(outDir, "result-420.png")}`);
  await narrow.close();
} finally {
  await browser.close();
}
