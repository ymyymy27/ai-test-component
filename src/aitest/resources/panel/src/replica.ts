/** Pixel-oriented reproduction of the ten editor panel reference images.
 * The fixtures are explicitly demo-only; production hosts must project the same
 * view fields from core DTOs. Write actions never bypass a controlled entry. */
import { sidebarCss, detailCss, mockupCss } from "./replica-styles";

type SidebarState = "prepare" | "running" | "paused" | "result" | "settings";
type DetailView =
  | "ai-records" | "driver" | "environment" | "issue" | "records"
  | "regression" | "result" | "running" | "source" | "empty";

type HostApi = { postMessage(message: unknown): void };
type HostOutcome = {
  kind: "outcome";
  action: string | null;
  status: "ok" | "refused" | "unavailable";
  code?: string;
  message?: string;
  response?: Record<string, unknown>;
};

const ic = {
  settings: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M3 5h10M3 8h10M3 11h10"/><circle cx="6" cy="5" r="1.4" fill="#22252b"/><circle cx="10" cy="8" r="1.4" fill="#22252b"/><circle cx="5" cy="11" r="1.4" fill="#22252b"/></svg>`,
  more: `<svg viewBox="0 0 16 16" fill="currentColor"><circle cx="3" cy="8" r="1"/><circle cx="8" cy="8" r="1"/><circle cx="13" cy="8" r="1"/></svg>`,
  folder: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M1.5 4.2h5l1.1 1.4h7v7.1h-13z"/><path d="M1.5 4.2v8.5h13"/></svg>`,
  spark: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.1"><path d="m8 1.5 1.1 3.2L12.5 6l-3.4 1.2L8 10.5 6.9 7.2 3.5 6l3.4-1.3z"/><path d="m12.5 10 .6 1.7 1.9.7-1.9.7-.6 1.7-.6-1.7-1.9-.7 1.9-.7z"/></svg>`,
  info: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.1"><circle cx="8" cy="8" r="6"/><path d="M8 7v4M8 5.2v.1"/></svg>`,
  shield: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M8 1.8 13 4v4.1c0 3.1-2.2 5.2-5 6.1-2.8-.9-5-3-5-6.1V4z"/><path d="m5.7 8 1.5 1.5 3-3"/></svg>`,
  warning: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><circle cx="8" cy="8" r="6"/><path d="M8 4.8v4.3M8 11.8v.2"/></svg>`,
  pause: `<svg viewBox="0 0 16 16" fill="currentColor"><rect x="4" y="3" width="2.2" height="10" rx=".3"/><rect x="9.8" y="3" width="2.2" height="10" rx=".3"/></svg>`,
  play: `<svg viewBox="0 0 16 16" fill="currentColor"><path d="m5 3 7 5-7 5z"/></svg>`,
  stop: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3.5" y="3.5" width="9" height="9" rx="1"/></svg>`,
  arrow: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 8h9M9 5l3 3-3 3"/></svg>`,
  export: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M8 10V2M5 5l3-3 3 3M3 9v4h10V9"/></svg>`,
  report: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M3 1.8h7l3 3v9.4H3z"/><path d="M10 1.8v3h3M5.5 8h5M5.5 10.5h5"/></svg>`,
  file: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.1"><path d="M3.5 1.5h6l3 3v10h-9z"/><path d="M9.5 1.5v3h3"/></svg>`,
  flask: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.1"><path d="M6 1.5h4M6.6 1.5v4.2l-3.4 6a1.6 1.6 0 0 0 1.4 2.3h6.8a1.6 1.6 0 0 0 1.4-2.3l-3.4-6V1.5"/><path d="M5 10h6"/></svg>`,
  chevron: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><path d="m5 6 3 3 3-3"/></svg>`,
  back: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M13 8H3M7 4 3 8l4 4"/></svg>`,
};

const top = `
<div class="top">
  <div class="brand-row"><button class="brand-title" data-home>AI 测试</button><span class="brand-spacer"></span>
    <button class="icon-btn" data-settings aria-label="设置">${ic.settings}</button>
    <button class="icon-btn" data-more aria-label="更多">${ic.more}</button>
  </div>
  <div class="project-row" data-project>${ic.folder}<span class="project-name">工单服务</span><span class="caret">⌄</span><span class="project-tail">本地项目</span></div>
  <div class="model-row" data-settings>${ic.spark}<span class="model-name">DeepSeek · 插件 API</span><span class="model-tail">配置</span></div>
  <div class="popover more-popover" data-more-menu hidden>
    <button data-settings>模型 API 设置</button><button data-open="ai-records">AI 生成记录</button><button data-refresh>刷新面板</button><button data-close-menu>关闭菜单</button>
  </div>
  <div class="popover project-popover" data-project-menu hidden>
    <div class="popover-head">当前项目</div><button class="selected">工单服务 <span>✓</span></button><button data-open="records">管理项目显示</button><button data-close-menu>关闭菜单</button>
  </div>
</div>`;

const footer = `<div class="footer" data-testid="sidebar-footer"><span class="dot"></span><span data-core-label>核心已连接 · 模型 API 已配置</span></div>`;

const tree = `
<div class="tree-root"><span class="chevron">⌄</span><span>工单状态修改</span><span class="tree-count">12 项</span></div>
<button class="tree-item" data-case="L1 入口加载"><span class="tree-circle"></span><span>L1 入口加载</span></button>
<button class="tree-item" data-case="L2 状态修改接口"><span class="tree-circle"></span><span>L2 状态修改接口</span></button>
<button class="tree-item" data-case="L2 查询与分页"><span class="tree-circle"></span><span>L2 查询与分页</span></button>
<button class="tree-item" data-case="L2 异常状态拒绝"><span class="tree-circle"></span><span>L2 异常状态拒绝</span></button>
<button class="tree-item" data-case="L3 最终持久化"><span class="tree-circle"></span><span>L3 最终持久化</span></button>
<button class="link block" data-open="plan">查看全部用例</button>`;

function section(id: string, title: string, tail: string, body: string, extra = ""): string {
  return `<section class="section ${id} ${extra}" data-section="${id}">
    <button class="section-head" data-toggle="${id}" aria-expanded="true"><span class="section-chevron">⌄</span><span class="section-title">${title}</span><span class="section-tail">${tail}</span></button>
    <div class="body" data-body="${id}">${body}</div>
  </section>`;
}

