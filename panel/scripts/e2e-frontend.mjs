/** 前端全功能回归：在真实浏览器中驱动 Vite 开发版与静态演示宿主。
 *
 * 前置：`cd panel && npm run dev -- --port 5174`。
 * 运行：`node scripts/e2e-frontend.mjs [baseUrl] [输出目录]`。
 * 覆盖：无宿主、无项目、建议测试单、换目标、调整抽屉、交付/模板/模块建议、
 * 开始运行、实时证据、取消、结果页、问题处置、报告导出、验收意见、历史回放、
 * 项目详情、设置、高级配置、窄幅布局与抽屉关闭。
 */
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

import { chromium } from "playwright";

const base = (process.argv[2] ?? "http://localhost:5174").replace(/\/$/, "");
const outDir = resolve(process.argv[3] ?? "../docs/validation/2026-11-17-frontend-full");
const viewport = { width: 1100, height: 900 };

await mkdir(outDir, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({
  viewport,
  deviceScaleFactor: 1,
  colorScheme: "dark",
  reducedMotion: "reduce",
  permissions: ["clipboard-read", "clipboard-write"],
});
const page = await context.newPage();
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));

let passed = 0;

function normalize(text) {
  return text.replace(/\s+/g, " ").trim();
}

async function bodyText() {
  return normalize(await page.locator("body").innerText());
}

async function expectText(text, label = text) {
  await page.waitForFunction(
    (needle) => document.body.innerText.includes(needle),
    text,
    { timeout: 10_000 },
  );
  passed += 0;
  console.log(`  ✓ ${label}`);
}

