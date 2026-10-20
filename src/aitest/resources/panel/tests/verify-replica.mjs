import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { Window } from "happy-dom";

const bundle = await readFile(resolve("dist/index.js"), "utf8");
const cases = [
  ["prepare", "工单状态修改", "准备", "ticket_service.py"],
  ["running", "核验最终持久化", "运行中", "测试 · 工单状态修改"],
  ["driver", "计划与运行中修订", "运行中", "驱动与运行修订"],
  ["environment", "环境与执行来源", "准备", "环境与来源"],
  ["issue", "问题草稿 · IS-017", "准备", "问题 · IS-017"],
  ["ai-records", "AI 生成记录", "准备", "AI 生成记录"],
  ["records", "工单服务 · 报告", "本轮结果", "报告查询"],
  ["regression", "变更与回归建议", "准备", "变更与回归"],
  ["result", "覆盖与结论", "本轮结果", "报告 · 修订3"],
  ["single", "模型 API 设置", "模型 API 设置", "ticket_service.py"],
];

function text(element) {
  return (element?.textContent ?? "").replace(/\s+/g, " ").trim();
}

function pattern(value) {
  return new RegExp(value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
}

for (const [view, expected, sidebarTail, tabOrSource] of cases) {
  const window = new Window({ url: `http://localhost/?view=${view}` });
  window.__AITEST_DEMO__ = true;
  const { document } = window;
  document.body.innerHTML = `<aitest-mockup view="${view}"></aitest-mockup>`;
  window.eval(bundle);
  await new Promise((resolveTick) => setTimeout(resolveTick, 0));

  const mockup = document.querySelector("aitest-mockup");
  assert.ok(mockup?.shadowRoot, `${view}: mockup shadow root`);
  const mock = text(mockup.shadowRoot);
  const panel = mockup.shadowRoot.querySelector("ai-test-panel");
  assert.ok(panel?.shadowRoot, `${view}: panel shadow`);
  const detail = mockup.shadowRoot.querySelector("ai-test-detail");
  const combined = `${mock} ${text(panel.shadowRoot)} ${text(detail?.shadowRoot)}`;

  assert.match(combined, pattern(expected), `${view}: expected content`);
  assert.match(text(panel.shadowRoot), pattern(sidebarTail), `${view}: sidebar state`);
  assert.match(mock, pattern(tabOrSource), `${view}: detail or source tab`);
  assert.match(mock, /交互草图 · 示例数据/);
  assert.match(mock, /PS ticket-service> git status --short/);
  assert.match(mock, /Python 3\.13/);
  assert.match(text(panel.shadowRoot), /AI 测试/);

  if (view !== "prepare" && view !== "single") {
    assert.ok(detail?.shadowRoot, `${view}: detail shadow`);
    assert.match(text(detail.shadowRoot), /AI 测试 › 工单服务/);
  }
}

// Every visibly interactive control in the reference shell must respond.
{
  const window = new Window({ url: "http://localhost/?view=prepare" });
  window.__AITEST_DEMO__ = true;
  const { document } = window;
  document.body.innerHTML = '<aitest-mockup view="prepare"></aitest-mockup>';
  window.eval(bundle);
  await new Promise((resolveTick) => setTimeout(resolveTick, 0));
  const mockup = document.querySelector("aitest-mockup");
  const panel = mockup.shadowRoot.querySelector("ai-test-panel");
  const sidebar = panel.shadowRoot;

  sidebar.querySelector("[data-settings]").click();
  assert.match(text(sidebar), /模型 API 设置/);
  sidebar.querySelector("[data-back]").click();
  assert.match(text(sidebar), /快速检查 · 计划驱动/);

  sidebar.querySelector("[data-project]").click();
  assert.equal(sidebar.querySelector("[data-project-menu]").hidden, false);
  sidebar.querySelector("[data-close-menu]").click();
  assert.equal(sidebar.querySelector("[data-project-menu]").hidden, true);

  sidebar.querySelector("[data-more]").click();
  assert.equal(sidebar.querySelector("[data-more-menu]").hidden, false);
  sidebar.querySelector("[data-close-menu]").click();
  assert.equal(sidebar.querySelector("[data-more-menu]").hidden, true);

  const current = sidebar.querySelector('[data-toggle="current"]');
  current.click();
  assert.equal(sidebar.querySelector('[data-body="current"]').hidden, true);
  current.click();
  assert.equal(sidebar.querySelector('[data-body="current"]').hidden, false);

  const caseButton = sidebar.querySelector('[data-case="L3 最终持久化"]');
  caseButton.click();
  assert.equal(caseButton.classList.contains("selected"), true);

  mockup.shadowRoot.querySelector('[data-menu="file"]').click();
  assert.match(text(mockup.shadowRoot), /新建文本文件/);
  mockup.shadowRoot.querySelector('[data-menu-action="new"]').click();
  assert.match(text(mockup.shadowRoot), /已新建文本文件/);

  mockup.shadowRoot.querySelector('[data-menu="edit"]').click();
  assert.match(text(mockup.shadowRoot), /撤销/);
  mockup.shadowRoot.querySelector('[data-menu-action="copy"]').click();
  assert.match(text(mockup.shadowRoot), /已复制当前选择/);

  mockup.shadowRoot.querySelector('[data-menu="view"]').click();
  assert.match(text(mockup.shadowRoot), /切换 AI 测试面板/);
  mockup.shadowRoot.querySelector('[data-menu-action="source"]').click();
  assert.match(text(mockup.shadowRoot), /已切回源码编辑器/);

  mockup.shadowRoot.querySelector('[data-menu="terminal"]').click();
  assert.match(text(mockup.shadowRoot), /清空终端/);
  mockup.shadowRoot.querySelector('[data-menu-action="clear-terminal"]').click();
  assert.match(text(mockup.shadowRoot), /终端已清空/);

  mockup.shadowRoot.querySelector('[data-activity="files"]').click();
  assert.match(text(mockup.shadowRoot), /资源管理器/);
  mockup.shadowRoot.querySelector('[data-activity="aitest"]').click();
  assert.match(text(mockup.shadowRoot.querySelector("ai-test-panel").shadowRoot), /工单状态修改/);
}

console.log("PASS 10 reference states and all visible sidebar controls");