function planSection(accent = false, running = false, result = false): string {
  let rows = tree;
  if (running) {
    rows = `
      <div class="tree-root"><span class="chevron">⌄</span><span>工单状态修改</span><span class="tree-count">12 项</span></div>
      <div class="tree-item done"><span class="tree-circle"></span><span>L1 入口加载</span></div>
      <div class="tree-item done"><span class="tree-circle"></span><span>L2 状态修改接口</span></div>
      <div class="tree-item"><span class="tree-circle"></span><span>L2 查询与分页</span><span class="tree-state">依据过期</span></div>
      <div class="tree-item"><span class="tree-circle"></span><span>L2 异常状态拒绝</span><span class="tree-state">依据过期</span></div>
      <div class="tree-item"><span class="tree-circle"></span><span>L3 最终持久化</span><span class="tree-state">依据过期</span></div>
      <button class="link block" data-open="plan">查看全部用例</button>`;
  } else if (result) {
    rows = `
      <div class="tree-root"><span class="chevron">⌄</span><span>工单状态修改</span><span class="tree-count">12 项</span></div>
      <div class="tree-item done"><span class="tree-circle"></span><span>L1 入口加载</span></div>
      <div class="tree-item done"><span class="tree-circle"></span><span>L2 状态修改接口</span></div>
      <div class="tree-item done"><span class="tree-circle"></span><span>L2 查询与分页</span></div>
      <div class="tree-item done"><span class="tree-circle"></span><span>L2 异常状态拒绝</span></div>
      <div class="tree-item"><span class="tree-circle"></span><span>L3 最终持久化</span><span class="tree-state">待核验</span></div>
      <button class="link block" data-open="plan">查看全部用例</button>`;
  }
  return section("plan", "测试计划", `<span class="success">已发布</span>`, `<div class="plan-top"><strong>工单流程 · 修订3</strong><button class="link" data-open="plan">编辑</button></div>${rows}`, accent ? "accent" : "");
}

function pendingSection(kind: "prepare" | "running" | "paused" | "result"): string {
  if (kind === "prepare") {
    return section("pending", "待处理", "执行前确认", `<div class="pending-item">${ic.shield}<div class="pending-main"><div class="pending-title">核对副作用动作与目标</div><div class="pending-detail">逐项授权，不自动执行</div></div></div>`);
  }
  if (kind === "running") {
    return section("pending", "待处理", "待人工", `<div class="pending-item">${ic.pause}<div class="pending-main"><div class="pending-title">最终持久化核验</div><div class="pending-detail">记录实际操作并附加证据</div><div class="pending-detail">阻塞 1 · 未执行 3 · 跳过 0</div><div class="pending-detail">缺证据 1 · 真实性未知 1</div><button class="link" data-open="running">查看缺口详情</button></div></div>`, "accent");
  }
  if (kind === "paused") {
    return section("pending", "待处理", "待人工", `<div class="pending-item">${ic.warning}<div class="pending-main"><div class="pending-title">查询与分页 / 持久化核验</div><div class="pending-detail">受影响的依据过期，原通过仅作历史。</div><button class="link" data-open="driver">核对修订与需重跑范围</button></div></div>`, "accent");
  }
  return section("pending", "待处理", "证据缺口", `<div class="pending-item">${ic.report}<div class="pending-main"><div class="pending-title">补齐持久化核验</div><div class="pending-detail">业务对象 ticket-042</div><div class="pending-detail">阻塞 1 · 未执行 1 · 跳过 0</div><div class="pending-detail">缺证据 1 · 真实性未知 0</div><button class="link" data-open="issue">查看全部问题</button></div></div>`);
}

function prepareSidebar(): string {
  const body = `
    <div class="test-title">工单状态修改</div><div class="test-meta">快速检查 · 计划驱动</div>
    <div class="button-row"><button class="btn" data-open="regression">快速检查 <span>⌄</span></button><button class="btn primary" data-action="run-quick">${ic.play} 运行快速检查</button></div>
    <button class="link block" data-open="environment">查看准备详情</button>
    <button class="link block" data-open="plan">用 AI 准备 / 更新测试草稿</button>
    <div class="divider"></div>
    <div class="kv"><span class="kv-label">计划</span><span class="kv-value">已发布 · 修订3</span></div>
    <div class="kv"><span class="kv-label">本次</span><span class="kv-value">L1 + 关键 L2 + 独立核验</span></div>
    <div class="kv"><span class="kv-label">依赖环境</span><span class="kv-value">Python 3.13 · venv</span></div>
    <div class="kv"><span class="kv-label">数据/目标</span><span class="kv-value">复位已登记 · 部署待核实</span></div>
    <div class="kv"><span class="kv-label">源码</span><span class="kv-value">abc1234 · 2 个未提交文件</span></div>
    <div class="note-row">${ic.info}<span>当前目录尚未重新核实</span></div>
    <div class="note-row">${ic.info}<span>仅本轮选定范围，未打分</span></div>`;
  return `<div class="sidebar">${top}<div class="content">${section("current", "当前测试", "准备", body)}${pendingSection("prepare")}${planSection()}${recordsCollapsed()}</div>${footer}</div>`;
}