async function expectCount(selector, count, label) {
  await page.waitForFunction(
    ([sel, expected]) => document.querySelectorAll(sel).length === expected,
    [selector, count],
    { timeout: 10_000 },
  );
  console.log(`  ✓ ${label}（${count}）`);
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

async function step(name, fn) {
  console.log(`\n[${name}]`);
  try {
    await fn();
    passed += 1;
  } catch (error) {
    const text = await bodyText().catch(() => "");
    console.error(`FAIL ${name}: ${error.message}`);
    console.error(`BODY: ${text.slice(0, 800)}`);
    await page.screenshot({ path: resolve(outDir, `FAIL-${name}.png`), fullPage: true });
    throw error;
  }
}

async function screenshot(name, fullPage = false) {
  await page.screenshot({ path: resolve(outDir, name), fullPage });
  console.log(`  ▸ ${name}`);
}

async function gotoMock(mock) {
  await page.goto(`${base}/?mock=${mock}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(300);
}

async function closeDrawer() {
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => document.querySelectorAll('[role="dialog"]').length === 0);
}

async function openHistory() {
  await page.locator(".order-nav .nav-btn", { hasText: "历史" }).click();
  await page.waitForSelector('[data-testid="history-list"]');
}

async function openProject() {
  await page.locator(".order-nav .nav-btn", { hasText: "项目详情" }).click();
  await page.waitForSelector('[role="dialog"][aria-label="项目详情"]');
}

try {
  await step("无宿主：独立浏览器提示", async () => {
    await page.goto(`${base}/`, { waitUntil: "networkidle" });
    await expectText("面板没有连接到编辑器");
    await expectText("独立浏览器页面只能查看静态预览");
    await screenshot("no-host.png");
  });

  await step("无项目：选择目录后进入建议测试单", async () => {
    await gotoMock("no-project");
    await page.waitForSelector('[data-testid="no-project"]');
    await expectText("先选择一个项目文件夹");
    await page.locator('[data-testid="select-project"]').click();
    await page.locator("input#project-root").fill("E:\\work\\order-service");
    await page.locator("form.picker button[type=submit]").click();
    await page.waitForSelector('[data-testid="proposal-panel"]');
    await expectText("验证下单成功后订单只出现一次");
    await expectText("依据：order 模块近三次提交");
    await screenshot("no-project-to-proposal.png");
  });

  await step("建议测试单：默认全选、范围联动、换目标与自定义目标", async () => {
    await gotoMock("proposal");
    await page.waitForSelector('[data-testid="proposal-panel"]');
    await expectText("将验证");
    await expectCount('[data-testid="proposal-criteria"] li', 8, "验收标准条数");
    assert(
      normalize(await page.locator('[data-testid="proposal-scope-value"]').innerText()).includes("8/8"),
      "默认范围不是 8/8",
    );

    await page.locator('[role="switch"]').first().click();
    await page.waitForFunction(
      () =>
        document
          .querySelector('[data-testid="proposal-scope-value"]')
          ?.textContent?.replace(/\s+/g, "")
          .includes("7/8") === true,
    );
    console.log("  ✓ 点掉一条后范围变为 7/8");
    await page.locator('[role="switch"]').first().click();
    await page.waitForFunction(
      () =>
        document
          .querySelector('[data-testid="proposal-scope-value"]')
          ?.textContent?.replace(/\s+/g, "")
          .includes("8/8") === true,
    );

    await page.locator('[data-testid="switch-target"]').click();
    await page.waitForSelector('[role="dialog"][aria-label="换一个测试目标"]');
    await expectCount('[role="dialog"] .option', 3, "目标候选与通用兜底");
    await page.locator('[role="dialog"] .option').nth(1).click();
    await page.waitForFunction(
      () => document.querySelector(".order-head h1")?.textContent?.includes("验证并发下单时库存只扣减一次") === true,
    );
    await page.waitForTimeout(300);
    console.log("  ✓ 切到第二个候选目标");

    await page.locator('[data-testid="switch-target"]').click();
    await page.locator('[role="dialog"] .custom-link').click();
    await page.locator('[role="dialog"] input[aria-label="测试目标"]').fill("验证退款流程");
    await page.locator('[role="dialog"] button', { hasText: "用这个目标" }).click();
    await page.waitForFunction(
      () => document.querySelector(".order-head h1")?.textContent?.includes("验证退款流程") === true,
    );
    await page.waitForSelector('[data-testid="proposal-criteria"] li');
    console.log("  ✓ 自定义目标已替换");

    await page.locator('[data-testid="switch-target"]').click();
    await page.waitForSelector('[role="dialog"][aria-label="换一个测试目标"]');
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => document.querySelectorAll('[role="dialog"]').length === 0);
    console.log("  ✓ Escape 关闭目标菜单");
    await screenshot("proposal.png", true);
  });

  await step("顶部项目切换：切到另一个已登记项目并重新生成测试单", async () => {
    await gotoMock("proposal");
    await page.waitForSelector('[data-testid="proposal-panel"]');
    await page.locator(".project-select select").selectOption("proj_other");
    await page.waitForFunction(
      () => document.querySelector(".project-select select")?.value === "proj_other",
    );
    const selected = await page.locator(".project-select select").evaluate((element) => {
      const select = element;
      return select.selectedOptions[0]?.textContent?.trim() ?? "";
    });
    assert(selected === "会员中心", `项目切换后显示 ${selected}`);
    await page.waitForSelector('[data-testid="proposal-panel"]');
    console.log("  ✓ 项目选择器切换并刷新建议测试单");
  });

  await step("调整抽屉：验收项增删改、范围、交付、模板、模块建议", async () => {
    await page.locator('[data-testid="open-adjust"]').click();
    await page.waitForSelector('[data-testid="adjust-drawer"]');
    assert(
      await page.evaluate(() => document.body.style.overflow === "hidden"),
      "抽屉打开后没有锁定背景滚动",
    );
    await expectCount('[data-testid="adjust-drawer"] .item-row', 8, "抽屉内验收项");
    await expectText("未填写");
    await expectText("未应用");
    await expectText("未生成");
    await screenshot("adjust-overview.png");

    await page.locator('[data-testid="adjust-drawer"] .item-row input[type=checkbox]').first().click();
    await expectText("7 / 8 条适用");
    console.log("  ✓ 抽屉内排除一条后范围联动");

    await page.locator('[data-testid="adjust-drawer"] .item-row .link').first().click();
    const edit = page.locator('input[aria-label="编辑验收标准"]');
    await edit.fill("修改后的验收标准：订单只出现一次");
    await page.locator('[data-testid="adjust-drawer"] button', { hasText: "完成" }).click();
    await expectText("修改后的验收标准：订单只出现一次");
    console.log("  ✓ 编辑验收标准");

    await page.locator('[data-testid="adjust-drawer"] .add-link').click();
    await page.locator('input[aria-label="补充验收标准"]').fill("新增验收：退款到账");
    await page.locator('[data-testid="adjust-drawer"] button', { hasText: "添加" }).click();
    await expectCount('[data-testid="adjust-drawer"] .item-row', 9, "补充后验收项");

    await page.locator('[data-testid="adjust-drawer"] .item-row').nth(8).locator(".link").click();
    await page.locator('[data-testid="adjust-drawer"] button', { hasText: "移除" }).click();
    await expectCount('[data-testid="adjust-drawer"] .item-row', 8, "移除后验收项");
    console.log("  ✓ 添加与移除验收标准");

    await page.locator('[data-testid="delivery-fold"] > summary').click();
    await page.locator('[data-testid="delivery-draft-button"]').click();
    await expectText("运行方式");
    await expectText("python -m pytest tests/test_order.py");
    await page.locator("details.delivery-fields summary").click();
    await page.locator('input[placeholder="例如 abc123"]').fill("abc123");
    await page.locator('textarea[aria-label="已完成"]').fill("完成项 A");
    await page.locator('[data-testid="delivery-save-button"]').click();
    await expectText("交付说明已保存");
    console.log("  ✓ AI 起草并保存交付说明");

    await page.locator('[data-testid="template-fold"] > summary').click();
    await page.waitForSelector('select[aria-label="选择模板"] option[value="python-service"]', {
      state: "attached",
    });
    await page.locator('select[aria-label="选择模板"]').selectOption("python-service");
    await page.locator('[data-testid="apply-template"]').click();
    await expectText("Python服务");
    await expectText("1.0.0");
    console.log("  ✓ 应用模板并显示处置");

    await page.locator('[data-testid="module-fold"] > summary').click();
    await page.locator('[data-testid="module-suggest-button"]').click();
    await page.waitForSelector('[data-testid="module-suggestions"] li');
    await expectCount('[data-testid="module-suggestions"] li', 2, "模块建议");
    await page.locator('[data-testid="module-confirm-button"]').click();
    await expectText("已写入 2 个模块");
    console.log("  ✓ AI 建议并确认模块");

    await screenshot("adjust-drawer.png");
    await page.locator('[data-testid="adjust-save"]').click();
    await page.waitForSelector('[data-testid="proposal-panel"]');
    await page.waitForFunction(
      () =>
        document
          .querySelector('[data-testid="proposal-scope-value"]')
          ?.textContent?.replace(/\s+/g, "")
          .includes("7/8") === true,
    );
    console.log("  ✓ 保存调整后范围保持 7/8");
  });

  await step("开始运行：进度、当前用例、实时证据、取消", async () => {
    await page.locator('[data-testid="start-test"]').click();
    await page.waitForSelector('[data-testid="run-panel"]');
    await expectText("运行中");
    await expectText("4 / 6");
    await expectText("收银台地址选择");
    await expectText("每 2 秒更新");
    await screenshot("running.png");

    await page.locator('[data-testid="live-evidence"]').click();
    await page.waitForSelector('[data-testid="evidence-list"]');
    await expectCount('[data-testid="evidence-list"] li', 4, "实时证据");
    await expectText("screenshot-order-list.png");
    await expectText("missing.log");
    const missingRow = page.locator('[data-testid="evidence-list"] li', { hasText: "missing.log" });
    assert(await missingRow.locator("button", { hasText: "打开" }).isDisabled(), "缺失证据的打开按钮仍可用");
    console.log("  ✓ 缺失证据标记并禁用打开");
    await page
      .locator('[data-testid="evidence-list"] li')
      .first()
      .locator("button", { hasText: "复制引用" })
      .click();
    await expectText("已复制引用");
    console.log("  ✓ 复制证据引用");
    await screenshot("evidence-drawer.png");
    await closeDrawer();

    await page.locator(".cancel-zone button", { hasText: "取消这次运行" }).click();
    await expectText("确认取消？");
    await page.locator(".cancel-zone button", { hasText: "确认取消" }).click();
    await page.waitForSelector('[data-testid="result-panel"]');
    await expectText("本次运行已取消");
    await expectText("已取消");
    await expectText("失败");
    await page.waitForSelector('[data-testid="template-block"]');
    await page.locator('[data-testid="template-block"] summary').click();
    await expectText("只读提示：不进入必测分母");
    console.log("  ✓ 取消后进入已取消结果，模板处置保留");
  });

  await step("结果页：覆盖、证据、分析、问题、导出、意见、再测", async () => {
    await expectText("失败用例（2）");
    await expectText("还有 5 条验收项未纳入本次范围");
    await screenshot("result.png", true);

    await page.locator('[data-testid="failed-case"] .tile').first().click();
    await expectText("已请求用系统程序打开");
    console.log("  ✓ 内联证据调用宿主打开");

    await expectText("观察到的事实");
    await expectText("候选原因");
    await expectText("生产环境是否存在同样的并发窗口");

    const statusSelect = page.locator('[data-testid="issue-inline"] select').first();
    await statusSelect.selectOption("in_progress");
    await expectText("状态已更新为“处理中”");
    await page.locator('[data-testid="issue-inline"] button', { hasText: "关闭问题" }).first().click();
    await expectText("问题已关闭，关闭依据是本次运行的证据。");
    const closedSelect = page.locator('[data-testid="issue-inline"] select').first();
    assert((await closedSelect.inputValue()) === "closed", "关闭后状态选择器不是已关闭");
    assert(await closedSelect.isDisabled(), "关闭后状态选择器仍可编辑");
    console.log("  ✓ 问题状态更新与关闭");

    await page.locator('[data-testid="link-block"] summary').click();
    await expectText("首错节点");
    await page.locator(".all-cases summary").click();
    await expectCount(".all-cases .case", 6, "全部用例");
    await page.locator(".all-cases .case .case-head").first().click();
    await expectText("AssertionError");
    console.log("  ✓ 链路与全部用例展开");

    await page.locator("button", { hasText: "导出报告" }).click();
    await page.locator("#export-path").fill("C:\\reports\\run.md");
    await page.locator(".export-form button", { hasText: "导出" }).click();
    await expectText("已导出到");
    console.log("  ✓ 导出报告");

    await page.locator("#opinion-by").fill("测试员");
    await page.locator("#opinion-text").fill("已完成前端全功能回归");
    await page.locator("button", { hasText: "保存意见" }).click();
    await expectText("已记录");
    console.log("  ✓ 保存验收意见");
    await screenshot("result-actions.png", true);

    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(200);
    const sticky = await page.locator('[data-testid="rerun-button"]').boundingBox();
    assert(sticky !== null && sticky.y < 130, "滚动后“再测一次”没有保持在视口顶部");
    await screenshot("result-scrolled.png");
    console.log("  ✓ 滚动后主操作仍可见");

    await page.locator('[data-testid="rerun-button"]').click();
    await page.waitForSelector('[data-testid="run-panel"]');
    await expectText("运行中");
    console.log("  ✓ 再测一次进入运行");
  });

  await step("历史回放：切换到上一次取消的运行", async () => {
    await gotoMock("result");
    await page.waitForSelector('[data-testid="result-panel"]');
    await openHistory();
    await expectCount('[data-testid="history-list"] li', 3, "历史运行");
    await page.locator('[role="dialog"][aria-label="历史"] button', { hasText: "刷新" }).click();
    await page.waitForTimeout(300);
    await expectCount('[data-testid="history-list"] li', 3, "刷新后历史运行");
    await page.locator('[data-testid="history-list"] .run').nth(1).click();
    await page.waitForSelector('[data-testid="result-panel"]');
    await expectText("失败");
    await expectText("已取消");
    await screenshot("history-replay.png");
    console.log("  ✓ 历史回放切换运行");
  });

  await step("项目详情与设置：只读事实、自检、修复、高级配置", async () => {
    await gotoMock("result");
    await page.waitForSelector('[data-testid="result-panel"]');
    await openProject();
    await expectText("订单服务");
    await expectText("模块");
    await expectCount('[role="dialog"][aria-label="项目详情"] .module-list li', 4, "项目模块");
    await screenshot("project-drawer.png");
    await page.locator('[role="dialog"][aria-label="项目详情"] .recent-row').first().click();
    await page.waitForSelector('[data-testid="result-panel"]');
    await expectText("失败用例（2）");
    console.log("  ✓ 项目详情打开最近运行");
    await openProject();
    await page.locator('[role="dialog"][aria-label="项目详情"] button', { hasText: "打开设置" }).click();
    await page.waitForSelector('[data-testid="settings-view"]');

    await expectText("环境自检");
    await expectText("模型");
    await expectText("工作空间");
    await expectText("GitHub");
    await page.locator("button", { hasText: "重新自检" }).click();
    await page.locator("button", { hasText: "修复可自动处理项" }).click();
    await page.waitForTimeout(300);
    await screenshot("settings.png", true);

    await page.locator('[data-testid="advanced-config"] .advanced-head').click();
    await page.waitForSelector("#config-services");
    for (const [title, id] of [
      ["服务", "config-services"],
      ["链路", "config-link"],
      ["运行器", "config-runners"],
      ["证据预算", "config-evidence"],
      ["源码可运行（L1）", "config-l1"],
      ["数据清理", "config-data"],
    ]) {
      await page.locator('[role="tab"]', { hasText: title }).click();
      await page.waitForSelector(`#${id}`);
    }
    console.log("  ✓ 六个配置分组均可切换");
    await page.locator('[role="tab"]', { hasText: "源码可运行（L1）" }).click();
    await page.waitForSelector("#config-l1");
    const l1 = page.locator("#config-l1");
    assert((await l1.inputValue()).includes("compileall"), "L1 配置没有显示 build");
    await l1.fill('{"build":"python -m compileall src","entry":["app.main"],"smoke_command":"python -m pytest -q"}');
    await page.locator("button", { hasText: "保存项目配置" }).click();
    await expectText("项目配置已保存。");
    console.log("  ✓ 高级配置保存");

    await l1.fill("{bad");
    await page.locator("button", { hasText: "保存项目配置" }).click();
    await expectText("无法解析");
    console.log("  ✓ 非法 JSON 被拦截");
    await l1.fill('{"build":"python -m compileall src","entry":["app.main"],"smoke_command":"python -m pytest -q"}');
    await page.locator("button", { hasText: "保存项目配置" }).click();
    await expectText("项目配置已保存。");

    await page.locator("button", { hasText: "返回测试单" }).click();
    await page.waitForSelector('[data-testid="result-panel"]');
    console.log("  ✓ 返回测试单");
  });

  await step("通过运行：项目检查与“不需要分析”文案", async () => {
    await gotoMock("passed");
    await page.waitForSelector('[data-testid="result-panel"]');
    await expectText("通过");
    await expectText("范围覆盖");
    await expectText("3/8");
    await expectText("本次没有失败，不需要生成分析。");
    await screenshot("passed-result.png", true);
    console.log("  ✓ 通过运行不再显示分析未生成");
  });

  await step("结果页次级操作：只重跑这条与新目标", async () => {
    await gotoMock("result");
    await page.waitForSelector('[data-testid="result-panel"]');
    await page.locator('[data-testid="failed-case"] button', { hasText: "只重跑这条" }).first().click();
    await page.waitForSelector('[data-testid="run-panel"]');
    await expectText("运行中");
    console.log("  ✓ 只重跑单条失败用例");

    await gotoMock("result");
    await page.waitForSelector('[data-testid="result-panel"]');
    await page.locator('[data-testid="new-target"]').click();
    await page.waitForSelector('[data-testid="proposal-panel"]');
    await expectText("验证下单成功后订单只出现一次");
    console.log("  ✓ 新目标回到建议测试单");
  });

  await step("窄幅布局：420px 主操作可达且无横向溢出", async () => {
    const narrow = await context.newPage();
    await narrow.setViewportSize({ width: 420, height: 900 });
    await narrow.goto(`${base}/?mock=proposal`, { waitUntil: "networkidle" });
    await narrow.waitForSelector('[data-testid="start-test"]');
    const metrics = await narrow.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      start: document.querySelector('[data-testid="start-test"]')?.getBoundingClientRect().toJSON(),
    }));
    assert(metrics.scrollWidth <= metrics.innerWidth + 1, `窄幅横向溢出：${metrics.scrollWidth} > ${metrics.innerWidth}`);
    assert(metrics.start && metrics.start.right <= metrics.innerWidth + 1, "开始测试按钮超出视口");
    await narrow.screenshot({ path: resolve(outDir, "narrow-420.png"), fullPage: true });

    await narrow.goto(`${base}/?mock=result`, { waitUntil: "networkidle" });
    await narrow.waitForSelector('[data-testid="result-panel"]');
    const resultMetrics = await narrow.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    assert(
      resultMetrics.scrollWidth <= resultMetrics.innerWidth + 1,
      `窄幅结果页横向溢出：${resultMetrics.scrollWidth} > ${resultMetrics.innerWidth}`,
    );
    await narrow.screenshot({ path: resolve(outDir, "narrow-result-420.png"), fullPage: true });

    await narrow.goto(`${base}/?mock=settings`, { waitUntil: "networkidle" });
    await narrow.waitForSelector('[data-testid="settings-view"]');
    const settingsMetrics = await narrow.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    assert(
      settingsMetrics.scrollWidth <= settingsMetrics.innerWidth + 1,
      `窄幅设置页横向溢出：${settingsMetrics.scrollWidth} > ${settingsMetrics.innerWidth}`,
    );
    await narrow.screenshot({ path: resolve(outDir, "narrow-settings-420.png"), fullPage: true });
    await narrow.close();
    console.log("  ✓ 420px 建议单、结果页与设置页均无横向溢出");
  });

  await step("抽屉关闭：Escape 解锁背景并可继续操作", async () => {
    await gotoMock("proposal");
    await page.waitForSelector('[data-testid="proposal-panel"]');
    await page.locator('[data-testid="open-adjust"]').click();
    await page.waitForSelector('[data-testid="adjust-drawer"]');
    await closeDrawer();
    assert(
      await page.evaluate(() => document.body.style.overflow === ""),
      "抽屉关闭后背景滚动仍被锁定",
    );
    await page.locator('[data-testid="switch-target"]').click();
    await page.waitForSelector('[role="dialog"][aria-label="换一个测试目标"]');
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => document.querySelectorAll('[role="dialog"]').length === 0);
    console.log("  ✓ 抽屉与目标菜单均可 Escape 关闭");
  });

  if (pageErrors.length > 0) {
    throw new Error(`浏览器运行时错误：${pageErrors.join(" | ")}`);
  }

  console.log(`\n全部通过：${passed} 组前端功能检查`);
} finally {
  await context.close();
  await browser.close();
}
