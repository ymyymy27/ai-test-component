/** Static phase-one panel prototype. It renders fixture text only and never
 * calculates business outcomes or treats UI clicks as user confirmation. */
type Detail = "current" | "plan" | "workbench" | "review" | "settings";

const details: ReadonlyArray<readonly [Detail, string, string]> = [
  ["plan", "计划", "查看范围、用例树、依据确认和发布状态。"],
  ["workbench", "测试工作台", "查看步骤、当前尝试、证据、人工待办和阻塞原因。"],
  ["review", "问题与报告", "查看问题清单、修复回归、报告修订和本地导出。"],
  ["settings", "设置", "配置模型、环境、凭据引用、存储和显示偏好。"],
];

const phases: ReadonlyArray<readonly [string, string, string]> = [
  ["运行前", "等待准备计划", "确认范围、来源、环境、依据和逐项动作授权。"],
  ["运行时", "尚未开始", "显示当前步骤、尝试、待处理事项和准确阻塞原因。"],
  ["运行后", "暂无报告修订", "显示业务结论、证据等级、未验证范围和下一步。"],
];

type Outcome = {
  kind: "outcome";
  action: string | null;
  status: "ok" | "refused" | "unavailable";
  code?: string;
  message?: string;
  response?: Record<string, unknown>;
};

interface HostApi { postMessage(message: unknown): void }

function hostApi(): HostApi | null {
  const acquire = (globalThis as { acquireVsCodeApi?: () => HostApi }).acquireVsCodeApi;
  return typeof acquire === "function" ? acquire() : null;
}

class AITestPanel extends HTMLElement {
  private detail: Detail = "current";
  private readonly root = this.attachShadow({ mode: "open" });
  private readonly host = hostApi();
  private outcome: Outcome | null = null;
  private pending = false;

  connectedCallback(): void {
    window.addEventListener("message", (event: MessageEvent) => {
      const data = event.data as Outcome | undefined;
      if (!data || data.kind !== "outcome") { return; }
      this.outcome = data;
      this.pending = false;
      this.render();
    });
    this.render();
    this.dispatch("doctor");
  }

  /** 面板只发起**只读**查询；写动作由受控用户入口发起，界面点击不等于确认。 */
  private dispatch(action: string, parameters?: Record<string, unknown>): void {
    if (this.host === null) {
      this.outcome = {
        kind: "outcome", action, status: "unavailable",
        message: "当前不在编辑器宿主中，面板未连接核心",
      };
      this.render();
      return;
    }
    this.pending = true;
    this.host.postMessage({ kind: "dispatch", action, parameters: parameters ?? {} });
    this.render();
  }

  private statusText(): string {
    if (this.pending) { return "已请求核心，等待回执…"; }
    const outcome = this.outcome;
    if (outcome === null) { return "尚未连接核心"; }
    if (outcome.status === "ok") {
      const result = outcome.response?.["result"] as Record<string, unknown> | undefined;
      const status = typeof result?.["status"] === "string" ? result["status"] : "OK";
      const actions = Array.isArray(result?.["supported_actions"])
        ? (result["supported_actions"] as unknown[]).length : 0;
      return `核心回执 · ${outcome.action} · ${status} · 可派发动作 ${actions}`;
    }
    const code = outcome.code ?? outcome.status;
    return `核心未通过 · ${outcome.action ?? "-"} · ${code}：${outcome.message ?? ""}`;
  }