function runningSidebar(paused: boolean): string {
  const status = paused ? `<div class="status-line">${ic.warning} 已暂停 · 依据过期</div>` : `<div class="status-line">${ic.info} 等待人工核验</div>`;
  const buttons = paused
    ? `<button class="btn primary" data-open="driver">查看运行修订</button><button class="btn" data-action="cancel">取消</button>`
    : `<button class="btn" data-action="pause">${ic.pause} 暂停</button><button class="btn" data-action="cancel">${ic.stop} 取消</button><button class="btn primary" data-open="running">打开当前步骤</button>`;
  const body = `
    <div class="test-title">工单状态修改</div><div class="test-meta">完整验证 · 计划驱动</div>${status}
    <div class="button-row">${buttons}</div>
    <button class="link block" data-open="ai-records">AI：证据分析已保存，待验证假设</button>
    <button class="link block" data-open="ai-records">查看 AI 分析</button>
    <div class="divider"></div>
    <div class="progress-row"><span class="progress-label">自动步骤</span><span class="progress-tail">3 / 5</span></div>
    <div class="progress-track"><div class="progress-bar"></div></div>
    <div class="note-row">${ic.info}<span>待你操作：持久化核验，第3步</span></div>
    <div class="note-row">${ic.info}<span>最近：HTTP 200 · 响应已保存</span></div>
    <div class="note-row">${ic.info}<span>活动执行在关闭编辑器后由核心继续管理</span></div>`;
  return `<div class="sidebar">${top}<div class="content">${section("current", "当前测试", "运行中", body)}${pendingSection(paused ? "paused" : "running")}${planSection(true, true)}${recordsCollapsed()}</div>${footer}</div>`;
}

function resultSidebar(): string {
  const body = `
    <div class="test-title">工单状态修改</div><div class="test-meta">完整验证 · 计划驱动</div><div class="status-line">${ic.warning} 未完成</div>
    <div class="button-row"><button class="btn primary" data-open="running">${ic.arrow} 去核验</button><button class="btn" data-open="result">查看报告</button><button class="btn" data-open="result">导出</button></div>
    <button class="link block" data-open="ai-records">AI：证据分析已保存，待验证假设</button><button class="link block" data-open="ai-records">查看 AI 分析</button>
    <div class="divider"></div>
    <div class="kv"><span class="kv-label">依据确认</span><span class="kv-value">12/12 · 不代替核验；采集 10:24</span></div>
    <div class="metrics"><div class="metric">执行<strong>11 / 12</strong></div><div class="metric">复用<strong>0 / 12</strong></div><div class="metric">验证<strong>11 / 12</strong></div><div class="metric warn">未验证<strong>1</strong></div><div class="metric">已确认失败<strong>0</strong></div><div class="metric">证据<strong>C · 关键证据不足</strong></div></div>
    <div class="note-row">${ic.info}<span>最终持久化尚未核验</span></div>
    <details><summary class="note-row">${ic.info}<span>完整统计与来源</span></summary><div class="hint">执行、复用和有效验证均来自核心 CoverageDTO。</div></details>`;
  return `<div class="sidebar">${top}<div class="content">${section("current", "当前测试", "本轮结果", body)}${pendingSection("result")}${planSection(false, false, true)}${recordsCollapsed()}</div>${footer}</div>`;
}

function recordsCollapsed(): string {
  return `<button class="records-collapsed" data-open="records"><span class="section-chevron">›</span><span class="section-title">测试记录</span><span class="section-tail">最近报告</span></button>`;
}
function settingsSidebar(): string {
  return `<div class="sidebar">${top}<div class="content">
    <button class="settings-top" data-back>${ic.back}<span>返回本次测试</span></button>
    <div class="hint" style="margin:0 14px 5px">工单状态修改 · 准备中</div>
    <div class="settings-title">模型 API 设置</div>
    <div class="settings-sub">插件直接调用模型 · 项目：工单服务 · 配置修订2</div>
    <div class="settings-body">
      <label class="check-row"><input type="checkbox" checked><span>启用插件模型 API</span></label>
      <label class="field-label">供应方</label>
      <select class="input"><option>DeepSeek（默认）</option></select>
      <label class="field-label">API 地址</label>
      <input class="input" value="https://model.example/v1">
      <div class="hint">示例地址。使用你配置的服务端点</div>
      <label class="field-label">模型</label>
      <input class="input" value="示例模型">
      <label class="field-label">API 密钥引用</label>
      <input class="input" value="模型凭据 · 已配置">
      <button class="link" data-noop>管理安全凭据</button>
      <label class="field-label">请求超时（秒）</label>
      <input class="input" value="30">
      <div class="settings-actions"><button class="btn primary" data-noop>保存配置预览</button><button class="btn" data-noop>检查连接</button></div>
      <div class="settings-section"><h3>允许发送的材料</h3><p>项目摘要、需求及已允许的脱敏证据。首次发送、变更目标或扩大材料范围须核对策略。</p>
        <label class="check-row"><input type="checkbox"><span>允许发送选定源码片段<br><span class="hint" style="margin:0">默认关闭；关闭适用于所有出站材料。</span></span></label>
      </div>
      <details class="settings-section"><summary>项目、默认测试与本地数据</summary><p>编辑器 AI 与 MCP 由用户选择是否使用；一期交付含 CLI / stdio MCP，复用同一核心。</p></details>
    </div>
  </div>${footer}</div>`;
}

function sidebarTemplate(state: SidebarState): string {
  if (state === "settings") return settingsSidebar();
  if (state === "running") return runningSidebar(false);
  if (state === "paused") return runningSidebar(true);
  if (state === "result") return resultSidebar();
  return prepareSidebar();
}

function breadcrumb(text: string): string {
  return `<div class="breadcrumb"><span>AI 测试 › 工单服务 › ${text}</span></div>`;
}

function detailShell(crumb: string, body: string): string {
  return `<div class="detail">${breadcrumb(crumb)}<div class="scroller">${body}</div></div>`;
}

function aiRecordsDetail(): string {
  return detailShell("AI 生成记录", `
    <h1>AI 生成记录</h1><p class="lead">插件 API · 原输入、原响应与草稿分别保存</p>
    <h2>检查与用例草稿 · AI-002</h2>
    <p class="subtle">复用已保存生成结果；本次未新调用。<br>来源：上下文修订2 / 模板修订1 / 规则修订3。</p>
    <h2>证据分析 · AI-003</h2>
    <div class="banner"><strong>基于部分材料 · 待验证假设</strong>输入：指定尝试的安全响应摘要。<br>未纳入：持久化证据尚缺；日志正文受输入限制。<br>源码片段关闭，不随其他字段发送。</div>
    <p class="subtle">原来源的迟到响应 · AI-001</p>
    <p class="subtle"><span style="color:#d6a84d">来源已过期：上下文修订1 → 修订2。</span><br>响应仍归原请求与输入；不覆盖当前分析、人工修订或业务结论。</p>
    <table class="table"><colgroup><col style="width:29%"><col></colgroup>
      <tr><th>模型 / 策略</th><td>DeepSeek · 配置修订2 / 出站策略修订2</td></tr>
      <tr><th>本地请求</th><td>AI-003</td></tr>
      <tr><th>供应商请求号</th><td>未知 · 示例无实际调用</td></tr>
      <tr><th>安全响应 / 错误</th><td>按原请求读取；截断和结构错误分别显示</td></tr>
    </table>
    <div class="button-row"><button class="btn primary" data-noop>查看原来源</button><button class="btn" data-noop>重新核对材料</button></div>
    <p class="subtle">仅重新生成受影响草稿。换供应方由用户选择；确认分析不替代实际核验。</p>`);
}

