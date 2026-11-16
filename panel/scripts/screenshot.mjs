/** 用开发服务器的静态 mock 宿主截取 640×900 面板截图。
 *
 * 用法：先 `npm run dev`，再 `npm run screenshot -- <输出路径> [url]`。
 * 使用本机 Chrome（channel: chrome），不下载浏览器。
 */
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";

import { chromium } from "playwright";

const output = resolve(process.argv[2] ?? "../docs/validation/2026-11-16-panel-v2/panel.png");
const url = process.argv[3] ?? "http://localhost:5173/?mock=result";

const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const page = await browser.newPage({
    viewport: { width: 640, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
  });
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="result-view"]', { timeout: 15000 });
  await mkdir(dirname(output), { recursive: true });
  await page.screenshot({ path: output, fullPage: false });
  console.log(`saved ${output}`);
} finally {
  await browser.close();
}