  private render(): void {
    this.root.replaceChildren();
    const style = document.createElement("style");
    style.textContent = `
      :host { display:block; color:var(--vscode-foreground,#222); font:13px/1.5 system-ui; }
      * { box-sizing:border-box; }
      main { max-width:760px; margin:auto; padding:12px; }
      header, section { border:1px solid var(--vscode-widget-border,#bbb); border-radius:6px;
        padding:12px; margin-bottom:10px; }
      h1 { font-size:18px; margin:0 0 4px; } h2 { font-size:15px; margin:0 0 8px; }
      h3 { font-size:13px; margin:0 0 4px; } p { margin:4px 0; }
      .muted { opacity:.75; } .scope { font-weight:600; }
      .phases { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }
      .phase { border:1px solid var(--vscode-widget-border,#ccc); border-radius:4px; padding:8px; }
      button, textarea { font:inherit; color:inherit; }
      button { padding:7px 10px; background:transparent; border:1px solid currentColor;
        border-radius:4px; cursor:pointer; }
      button[disabled] { cursor:not-allowed; opacity:.55; }
      button:focus-visible, textarea:focus-visible { outline:3px solid var(--vscode-focusBorder,#2678d8); }
      .actions, .details { display:flex; flex-wrap:wrap; gap:8px; }
      .actions button:first-child { font-weight:700; }
      textarea { width:100%; min-height:64px; resize:vertical; padding:8px;
        color:inherit; background:transparent; border:1px solid currentColor; border-radius:4px; }
      .next { border-left:4px solid var(--vscode-focusBorder,#2678d8); padding-left:10px; }
      @media (max-width:520px) { .phases { grid-template-columns:1fr; } }
    `;
    const main = document.createElement("main");
    main.append(this.header(), this.detail === "current" ? this.currentView() : this.detailView());
    this.root.append(style, main);
  }

  private header(): HTMLElement {
    const header = document.createElement("header");
    const title = document.createElement("h1"); title.textContent = "AI 辅助测试";
    const status = document.createElement("p");
    status.dataset.testid = "panel-status";
    status.className = "muted";
    status.textContent = this.statusText();
    const refresh = document.createElement("button");
    refresh.textContent = "刷新核心状态";
    refresh.dataset.action = "refresh-core";
    refresh.disabled = this.pending;
    refresh.onclick = () => { this.dispatch("doctor"); };
    const scope = document.createElement("p"); scope.className = "scope";
    scope.textContent = "当前项目：未选择 · 范围：尚未准备（一期只读入口）";
    header.append(title, status, refresh, scope);
    return header;
  }

  private currentView(): HTMLElement {
    const fragment = document.createElement("div");
    fragment.append(
      this.phaseSection(),
      this.capabilitySection(),
      this.readOnlySection(),
      this.aiSection(),
      this.actionSection(),
      this.nextSection(),
      this.detailSection(),
    );
    return fragment;
  }

  /** 核心能力：只显示**真实回执**里的 supported_actions，不推断、不补造。 */
  private capabilitySection(): HTMLElement {
    const section = document.createElement("section");
    section.dataset.testid = "core-capability";
    const title = document.createElement("h2"); title.textContent = "核心能力（真实回执）";
    const outcome = this.outcome;
    const line = document.createElement("p");
    if (outcome === null) {
      line.textContent = "尚未连接核心。";
      section.append(title, line);
      return section;
    }
    if (outcome.status === "ok") {
      const result = outcome.response?.["result"] as Record<string, unknown> | undefined;
      const supported = result?.["supported_actions"];
      const actions = Array.isArray(supported) ? supported.map(String) : [];
      const state = typeof result?.["status"] === "string" ? result["status"] : "OK";
      line.textContent = `核心 ${state}，当前可派发 ${actions.length} 个动作。`;
      const list = document.createElement("p");
      list.className = "muted";
      list.textContent = actions.join("、");
      section.append(title, line, list);
      return section;
    }
    line.textContent = `核心未通过：${outcome.code ?? outcome.status} — ${outcome.message ?? ""}`;
    section.append(title, line);
    return section;
  }

  /** 只读入口：可派发；写动作必须经受控用户入口，界面点击不等于确认。 */
  private readOnlySection(): HTMLElement {
    const section = document.createElement("section");
    section.dataset.testid = "read-only-actions";
    const title = document.createElement("h2"); title.textContent = "只读查询";
    const actions = document.createElement("div"); actions.className = "actions";
    const entries: ReadonlyArray<readonly [string, string]> = [
      ["空间诊断", "storage_usage"],
      ["事件续读", "events"],
    ];
    for (const [label, action] of entries) {
      const button = document.createElement("button");
      button.textContent = label;
      button.dataset.action = action;
      button.disabled = this.pending || this.host === null;
      button.title = this.host === null ? "不在编辑器宿主中，面板未连接核心" : "";
      button.onclick = () => {
        this.dispatch(action, action === "events" ? { limit: 20 } : {});
      };
      actions.append(button);
    }
    const note = document.createElement("p"); note.className = "muted";
    note.textContent =
      "只读入口：写动作必须经受控用户入口并携带授权，界面点击不等于用户确认。";
    section.append(title, actions, note);
    return section;
  }

