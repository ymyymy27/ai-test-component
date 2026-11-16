/** 用开发服务器的静态 mock 宿主截取面板主路径截图。
 *
 * 用法：先 `npm run dev`，再 `npm run screenshot -- <输出目录> [baseUrl] [width]`。
 * 默认输出到 docs/validation/2026-11-16-panel-v3，640×900。
 * 使用本机 Chrome（channel: chrome），不下载浏览器。
 */
import { mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";

import { chromium } from "playwright";

const outDir = resolve(process.argv[2] ?? "../docs/validation/2026-11-16-panel-v3");
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

  // 项目页：先生成交付摘要，让 chips 出现在主视图。
  await page.goto(`${base}/?mock=project`, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="project-overview"]');
  const draftButton = page.locator('[data-testid="delivery-draft-button"]');
  if ((await draftButton.count()) > 0) {
    await draftButton.click();
    await page.waitForSelector('[data-testid="delivery-edit"]');
  }
  await shot("project.png");

  // 交付说明抽屉：主视图之外才出现字段编辑。
  const editButton = page.locator('[data-testid="delivery-edit"]');
  if ((await editButton.count()) > 0) {
    await editButton.click();
    await page.waitForSelector('[role="dialog"]');
    await shot("project-drawer.png");
    await page.keyboard.press("Escape");
  }

  // 向导第 1 步：候选卡片 + 一行“或自己写一句”。
  await page.getByRole("button", { name: "新建测试" }).click();
  await page.waitForSelector('[data-testid="wizard-step-1"]');
  await shot("wizard-step1.png");

  // 第 2 步：点候选卡片自动进入，验收项为开关列表。
  await page.locator('[data-testid="candidate"]').first().click();
  await page.waitForSelector('[data-testid="wizard-step-2"]');
  await page.waitForSelector('[data-testid="acceptance-list"]');
  await shot("wizard-step2.png");

  // 第 4 步：范围默认全部适用，直接下一步到确认页。
  await page.locator('[data-testid="wizard-next"]').click();
  await page.waitForSelector('[data-testid="wizard-step-3"]');
  await page.locator('[data-testid="wizard-next"]').click();
  await page.waitForSelector('[data-testid="wizard-step-4"]');
  await page.waitForSelector('[data-testid="start-run"]');
  await shot("wizard-step4.png");

  // 设置页主视图：没有 JSON 文本框。
  await page.goto(`${base}/?mock=settings`, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="settings-view"]');
  await shot("settings.png", true);

  // 高级配置：一次只显示一个 JSON 分组。
  await page.locator('[data-testid="advanced-config"] .advanced-head').click();
  await page.waitForSelector("textarea#config-services");
  await page.locator('[data-testid="advanced-config"]').scrollIntoViewIfNeeded();
  await page.waitForTimeout(150);
  await shot("settings-advanced.png");

  // 400px 窄幅：确认列布局与操作可达。
  const narrow = await browser.newPage({
    viewport: { width: 420, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    reducedMotion: "reduce",
  });
  await narrow.goto(`${base}/?mock=project`, { waitUntil: "networkidle" });
  await narrow.waitForSelector('[data-testid="project-overview"]');
  await narrow.screenshot({ path: join(outDir, "project-420.png"), fullPage: true });
  console.log(`saved ${join(outDir, "project-420.png")}`);
  await narrow.close();
} finally {
  await browser.close();
}