function driverDetail(): string {
  return detailShell("驱动与运行修订", `
    <h1>驱动与运行修订</h1><p class="lead">R-20261001-03 · 受影响运行已暂停 · 步骤边界处理</p>
    <table class="table"><colgroup><col style="width:17%"><col></colgroup>
      <tr><th>初始驱动</th><td>计划驱动</td></tr><tr><th>有效驱动</th><td>计划驱动</td></tr><tr><th>生效边界</th><td>下一未执行步骤：由核心确认</td></tr>
    </table>
    <h2>变更原因</h2><textarea class="input">逐步核对变更后的持久化核验输入。</textarea>
    <div class="button-row"><button class="btn primary" data-noop>改为逐步驱动</button><button class="btn" data-open-detail="running">重新准备新运行</button></div>
    <h2>计划与运行中修订</h2>
    <table class="table"><colgroup><col style="width:20%"><col style="width:39%"><col></colgroup>
      <tr><th>记录</th><th>变化 / 原因</th><th>影响</th></tr>
      <tr><td>基础计划修订3</td><td>原验收范围与必测集合</td><td>冻结，不回改</td></tr>
      <tr><td>运行修订1</td><td>未执行核验的查询截止条件</td><td>仅后续步骤；保留原因</td></tr>
      <tr><td>相关验收修订变化</td><td>原查询依据需复核</td><td>受影响依据过期</td></tr>
    </table>
    <div class="banner warning"><strong>需重跑：查询与分页、最终持久化核验</strong>原结果仅作历史；修改不自动重跑。新实际输入须重新核对授权。</div>
    <div class="button-row"><button class="btn" data-noop>查看修订差异</button><button class="btn" data-noop>修订未执行步骤</button></div>
    <p class="subtle">运行中不能切回计划驱动。执行中步骤不可编辑；full 必测项和断言要求保持锁定。</p>`);
}

function environmentDetail(): string {
  return detailShell("环境与来源", `
    <h1>环境与执行来源</h1><p class="lead">准备详情 · 保存配置后仍须重新准备 · 未启动执行</p>
    <label class="field-label">依赖环境</label><select class="input"><option>venv（默认）</option><option>显式不隔离</option></select>
    <p class="subtle" style="margin-top:6px">Python 3.13 · 已登记解释器；依赖集合修订2。</p>
    <h2>业务数据与目标</h2>
    <dl class="detail-list">
      <dt>数据来源 / 隔离</dt><dd>工单测试集 / 本轮业务对象 ticket-042</dd>
      <dt>复位方式</dt><dd>已登记测试数据复位流程；准备时核对</dd>
      <dt>目标部署</dt><dd>测试服务 · 部署版本待核实</dd>
      <dt>实际来源</dt><dd>入口已登记；解释器与加载路径待启动核对</dd>
      <dt>凭据引用</dt><dd>HTTP测试 / 独立核验，分用途配置</dd>
    </dl>
    <div class="banner"><strong>环境与证据等级分别呈现</strong>venv 不证明数据隔离；部署待核实不冒充源码一致。</div>
    <h2>编辑器有未保存内容</h2>
    <label class="radio-line"><input type="radio" name="unsaved" checked><span>由用户保存后重新准备</span></label>
    <label class="radio-line"><input type="radio" name="unsaved"><span>明确使用磁盘版本，记录本次选择</span></label>
    <div class="button-row"><button class="btn primary" data-noop>核对准备选择</button><button class="btn" data-open-detail="regression">查看回归建议</button></div>
    <p class="subtle">不会自动保存源码。实际入口与固定源码不符时阻塞；prepare / start 间变化须重新准备。</p>`);
}

function issueDetail(): string {
  return detailShell("问题 · IS-017", `
    <h1>问题草稿 · IS-017</h1><p class="lead">草稿 · 人工记录 EV-017 / 历史运行 R-20260930-01</p>
    <label class="field-label">标题</label><input class="input" value="分页后未找到已创建的工单">
    <label class="field-label">复现、预期与实际</label><textarea class="input">创建并记录工单ID → 查询下一页。预期：可按规则找到同一ID；实际：人工记录未找到，待核对筛选条件和证据。</textarea>
    <h2>事实与分析分别保存</h2>
    <p class="subtle">事实来源：人工截图 EV-017，关联查询与分页用例。<br>AI 假设：可能与分页条件有关，未确认根因。<br>版本 / 环境：历史源码与环境引用；位置未知。</p>
    <label class="field-label">严重程度</label><select class="input"><option>待确认建议 P2</option></select>
    <label class="field-label">本地责任人</label><input class="input" value="本地开发人（待确认）">
    <label class="checkbox-line"><input type="checkbox"><span>我已核对当前事实、证据、级别与本地责任人</span></label>
    <div class="button-row"><button class="btn primary" data-noop>提交待确认预览</button><button class="btn" data-noop>保存草稿预览</button></div>
    <p class="subtle">人工核对独立于处理进度、报告复核与关闭。当前事实变化需重新核对；旧确认永久保留。</p>
    <p class="subtle">修复关闭须新实际回归；缺证据本身不确认产品缺陷。</p>`);
}