  private phaseSection(): HTMLElement {
    // “当前测试”是区块标题，不属于阶段摘要本身：phase-summary 内只允许
    // 三个阶段标题（运行前/运行时/运行后），标题放在外层（Playwright 按
    // role=heading 核对阶段数时不得把区块标题计入）。
    const fragment = document.createElement("div");
    const title = document.createElement("h2"); title.textContent = "当前测试";
    const section = document.createElement("section");
    section.dataset.testid = "phase-summary";
    const grid = document.createElement("div"); grid.className = "phases";
    for (const [phase, state, description] of phases) {
      const card = document.createElement("article"); card.className = "phase";
      card.dataset.phase = phase;
      const name = document.createElement("h3"); name.textContent = phase;
      const current = document.createElement("p"); current.textContent = state;
      const detail = document.createElement("p"); detail.className = "muted"; detail.textContent = description;
      card.append(name, current, detail); grid.append(card);
    }
    section.append(grid);
    fragment.append(title, section);
    return fragment;
  }

  private aiSection(): HTMLElement {
    const section = document.createElement("section");
    const title = document.createElement("h2"); title.textContent = "让 AI 准备什么";
    const input = document.createElement("textarea");
    input.placeholder = "描述本次测试目标，或改为人工/模板创建计划。";
    input.disabled = true;
    const button = document.createElement("button");
    button.textContent = "生成计划草稿"; button.disabled = true;
    button.title = "等待统一核心和模型接口接入";
    section.append(title, input, button);
    return section;
  }

  private actionSection(): HTMLElement {
    const section = document.createElement("section");
    section.dataset.testid = "primary-actions";
    const title = document.createElement("h2"); title.textContent = "运行测试";
    const actions = document.createElement("div"); actions.className = "actions";
    for (const label of ["运行快速检查", "运行所选检查", "运行完整验证"]) {
      const button = document.createElement("button");
      button.textContent = label; button.disabled = true;
      button.title = "等待统一核心接入";
      actions.append(button);
    }
    section.append(title, actions);
    return section;
  }

  private nextSection(): HTMLElement {
    const section = document.createElement("section");
    const title = document.createElement("h2"); title.textContent = "下一步";
    const next = document.createElement("p"); next.className = "next";
    next.textContent = "配置模型或人工创建计划；发布计划后，动作区才会获得真实范围与授权。";
    section.append(title, next);
    return section;
  }

  private detailSection(): HTMLElement {
    const section = document.createElement("section");
    const title = document.createElement("h2"); title.textContent = "详情";
    const links = document.createElement("div"); links.className = "details";
    for (const [id, label] of details) {
      const button = document.createElement("button");
      button.textContent = label; button.dataset.detail = id;
      button.onclick = () => { this.detail = id; this.render(); };
      links.append(button);
    }
    section.append(title, links);
    return section;
  }

  private detailView(): HTMLElement {
    const selected = details.find(([id]) => id === this.detail)!;
    const section = document.createElement("section");
    section.dataset.testid = "detail-view";
    const back = document.createElement("button"); back.textContent = "返回本次测试";
    back.dataset.action = "back"; back.onclick = () => { this.detail = "current"; this.render(); };
    const title = document.createElement("h1"); title.textContent = selected[1];
    const description = document.createElement("p"); description.textContent = selected[2];
    const note = document.createElement("p"); note.className = "muted";
    note.textContent = "详情骨架：等待核心 DTO 与事件接入；本面板不计算业务结论。";
    section.append(back, title, description, note);
    return section;
  }
}

customElements.define("ai-test-panel", AITestPanel);