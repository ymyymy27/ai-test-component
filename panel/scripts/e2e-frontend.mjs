/** 任务台账的前端端到端检查：用开发服务器 + 静态 mock 宿主。
 *
 * 用法：先 `npm run dev -- --port 5174`，再 `npm run test:e2e -- [baseUrl] [输出目录]`。
 */
import { mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";

import { chromium } from "playwright";

const base = (process.argv[2] ?? "http://localhost:5174").replace(/\/$/, "");
const outDir = resolve(process.argv[3] ?? "test-results");
await mkdir(outDir, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: true });
const errors = [];

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

try {
  const page = await browser.newPage({
    viewport: { width: 640, height: 900 },
    colorScheme: "dark",
    reducedMotion: "reduce",
  });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto(base, { waitUntil: "networkidle" });
  await page.waitForSelector(".empty");
  assert((await page.textContent("body"))?.includes("面板没有连接到编辑器"), "无宿主时应说明未连接");

  await page.goto(`${base}/?mock=proposal`, { waitUntil: "networkidle" });
  await page.waitForSelector(".test");
  assert((await page.textContent("body"))?.includes("执行前确认"), "计划态应有执行前确认");
  assert((await page.textContent("body"))?.includes("这些验收项不计入本次结论"), "应显示未选项说明");

  const checkbox = page.locator('.confirm input[type="checkbox"]');
  await checkbox.check();
  await page.getByRole("button", { name: "开始测试", exact: true }).click();
  await page.waitForSelector(".running", { timeout: 5000 });
  assert((await page.textContent("body"))?.includes("停止"), "运行中应有停止");
  await page.getByRole("button", { name: "停止", exact: true }).click();
  await page.waitForTimeout(2400);
  assert((await page.textContent("body"))?.includes("已取消"), "停止后应显示已取消");

  await page.goto(`${base}/?mock=result`, { waitUntil: "networkidle" });
  await page.waitForSelector(".test");
  assert((await page.textContent("body"))?.includes("没通过"), "结果页应显示结论");
  assert((await page.textContent("body"))?.includes("解释 · 不改变结论"), "失败应带解释脚注");

  await page.getByRole("button", { name: "查看计划", exact: true }).click();
  await page.waitForSelector(".ui-sheet");
  assert((await page.textContent("body"))?.includes("任务与计划"), "计划文档应打开");
  await page.getByText("返回测试", { exact: true }).click();

  await page.getByRole("button", { name: "报告", exact: true }).click();
  await page.waitForSelector(".ui-sheet");
  assert((await page.textContent("body"))?.includes("验收意见"), "报告应含验收意见");
  await page.screenshot({ path: join(outDir, "report.png") });
  await page.getByText("返回测试", { exact: true }).click();

  await page.getByRole("button", { name: "问题", exact: true }).click();
  await page.waitForSelector(".issues-page");
  await page.locator(".issue-row").first().click();
  await page.waitForSelector(".detail");
  assert((await page.textContent("body"))?.includes("发生记录"), "问题详情应含发生记录");
  await page.screenshot({ path: join(outDir, "issue-detail.png") });

  await page.getByRole("button", { name: "历史", exact: true }).click();
  await page.waitForSelector(".history-page");
  assert((await page.textContent("body"))?.includes("计划 1"), "历史应显示计划");

  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.waitForSelector(".settings");
  assert((await page.textContent("body"))?.includes("环境自检"), "设置应显示环境自检");

  const narrow = await browser.newPage({
    viewport: { width: 420, height: 900 },
    colorScheme: "dark",
    reducedMotion: "reduce",
  });
  await narrow.goto(`${base}/?mock=result`, { waitUntil: "networkidle" });
  await narrow.waitForSelector(".test");
  const overflow = await narrow.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  assert(!overflow, "420px 不应横向溢出");
  await narrow.screenshot({ path: join(outDir, "result-420.png") });
  await narrow.close();

  assert(errors.length === 0, `页面错误：${errors.join(" | ")}`);
  console.log("e2e ok");
} finally {
  await browser.close();
}