function recordsDetail(): string {
  return detailShell("报告查询", `
    <h1>工单服务 · 报告</h1><p class="lead">按项目和业务结果查询；完整编号精确匹配</p>
    <label class="field-label">项目</label><select class="input"><option>工单服务</option></select>
    <label class="field-label">业务结果</label><select class="input"><option>全部业务结果</option></select>
    <label class="field-label">编号类型</label><select class="input"><option>报告编号</option></select>
    <label class="field-label">完整编号</label><input class="input" placeholder="输入完整运行 / 报告编号">
    <div class="button-row"><button class="btn primary" data-noop>查询示例</button><button class="btn" data-noop>清除编号</button></div>
    <table class="table"><colgroup><col style="width:54%"><col style="width:27%"><col></colgroup>
      <tr><th>报告编号 / 范围</th><th>业务结果</th><th>证据</th></tr>
      <tr><td>RP-20261001-03 · 工单状态修改</td><td>未完成</td><td>C</td></tr>
      <tr><td>RP-20260930-01 · 快速检查</td><td>通过（所选）</td><td>未打分</td></tr>
    </table>
    <details open style="margin-top:13px"><summary>▾ RP-20261001-03 · 历史修订</summary>
      <p class="subtle">修订3：未完成；策略修订3<br>修订2：失败；策略修订3（历史依据）<br>历史修订保留自身结果，不继承列表筛选。</p>
      <div class="button-row"><button class="btn" data-noop>下一页</button><button class="btn" data-noop>刷新</button><span class="subtle">同一数据提交分页</span></div>
    </details>
    <details style="margin-top:12px"><summary>▾ 项目显示偏好</summary><p class="subtle">已隐藏项目：检索示例；隐藏不删除材料或停止运行。</p><button class="link" data-noop>恢复检索示例管理项目显示</button></details>`);
}

function regressionDetail(): string {
  return detailShell("变更与回归", `
    <h1>变更与回归建议</h1><p class="lead">需求 / 源码 / 接口 / 规则变化。确认后另行准备运行</p>
    <h2>变更来源</h2><p class="subtle">状态模块内容差异、接口规则修订3；依赖与用例映射修订2。按已登记关系反向匹配。</p>
    <table class="table"><colgroup><col style="width:34%"><col style="width:38%"><col></colgroup>
      <tr><th>受影响用例</th><th>变更依据</th><th>建议</th></tr>
      <tr><td>状态修改接口</td><td>本模块改动</td><td>纳入</td></tr>
      <tr><td>查询与分页</td><td>登记的依赖关系</td><td>纳入</td></tr>
      <tr><td>最终持久化</td><td>关键链路关联</td><td>必测</td></tr>
    </table>
    <div class="banner warning"><strong>推导结果，可能不完整</strong>一个模块的用例映射缺失；不能解释为无影响。快速检查使用已发布保守集合，无法确定则等待确认范围。</div>
    <h2>人工新增 / 排除及理由</h2><textarea class="input">新增：异常状态拒绝；理由：规则边界发生变化。full 适用必测不能排除。</textarea>
    <div class="button-row"><button class="btn primary" data-noop>确认回归范围预览</button><button class="btn" data-open-detail="plan">查看计划</button></div>
    <p class="subtle">单模块仍检查本模块变化，仅跨模块传播不适用。确认建议不会启动执行，也不改变旧报告。</p>`);
}

function resultDetail(): string {
  return detailShell("报告 · 修订3", `
    <h1>工单状态修改</h1><p class="lead">报告快照 · 修订3 / 完整验证 · 计划驱动 / 来源：本轮已保存记录</p>
    <div class="status-main"><span class="outcome">未完成</span><span class="grade">证据 C · 关键证据不足</span></div>
    <p class="summary-text">最终持久化尚未核验。此结论只对应“工单状态修改”验收范围。</p>
    <div class="button-row"><button class="btn primary" data-open-detail="running">→ 去核验</button><button class="btn" data-noop>↥ 导出报告</button><button class="btn" data-noop>本地复核</button></div>
    <div class="large-metrics"><div class="large-metric">本轮执行<strong>11 / 12</strong></div><div class="large-metric">有效复用<strong>0 / 12</strong></div><div class="large-metric">有效验证<strong>11 / 12</strong></div></div>
    <h2>覆盖与结论</h2>
    <table class="table"><colgroup><col style="width:40%"><col style="width:20%"><col style="width:20%"><col></colgroup>
      <tr><th>范围</th><th>有效通过</th><th>整用例失败</th><th>未验证</th></tr>
      <tr><td>本轮所选 12项</td><td>11</td><td>0</td><td>1</td></tr>
      <tr><td>完整必测 12项</td><td>11</td><td>0</td><td>1</td></tr>
    </table>
    <p class="subtle">已确认失败用例 0 · 关键链路核验 3 / 4<br>断言依据确认 12 / 12 · 确认不代替实际核验</p>
    <h2>需要补齐的证据</h2><div class="banner warning"><strong>最终持久化核验 · 缺证据</strong>当前仅保存了请求与响应，缺少同一业务对象的持久化核验。</div>
    <h2>结果来源与时间</h2><p class="subtle">源码 abc1234 + 未提交差异 · 环境 E2 · 计划修订3<br>数据提交 c87 · 判定策略修订3 · 报告修订3<br>开始 2026-10-01 10:20 · 采集记录 10:24（本机时间）<br>此处为报告冻结照；核验更新形成新的报告修订。</p>
    <details><summary>▸ 独立本地复核记录</summary><p class="subtle">尚无本地复核记录。</p></details>`);
}

function runningDetail(): string {
  return detailShell("测试 · 工单状态修改", `
    <h1>工单状态修改</h1><p class="lead">当前运行 · R-20261001-03 / 计划修订3 / 所选步骤尝试1</p>
    <div class="tabs"><button>更新状态</button><button>查询状态</button><button aria-selected="true">核验持久化</button></div>
    <h2>核验最终持久化</h2><p class="lead">L3 业务场景　必测　业务对象 ticket-042</p>
    <p class="subtle">确认这次状态修改已保存到实际存储；核验对象必须与前面的请求一致。</p>
    <dl class="detail-list"><dt>执行状态</dt><dd>待人工操作</dd><dt>断言与核验</dt><dd>尚未有效验证</dd><dt>断言依据</dt><dd>已确认 · 原验收标准</dd><dt>来源真实性</dt><dd>待核实</dd></dl>
    <div class="banner warning"><strong>当前缺口：没有最终持久化核验证据</strong>接口响应成功不能代替持久化核验。请记录实际操作与同一业务对象的证据。</div>
    <div class="button-row"><button class="btn primary" data-noop>填写实际操作与补证</button><button class="btn" data-noop>核实执行状态</button></div>
    <div class="tabs" style="margin-top:18px"><button aria-selected="true">证据</button><button>AI分析</button><button>人工意见</button></div>
    <h2 style="margin-top:0">已保存证据</h2>
    <p class="subtle">HTTP 200 · request_id: req-1842<br>业务对象: ticket-042<br>响应: { "status": "resolved" }<br>持久化核验: 尚无有效材料</p>
    <button class="link" data-noop>打开响应详情</button>　<button class="link" data-noop>查看历史尝试</button><br>
    <button class="link" data-noop>驱动与运行修订</button>　<button class="link" data-noop>从记录创建问题草稿</button>`);
}

