/** 用开发服务器的静态 mock 宿主截取单页测试单的相位与抽屉。
 *
 * 用法：先 `npm run dev`，再 `npm run screenshot -- <输出目录> [baseUrl] [width]`。
 * 默认输出到 docs/validation/2026-11-16-panel-v4，640×900。
 * 使用本机 Chrome（channel: chrome），不下载浏览器。
 */
import { mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";

import { chromium } from "playwright";

const outDir = resolve(process.argv[2] ?? "../docs/validation/2026-11-16-panel-v4");
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

  async function shot(name, fullPage = false) {
    await page.screenshot({ path: join(outDir, name), fullPage });
    console.log(`saved ${join(outDir, name)}`);
  }

  // 建议测试单：第一条候选为目标，默认全选，一个“开始测试”主按钮。
  await page.goto(`${base}/?mock=proposal`, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="test-order"][data-phase="proposal"]');
  await page.waitForSelector('[data-testid="start-test"]');
  await page.waitForSelector('[data-testid="proposal-criteria"] li');
  await shot("proposal.png");

  // 调整抽屉：验收项编辑、范围、交付说明、模板与模块建议。
  await page.locator('[data-testid="open-adjust"]').click();
  await page.waitForSelector('[data-testid="adjust-drawer"]');
  await page.waitForTimeout(200);
  await shot("adjust-drawer.png");
  await page.keyboard.press("Escape");

  // 运行相位：同一张单子顶部进度、当前用例、实时证据与取消。
  await page.goto(`${base}/?mock=running`, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="run-panel"]');
  await page.waitForSelector(".current");
  await shot("running.png");

  // 结果相位：原地变结论、覆盖、失败用例与内联证据。
  await page.goto(`${base}/?mock=result`, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="result-panel"]');
  await page.waitForSelector('[data-testid="failed-case"]');
  await shot("result.png", true);

  // 历史抽屉：时间线，点一项在当前视图回放。
  await page.locator(".order-nav .nav-btn").filter({ hasText: "历史" }).click();
  await page.waitForSelector('[data-testid="history-list"]');
  await shot("history-drawer.png");
  await page.keyboard.press("Escape");

  // 设置：从顶部状态条进入，主视图仍只有事实，JSON 收在高级配置。
  await page.getByRole("button", { name: "打开设置", exact: true }).click();
  await page.waitForSelector('[data-testid="settings-view"]');
  await shot("settings.png", true);

  // 400—900px 约束：420px 窄幅下建议测试单单列可读、主操作可达。
  const narrow = await browser.newPage({
    viewport: { width: 420, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    reducedMotion: "reduce",
  });
  await narrow.goto(`${base}/?mock=proposal`, { waitUntil: "networkidle" });
  await narrow.waitForSelector('[data-testid="start-test"]');
  await narrow.screenshot({ path: join(outDir, "proposal-420.png"), fullPage: true });
  console.log(`saved ${join(outDir, "proposal-420.png")}`);
  await narrow.close();
} finally {
  await browser.close();
}