function detailTemplate(view: DetailView): string {
  switch (view) {
    case "ai-records": return aiRecordsDetail();
    case "driver": return driverDetail();
    case "environment": return environmentDetail();
    case "issue": return issueDetail();
    case "records": return recordsDetail();
    case "regression": return regressionDetail();
    case "result": return resultDetail();
    case "running": return runningDetail();
    case "source": return "";
    default: return detailShell("当前测试", `<h1>尚未连接核心</h1><p class="lead">面板只显示核心投影的事实，不生成占位统计或业务结论。</p>`);
  }
}

function hostApi(): HostApi | null {
  const acquire = (globalThis as { acquireVsCodeApi?: () => HostApi }).acquireVsCodeApi;
  return typeof acquire === "function" ? acquire() : null;
}

function isDemo(): boolean {
  return (globalThis as { __AITEST_DEMO__?: boolean }).__AITEST_DEMO__ === true;
}

class AITestPanel extends HTMLElement {
  public static observedAttributes = ["state"];
  private readonly root = this.attachShadow({ mode: "open" });
  private readonly host = hostApi();
  private state: SidebarState = "prepare";
  private readonly collapsed = new Set<string>();
  private outcome: HostOutcome | null = null;

  connectedCallback(): void {
    const requested = this.getAttribute("state") as SidebarState | null;
    if (requested && ["prepare", "running", "paused", "result", "settings"].includes(requested)) {
      this.state = requested;
    }
    window.addEventListener("message", (event: MessageEvent) => {
      const message = event.data as HostOutcome | undefined;
      if (!message || message.kind !== "outcome") return;
      this.outcome = message;
      this.updateCoreLabel();
    });
    this.root.addEventListener("click", (event) => this.onClick(event));
    this.render();
    if (this.host) this.host.postMessage({ kind: "dispatch", action: "doctor", parameters: {} });
  }

  attributeChangedCallback(name: string, _oldValue: string | null, value: string | null): void {
    if (name !== "state" || !value) return;
    if (["prepare", "running", "paused", "result", "settings"].includes(value)) {
      this.state = value as SidebarState;
      this.render();
    }
  }

  private onClick(event: Event): void {
    const target = event.target as Element | null;
    const control = target?.closest<HTMLElement>("[data-settings],[data-back],[data-toggle],[data-open],[data-action],[data-noop],[data-more],[data-project],[data-home],[data-refresh],[data-close-menu],[data-case]");
    if (!control) return;
    if (control.hasAttribute("data-more") || control.hasAttribute("data-project")) {
      const wanted = control.hasAttribute("data-more") ? "[data-more-menu]" : "[data-project-menu]";
      this.root.querySelectorAll<HTMLElement>(".popover").forEach((menu) => { menu.hidden = menu.matches(wanted) ? !menu.hidden : true; });
      return;
    }
    if (control.hasAttribute("data-close-menu")) {
      this.root.querySelectorAll<HTMLElement>(".popover").forEach((menu) => { menu.hidden = true; });
      return;
    }
    if (control.hasAttribute("data-home")) { this.state = "prepare"; this.render(); return; }
    if (control.hasAttribute("data-refresh")) { this.render(); return; }
    if (control.hasAttribute("data-case")) {
      this.root.querySelectorAll<HTMLElement>("[data-case]").forEach((item) => { item.classList.toggle("selected", item === control); });
      return;
    }
    if (control.hasAttribute("data-settings")) { this.state = "settings"; this.render(); return; }
    if (control.hasAttribute("data-back")) { this.state = "prepare"; this.render(); return; }
    const toggle = control.getAttribute("data-toggle");
    if (toggle) {
      const body = this.root.querySelector<HTMLElement>(`[data-body="${toggle}"]`);
      if (body) {
        const close = !this.collapsed.has(toggle);
        if (close) this.collapsed.add(toggle); else this.collapsed.delete(toggle);
        body.hidden = close;
        control.setAttribute("aria-expanded", String(!close));
        const chevron = control.querySelector<HTMLElement>(".section-chevron");
        if (chevron) chevron.textContent = close ? "›" : "⌄";
      }
      return;
    }
    const view = control.getAttribute("data-open");
    if (view) { this.openDetail(view as DetailView); return; }
    const action = control.getAttribute("data-action");
    if (action) this.dispatchEvent(new CustomEvent("aitest-action", { bubbles: true, composed: true, detail: { action } }));
  }

  private openDetail(view: DetailView): void {
    if (this.host) { this.host.postMessage({ kind: "openDetail", detail: view }); return; }
    window.dispatchEvent(new CustomEvent("aitest-open-detail", { detail: { view } }));
  }

  private updateCoreLabel(): void {
    const label = this.root.querySelector<HTMLElement>("[data-core-label]");
    if (!label) return;
    if (this.outcome?.status === "ok") label.textContent = "核心已连接 · 模型 API 已配置";
    else if (this.outcome?.status === "unavailable") label.textContent = `核心不可用${this.outcome.code ? ` · ${this.outcome.code}` : ""}`;
  }

  private render(): void {
    this.root.replaceChildren();
    const style = document.createElement("style");
    style.textContent = sidebarCss;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = isDemo() || this.hasAttribute("demo")
      ? sidebarTemplate(this.state)
      : `<div class="sidebar"><div class="content" style="padding:24px 14px"><div class="hint">尚未取得核心投影。</div></div>${footer}</div>`;
    this.root.append(style, wrapper);
    for (const id of this.collapsed) {
      const body = this.root.querySelector<HTMLElement>(`[data-body="${id}"]`);
      if (body) body.hidden = true;
    }
    this.updateCoreLabel();
  }
}

class AITestDetail extends HTMLElement {
  public static observedAttributes = ["view"];
  private readonly root = this.attachShadow({ mode: "open" });
  private view: DetailView = "empty";

  connectedCallback(): void {
    this.root.addEventListener("click", (event) => {
      const target = event.target as Element | null;
      const control = target?.closest<HTMLElement>("[data-open-detail],[data-noop]");
      if (!control) return;
      const detail = control.getAttribute("data-open-detail");
      if (detail) window.dispatchEvent(new CustomEvent("aitest-open-detail", { detail: { view: detail } }));
    });
    this.render();
  }

  attributeChangedCallback(_name: string, _oldValue: string | null, value: string | null): void {
    if (value) { this.view = value as DetailView; this.render(); }
  }

  private render(): void {
    this.root.replaceChildren();
    const style = document.createElement("style");
    style.textContent = detailCss;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = detailTemplate(this.view);
    this.root.append(style, wrapper);
  }
}

const sourceLines = [
  "from dataclasses import dataclass", "", " # 示例：工单状态更新", "@dataclass", "class Ticket:", "    id: str", "    status: str", "",
  "def update_status(ticket_id: str, status: str):", "    ticket = repository.get(ticket_id)", "    ticket.status = status", "    repository.save(ticket)", "",
  "    return {", "        \"id\": ticket_id,", "        \"status\": ticket.status,", "    }", "", "",
  " # 本次测试还将独立核验持久化结果", " # 源码编辑区与测试侧栏可以同时保留",
];

function sourcePane(crumbs: string): string {
  return `<div class="source-breadcrumb">${crumbs}</div><div class="source-code">${sourceLines.map((line, index) => `<div class="source-line${index === 10 ? " active" : ""}"><span class="line-no">${index + 1}</span><span class="line-text">${line.replace(/&/g, "&amp;").replace(/</g, "&lt;")}</span></div>`).join("")}</div>`;
}

function iconForActivity(kind: string): string {
  if (kind === "files") return `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M4 2.5h8l4 4v11H4z"/><path d="M12 2.5v4h4M7 10h6M7 13h6"/></svg>`;
  if (kind === "search") return `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.3"><circle cx="9" cy="9" r="5.2"/><path d="m13 13 4 4"/></svg>`;
  if (kind === "git") return `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.2"><circle cx="5" cy="5" r="2"/><circle cx="15" cy="7" r="2"/><circle cx="8" cy="15" r="2"/><path d="M5 7v2c0 3 3 4 6 4h2M13 9V7"/></svg>`;
  if (kind === "apps") return `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.2"><rect x="3" y="3" width="5" height="5"/><rect x="12" y="3" width="5" height="5"/><rect x="3" y="12" width="5" height="5"/><rect x="12" y="12" width="5" height="5"/></svg>`;
  if (kind === "flask") return `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M7.5 2.5h5M8 2.5v5L4 15a1.5 1.5 0 0 0 1.3 2h9.4a1.5 1.5 0 0 0 1.3-2l-4-7.5v-5"/><path d="M6 13h8"/></svg>`;
  return `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.2"><circle cx="10" cy="7" r="3"/><path d="M4.5 17c.7-3 2.5-5 5.5-5s4.8 2 5.5 5"/></svg>`;
}

const viewMap: Record<string, { sidebar: SidebarState; detail: DetailView; title: string }> = {
  prepare: { sidebar: "prepare", detail: "source", title: "" },
  running: { sidebar: "running", detail: "running", title: "测试 · 工单状态修改" },
  paused: { sidebar: "paused", detail: "driver", title: "驱动与运行修订" },
  driver: { sidebar: "paused", detail: "driver", title: "驱动与运行修订" },
  result: { sidebar: "result", detail: "result", title: "报告 · 修订3" },
  records: { sidebar: "result", detail: "records", title: "报告查询" },
  "ai-records": { sidebar: "prepare", detail: "ai-records", title: "AI 生成记录" },
  environment: { sidebar: "prepare", detail: "environment", title: "环境与来源" },
  issue: { sidebar: "prepare", detail: "issue", title: "问题 · IS-017" },
  regression: { sidebar: "prepare", detail: "regression", title: "变更与回归" },
  single: { sidebar: "settings", detail: "source", title: "" },
};

const menuDefinitions: Readonly<Record<string, ReadonlyArray<readonly [string, string]>>> = {
  file: [["new", "新建文本文件"], ["open", "打开文件…"], ["save", "保存"], ["close", "关闭编辑器"]],
  edit: [["undo", "撤销"], ["redo", "重做"], ["cut", "剪切"], ["copy", "复制"], ["paste", "粘贴"]],
  view: [["ai-panel", "切换 AI 测试面板"], ["source", "切回源码编辑器"], ["palette", "命令面板"], ["zoom-in", "放大"], ["zoom-out", "缩小"]],
  terminal: [["new-terminal", "新建终端"], ["split-terminal", "拆分终端"], ["clear-terminal", "清空终端"]],
};
const menuLabels: Readonly<Record<string, string>> = { file: "文件", edit: "编辑", view: "查看", terminal: "终端" };

function menuBar(activeMenu: string | null): string {
  return `<div class="menu-items">${Object.entries(menuLabels).map(([id, label]) => `
    <div class="menu-wrap">
      <button class="menu-button ${activeMenu === id ? "active" : ""}" data-menu="${id}">${label}</button>
      ${activeMenu === id ? `<div class="menu-dropdown">${(menuDefinitions[id] ?? []).map(([action, text]) => `<button data-menu-action="${action}">${text}</button>`).join("")}</div>` : ""}
    </div>`).join("")}</div>`;
}
class AITestMockup extends HTMLElement {
  private readonly root = this.attachShadow({ mode: "open" });
  private activity = "aitest";
  private activeMenu: string | null = null;
  private statusMessage = "";
  private viewOverride: string | null = null;
  private terminalCleared = false;

  connectedCallback(): void {
    this.root.addEventListener("click", (event) => {
      const target = event.target as Element | null;
      const menu = target?.closest<HTMLElement>("[data-menu]");
      if (menu) {
        const id = menu.getAttribute("data-menu");
        this.activeMenu = this.activeMenu === id ? null : id;
        this.render();
        return;
      }
      const action = target?.closest<HTMLElement>("[data-menu-action]");
      if (action) {
        this.runMenuAction(action.getAttribute("data-menu-action") || "");
        return;
      }
      const activity = target?.closest<HTMLElement>("[data-activity]");
      if (activity) {
        this.activity = activity.getAttribute("data-activity") || "aitest";
        this.activeMenu = null;
        this.render();
        return;
      }
      if (this.activeMenu !== null) {
        this.activeMenu = null;
        this.render();
      }
    });
    this.render();
    window.addEventListener("aitest-open-detail", ((event: CustomEvent<{ view: string }>) => {
      const view = event.detail?.view;
      if (!view) return;
      this.viewOverride = null;
      this.activeMenu = null;
      const url = new URL(window.location.href);
      url.searchParams.set("view", view);
      window.history.replaceState(null, "", url);
      this.render();
    }) as EventListener);
  }

  private runMenuAction(action: string): void {
    if (action === "ai-panel") this.activity = "aitest";
    if (action === "source") { this.activity = "files"; this.viewOverride = "prepare"; }
    if (action === "close") { this.activity = "files"; this.viewOverride = "prepare"; }
    if (action === "clear-terminal") this.terminalCleared = true;
    if (action === "new-terminal") this.terminalCleared = false;
    const labels: Readonly<Record<string, string>> = {
      new: "已新建文本文件", open: "打开文件命令已触发", save: "当前源码已保存（预览）",
      close: "已关闭当前详情，返回源码编辑器", undo: "已撤销上一步", redo: "已重做",
      cut: "已剪切当前选择", copy: "已复制当前选择", paste: "已粘贴",
      "ai-panel": "已切换到 AI 测试面板", source: "已切回源码编辑器", palette: "命令面板已打开",
      "zoom-in": "编辑器已放大", "zoom-out": "编辑器已缩小", "new-terminal": "已新建终端",
      "split-terminal": "终端已拆分", "clear-terminal": "终端已清空",
    };
    this.statusMessage = labels[action] ?? `已执行：${action}`;
    this.activeMenu = null;
    this.render();
  }

  private currentView(): string {
    return this.viewOverride || this.getAttribute("view") || new URL(window.location.href).searchParams.get("view") || "prepare";
  }

  private render(): void {
    this.root.replaceChildren();
    const style = document.createElement("style");
    style.textContent = mockupCss;
    const view = this.currentView();
    const mapped = viewMap[view] ?? viewMap.prepare;
    const hasDetail = mapped.detail !== "source";
    const active = (id: string): string => this.activity === id ? "active" : "";
    const activityLabels: Readonly<Record<string, string>> = {
      files: "资源管理器", search: "搜索", git: "源代码管理", apps: "扩展",
    };
    const leftPanel = this.activity === "aitest"
      ? `<ai-test-panel demo state="${mapped.sidebar}"></ai-test-panel>`
      : `<aside class="activity-panel"><h2>${activityLabels[this.activity] ?? "编辑器"}</h2><p>这是编辑器原有工具区，参考图中仅展示它与 AI 测试侧栏并存。</p><button class="${this.activity === "files" ? "selected" : ""}">${this.activity === "search" ? "搜索文件与内容" : this.activity === "git" ? "ticket-service · main*" : this.activity === "apps" ? "已安装扩展" : "ticket-service"}</button></aside>`;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = `
      <div class="app" data-view="${view}">
        <div class="titlebar">${menuBar(this.activeMenu)}<div class="window-title">${hasDetail ? "ticket-service — Trae" : "ticket-service — 编辑器"}</div><div class="scene-label">交互草图 · 示例数据</div></div>
        <div class="workspace">
          <aside class="activitybar"><button data-activity="files" class="${active("files")}" aria-label="文件">${iconForActivity("files")}</button><button data-activity="search" class="${active("search")}" aria-label="搜索">${iconForActivity("search")}</button><button data-activity="git" class="${active("git")}" aria-label="源代码管理">${iconForActivity("git")}</button><button data-activity="apps" class="${active("apps")}" aria-label="扩展">${iconForActivity("apps")}</button><button data-activity="aitest" class="${active("aitest")}" aria-label="AI 测试">${iconForActivity("flask")}</button></aside>
          ${leftPanel}
          <main class="editor">
            <div class="tabbar"><div class="tab ${hasDetail ? "" : "active"}">${ic.file}<span>ticket_service.py</span></div>${hasDetail ? `<div class="tab active detail-tab">${ic.flask}<span>${mapped.title}</span><span class="tab-close">×</span></div>` : ""}</div>
            <div class="editor-main">${hasDetail ? `<ai-test-detail demo view="${mapped.detail}"></ai-test-detail>` : sourcePane("ticket-service › src › ticket_service.py")}</div>
            <div class="terminal"><div class="terminal-tabs"><button class="active">问题</button><button>终端</button><button>输出</button><span class="terminal-note">编辑器原有工具区</span></div><pre>${this.terminalCleared ? "终端已清空" : `PS ticket-service&gt; git status --short\n M src/ticket_service.py\n M tests/test_status.py`}</pre></div>
          </main>
        </div>
        <div class="statusbar"><span class="branch">⎇ main*</span><span>本地测试核心 · 已连接</span>${this.statusMessage ? `<span class="status-action">${this.statusMessage}</span>` : ""}<span class="status-spacer"></span><span>Python 3.13</span><span>UTF-8</span></div>
      </div>`;
    this.root.append(style, wrapper);
  }
}

if (!customElements.get("ai-test-panel")) customElements.define("ai-test-panel", AITestPanel);
if (!customElements.get("ai-test-detail")) customElements.define("ai-test-detail", AITestDetail);
if (!customElements.get("aitest-mockup")) customElements.define("aitest-mockup", AITestMockup);
