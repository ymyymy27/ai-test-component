/** Phase-one fact viewer and controlled command requester. The core owns
 * business outcomes and the independent human confirmation dialog. */
import {workflowActions, workflowCommand} from "./workflow";
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
  clientRequestId?: string;
};

/** 宿主（扩展）声明的当前项目上下文；面板**不猜**项目身份，未声明即未选择。 */
type ProjectContext = {
  kind: "projectContext";
  project_id: string;
  run_id?: string | null;
  scope_name?: string | null;
  context_id?: string;
};

interface HostApi { postMessage(message: unknown): void }

function hostApi(): HostApi | null {
  const acquire = (globalThis as { acquireVsCodeApi?: () => HostApi }).acquireVsCodeApi;
  return typeof acquire === "function" ? acquire() : null;
}

type Row = { record_id: string; revision: number; aggregate_kind: string };

/** 只读回执里的记录行；面板原样展示，不补造字段、不重算结论。 */
function rowsOf(response: Record<string, unknown> | undefined): Row[] {
  const result = response?.["result"] as Record<string, unknown> | undefined;
  const items = result?.["items"];
  if (!Array.isArray(items)) { return []; }
  const rows: Row[] = [];
  for (const item of items) {
    if (typeof item !== "object" || item === null) { continue; }
    const raw = item as Record<string, unknown>;
    const recordId = raw["record_id"];
    const revision = raw["revision"];
    const kind = raw["aggregate_kind"];
    if (typeof recordId !== "string" || typeof revision !== "number") { continue; }
    rows.push({ record_id: recordId, revision, aggregate_kind: typeof kind === "string" ? kind : "?" });
  }
  return rows;
}

/** 事件行（`aitest.event/2.0`）：只取回执里真实存在的字段，界面不补造。 */
type EventRow = {
  eventSequence: number | null;
  commitSequence: number | null;
  eventType: string;
  recordId: string;
  revision: number | null;
};

/** 从 `events` 回执里解析事件行；字段缺失保留 null，不猜。 */
function eventRowsOf(response: Record<string, unknown> | undefined): EventRow[] {
  const result = response?.["result"] as Record<string, unknown> | undefined;
  const events = result?.["events"];
  if (!Array.isArray(events)) { return []; }
  const rows: EventRow[] = [];
  for (const item of events) {
    if (typeof item !== "object" || item === null) { continue; }
    const raw = item as Record<string, unknown>;
    const sequence = raw["event_sequence"];
    const commit = raw["commit_sequence"];
    const revision = raw["revision"];
    rows.push({
      eventSequence: typeof sequence === "number" ? sequence : null,
      commitSequence: typeof commit === "number" ? commit : null,
      eventType: typeof raw["event_type"] === "string" ? String(raw["event_type"]) : "?",
      recordId: typeof raw["record_id"] === "string" ? String(raw["record_id"]) : "?",
      revision: typeof revision === "number" ? revision : null,
    });
  }
  return rows;
}

/** 事件续读游标（不透明字符串）；只在回执明确给出新游标时前移。 */
function nextCursorOf(response: Record<string, unknown> | undefined): string | null {
  const result = response?.["result"] as Record<string, unknown> | undefined;
  const cursor = result?.["next_cursor"];
  return typeof cursor === "string" && cursor.length > 0 ? cursor : null;
}
const issueFields: ReadonlyArray<readonly [string, string]> = [
  ["description", "说明"],
  ["reproduction", "复现上下文"],
  ["expected", "预期"],
  ["actual", "实际"],
  ["evidence_refs", "证据"],
  ["fact_state", "事实/假设"],
  ["severity", "级别"],
  ["owner_ref", "责任人"],
  ["status", "状态"],
  ["fix_history", "修复/回归"],
];

class AITestPanel extends HTMLElement {
  private detail: Detail = "current";
  private readonly root = this.attachShadow({ mode: "open" });
  private readonly host = hostApi();
  private outcome: Outcome | null = null;
  private pending = false;
  private project: ProjectContext | null = null;
  private contextId: string | null = null;
  private issueOutcome: Outcome | null = null;
  private reportOutcome: Outcome | null = null;
  /** 当前打开的问题详情身份（来自清单回执行，不猜）。 */
  private issueDetail: Row | null = null;
  private issueDetailOutcome: Outcome | null = null;
  private copyNote: string | null = null;
  /** 事件续读：游标只在回执明确给出新游标时前移，拒绝时保持原游标。 */
  private eventCursor: string | null = null;
  private eventOutcome: Outcome | null = null;
  private eventLog: EventRow[] = [];
  private eventNote: string | null = null;
  /** 复测简报（`copy_repair_brief`，只读）：按当前问题身份读取，界面原样展示。 */
  private briefOutcome: Outcome | null = null;
  private briefIssueId: string | null = null;
  private briefNote: string | null = null;
  /** 最近一次只读查询的作用域：回执只有动作名，靠它把结果归位（不猜）。 */
  private pendingQuery: "issues" | "reports" | "issue-detail" | null = null;
  private pendingRequest: { id: string; action: string } | null = null;
  private requestSequence = 0;
  private readonly requestPrefix = `panel-${Date.now().toString(36)}`;
  private doctorOutcome: Outcome | null = null;
  private commandDraft = "";
  private commandNote = "";
  private projectName = "";
  private projectGoal = "";
  private workflowAction = "publish_plan";
  private workflowValues: Record<string,string> = {expected_revision:"0"};
  private viewOutcomes = new Map<string, Outcome>();

  private readonly receiveMessage = (event: MessageEvent): void => {
      const data = event.data as Outcome | ProjectContext | undefined;
      if (!data || typeof data !== "object") { return; }
      if (data.kind === "projectContext") {
        if (typeof data.project_id !== "string" || data.project_id.length > 128
            || /[\u0000-\u001f]/.test(data.project_id)
            || (data.run_id != null && (typeof data.run_id !== "string" || data.run_id.length > 128))) { return; }
        const next = data.project_id.trim()
          ? { kind: "projectContext", project_id: data.project_id, run_id: data.run_id ?? null,
              scope_name: typeof data.scope_name === "string" ? data.scope_name : null,
              context_id: data.context_id, } as ProjectContext
          : null;
        if (JSON.stringify(next) !== JSON.stringify(this.project)) {
          if (this.contextId && data.context_id !== this.contextId) {
            this.doctorOutcome = null;
          }
          this.issueOutcome = this.reportOutcome = this.issueDetailOutcome = null;
          this.eventOutcome = this.briefOutcome = null;
          this.eventLog = []; this.eventCursor = this.eventNote = null;
          this.briefIssueId = this.briefNote = null;
          this.viewOutcomes.clear(); this.pendingQuery = null; this.outcome = null;
          this.commandDraft = ""; this.commandNote = "项目上下文变化，请重新核对操作输入。";
          this.projectName = this.projectGoal = "";
          this.workflowValues = {expected_revision:"0"};
          if (this.pendingRequest && !["doctor", "ensure-core"].includes(this.pendingRequest.action)) {
            this.pendingRequest = null; this.pending = false;
          }
        }
        const contextChanged = this.contextId !== null && data.context_id !== this.contextId;
        this.contextId = typeof data.context_id === "string" ? data.context_id : null;
        this.project = next;
        this.render();
        if (contextChanged && !this.pending) { this.dispatch("doctor"); }
        return;
      }
      if (data.kind !== "outcome") { return; }
      if (!["ok", "refused", "unavailable"].includes(data.status)) { return; }
      const correlation = data.clientRequestId ?? data.response?.["request_id"];
      if (this.pendingRequest) {
        if (correlation !== this.pendingRequest.id || data.action !== this.pendingRequest.action) { return; }
      } else if (this.host !== null || data.action !== "doctor") { return; }
      this.outcome = data;
      if (data.action === "doctor") { this.doctorOutcome = data; }
      if (data.action === "query") {
        // 按**本次查询的作用域**归位；没有待归位作用域时只更新最近回执。
        if (this.pendingQuery === "issues") { this.issueOutcome = data; }
        if (this.pendingQuery === "reports") { this.reportOutcome = data; }
        if (this.pendingQuery === "issue-detail") { this.issueDetailOutcome = data; }
        this.pendingQuery = null;
      }
      if (data.action === "events") {
        this.applyEventOutcome(data);
      }
      if (data.action === "copy_repair_brief") {
        this.briefOutcome = data;
        this.briefNote = data.status === "ok" ? null : "核心未通过：简报未取得。";
      }
      if (data.action === "read_view") {
        const result = data.response?.["result"] as Record<string, unknown> | undefined;
        const view = result?.["view"] as Record<string, unknown> | undefined;
        const kind = result?.["view_type"];
        if (data.status === "ok" && this.project && typeof kind === "string"
            && ["run", "panel", "delivery"].includes(kind)
            && typeof view === "object" && view !== null && !Array.isArray(view)
            && view["project_id"] === this.project.project_id
            && (kind === "delivery" || view["run_id"] === this.project.run_id)
            && Number.isSafeInteger(result?.["commit_id"])
            && Number(result?.["commit_id"]) >= 0
            && typeof result?.["event_cursor"] === "string" && result["event_cursor"].length > 0) {
          this.viewOutcomes.set(kind, data);
        }
      }
      this.pendingRequest = null;
      this.pending = false;
      this.render();
  };

  connectedCallback(): void {
    window.addEventListener("message", this.receiveMessage);
    this.render();
    this.dispatch("doctor");
  }

  disconnectedCallback(): void {
    window.removeEventListener("message", this.receiveMessage);
    this.pendingRequest = null; this.pendingQuery = null; this.pending = false;
  }

  /**
   * 事件续读的归位（设计 §12「已提交事件增量刷新」）：
   * - 成功：把回执行**追加**到事件流（去重按事件序号），并把游标前移到回执给出的 `next_cursor`；
   * - 拒绝（游标非法/需维护）：原样显示状态与代码，**保持原游标与既有事件行**，绝不静默重置；
   * - 界面不重算业务顺序：业务顺序看 `commit_sequence`，只如实展示。
   */
  private applyEventOutcome(data: Outcome): void {
    this.eventOutcome = data;
    const resultStatus = (data.response?.["result"] as Record<string, unknown> | undefined)
      ?.["status"];
    // 真实 `events` 动作把 `invalid_cursor`/`maintenance_required` 放在 result.status 里
    // （宿主层 status 仍是 ok）：两者都必须原样显示，并保持游标与既有事件行。
    if (data.status !== "ok" || (typeof resultStatus === "string" && resultStatus !== "ok")) {
      const code = data.code ?? (typeof resultStatus === "string" ? resultStatus : data.status);
      this.eventNote = `核心未通过 · ${code}：${data.message ?? ""}`
        + "（游标与既有事件行保持不变）";
      return;
    }
    const rows = eventRowsOf(data.response);
    for (const row of rows) {
      const duplicate = row.eventSequence !== null
        && this.eventLog.some(seen => seen.eventSequence === row.eventSequence);
      if (!duplicate) { this.eventLog.push(row); }
    }
    const cursor = nextCursorOf(data.response);
    if (rows.length === 0) {
      this.eventNote = "没有新事件（回执为空；已到已知边界）。";
    } else {
      this.eventNote = `已追加 ${rows.length} 条事件（去重按事件序号）。`;
    }
    if (cursor !== null) {
      this.eventCursor = cursor;
    } else if (rows.length > 0) {
      this.eventNote += " 回执未给出新游标：游标保持不变。";
    }
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
    const clientRequestId = `${this.requestPrefix}-${++this.requestSequence}`;
    this.pendingRequest = { id: clientRequestId, action };
    const message: Record<string, unknown> = {
      kind: "dispatch", action, parameters: parameters ?? {}, clientRequestId,
    };
    if (this.project !== null) { message["projectId"] = this.project.project_id; }
    this.host.postMessage(message);
    this.render();
  }

  /** 项目作用域的只读查询：没有宿主项目上下文时不派发（不猜项目身份）。 */
  private dispatchScoped(action: string, parameters?: Record<string, unknown>): void {
    if (this.project === null) {
      this.outcome = {
        kind: "outcome", action, status: "refused", code: "PROJECT_NOT_SELECTED",
        message: "宿主尚未声明当前项目；面板不猜项目身份，请先在编辑器选择项目",
      };
      this.render();
      return;
    }
    this.dispatch(action, parameters);
  }

  private statusText(): string {
    if (this.pending) { return "已请求核心，等待回执…"; }
    const outcome = this.doctorOutcome ?? this.outcome;
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
    const connect = document.createElement("button");
    connect.textContent = "连接核心";
    connect.dataset.action = "ensure-core";
    connect.disabled = this.pending || this.host === null;
    connect.title = this.host === null
      ? "不在编辑器宿主中，面板未连接核心"
      : "发现或启动该工作空间的唯一核心（宿主职责，不派发业务动作）";
    connect.onclick = () => { this.ensureCore(); };
    const refresh = document.createElement("button");
    refresh.textContent = "刷新核心状态";
    refresh.dataset.action = "refresh-core";
    refresh.disabled = this.pending;
    refresh.onclick = () => { this.dispatch("doctor"); };
    const scope = document.createElement("p"); scope.className = "scope";
    scope.dataset.testid = "project-context";
    if (this.project === null) {
      scope.textContent = "当前项目：未选择（宿主尚未声明项目上下文）· 一期只读入口";
    } else {
      const run = this.project.run_id ? ` · 运行 ${this.project.run_id}` : "";
      const name = this.project.scope_name ? ` · 范围 ${this.project.scope_name}` : "";
      scope.textContent = `当前项目：${this.project.project_id}${run}${name}`;
    }
    header.append(title, status, connect, refresh, scope);
    return header;
  }

  /** 宿主职责：发现或启动唯一核心；本动作不派发业务动作、不写业务记录。 */
  private ensureCore(): void {
    if (this.host === null) {
      return;
    }
    this.pending = true;
    const clientRequestId = `${this.requestPrefix}-${++this.requestSequence}`;
    this.pendingRequest = { id: clientRequestId, action: "ensure-core" };
    this.host.postMessage({ kind: "ensureCore", clientRequestId });
    this.render();
  }

  private currentView(): HTMLElement {
    const fragment = document.createElement("div");
    fragment.append(
      this.phaseSection(),
      this.capabilitySection(),
      this.controlledSection(),
      this.readOnlySection(),
      this.eventStreamSection(),
      this.resultSection(),
      this.aiSection(),
      this.actionSection(),
      this.nextSection(),
      this.detailSection(),
    );
    return fragment;
  }

  private controlledSection(): HTMLElement {
    const section = document.createElement("section");
    section.dataset.testid = "controlled-entry";
    const title = document.createElement("h2"); title.textContent = "受控操作";
    const note = document.createElement("p"); note.className = "muted";
    const capabilities = this.doctorOutcome?.response?.["result"] as Record<string, unknown> | undefined;
    const nativeAvailable = Array.isArray(capabilities?.["transport_capabilities"])
      && capabilities["transport_capabilities"].includes("native-confirmation/1");
    note.textContent = "先配置项目编号。此处请求核心窗口确认完整输入；窗口确认前不会提交。"
      + "计划、环境和执行仍须满足核心门禁。未知结果保留原意图查询，不自动重跑。";
    const create = document.createElement("button"); create.textContent = "填写新建项目";
    const name = document.createElement("input");
    name.setAttribute("aria-label", "项目名称"); name.placeholder = "项目名称";
    name.value = this.projectName; name.disabled = this.pending;
    name.oninput = () => { this.projectName = name.value; };
    const goal = document.createElement("textarea");
    goal.setAttribute("aria-label", "实际测试目标"); goal.placeholder = "填写实际测试目标";
    goal.rows = 3; goal.value = this.projectGoal; goal.disabled = this.pending;
    goal.oninput = () => { this.projectGoal = goal.value; };
    create.disabled = this.pending || this.project === null || this.host === null || !nativeAvailable;
    create.onclick = () => {
      const workspaceId = this.doctorOutcome?.response?.["workspace_id"];
      if (typeof workspaceId !== "string" || this.project === null) {
        this.commandNote = "请先刷新核心状态，取得已核实的工作空间身份。";
      } else if (!this.projectName.trim() || !this.projectGoal.trim()) {
        this.commandNote = "请填写项目名称和实际测试目标。";
      } else {
        const intentId = Array.from(crypto.getRandomValues(new Uint8Array(16)),
          value => value.toString(16).padStart(2, "0")).join("");
        this.commandDraft = JSON.stringify({action:"save_context", intent_id:intentId,
          expected_revision:0, parameters:{project:{schema_version:"aitest.project/2.0",
            local_project_id:this.project.project_id, project_id:this.project.project_id,
            workspace_id:workspaceId, name:this.projectName.trim(), goal:this.projectGoal.trim(),
            revision:1, created_at_commit:"@core-assigned", modules:[]}}}, null, 2);
        this.commandNote = "项目输入已准备，请求核心确认后保存。已有同编号项目会拒绝覆盖。";
        this.outcome = null;
      }
      this.render();
    };
    const draft = document.createElement("textarea");
    draft.setAttribute("aria-label", "受控操作 JSON"); draft.rows = 14;
    draft.placeholder = '{"action":"…","intent_id":"稳定业务意图","expected_revision":0,"parameters":{}}';
    draft.value = this.commandDraft; draft.disabled = this.pending;
    draft.oninput = () => { this.commandDraft = draft.value; };
    const submit = document.createElement("button"); submit.textContent = "请求核心确认并提交";
    submit.disabled = this.pending || this.project === null || this.host === null || !nativeAvailable;
    if (!nativeAvailable) { note.textContent += " 当前核心尚未声明原生确认能力，请连接并刷新新版核心。"; }
    submit.onclick = () => {
      try {
        const raw: unknown = JSON.parse(this.commandDraft);
        if (!raw || typeof raw !== "object" || Array.isArray(raw)) { throw new Error("输入须为 JSON 对象"); }
        const command = raw as Record<string, unknown>;
        const allowed = ["action", "intent_id", "expected_revision", "binding_revision", "target", "parameters"];
        if (Object.keys(command).some(key => !allowed.includes(key))
            || typeof command["action"] !== "string" || typeof command["intent_id"] !== "string"
            || !Number.isSafeInteger(command["expected_revision"]) || Number(command["expected_revision"]) < 0
            || !command["parameters"] || typeof command["parameters"] !== "object"
            || Array.isArray(command["parameters"])) { throw new Error("请核对动作、意图、预期修订和参数"); }
        const clientRequestId = `${this.requestPrefix}-${++this.requestSequence}`;
        this.pendingRequest = {id:clientRequestId, action:command["action"]};
        this.pending = true;
        this.commandNote = "已请求核心窗口；请核对窗口里的准确输入和保存依据。";
        this.host?.postMessage({kind:"controlledDispatch", clientRequestId, contextId:this.contextId,
          action:command["action"], intentId:command["intent_id"],
          expectedRevision:command["expected_revision"], bindingRevision:command["binding_revision"],
          target:command["target"], parameters:command["parameters"], projectId:this.project?.project_id});
      } catch (error) {
        this.commandNote = error instanceof Error ? error.message : "操作输入无效";
      }
      this.render();
    };
    const status = document.createElement("p"); status.textContent = this.commandNote;
    const receipt = document.createElement("pre");
    receipt.style.whiteSpace = "pre-wrap"; receipt.style.overflowWrap = "anywhere";
    receipt.textContent = this.outcome?.response ? JSON.stringify(this.outcome.response, null, 2) : "";
    if (this.outcome && this.outcome.action !== "doctor") {
      status.textContent = this.outcome.status === "ok"
        ? "核心已返回本次操作结果；请以回执中的具体业务状态为准。"
        : `${this.outcome.code ?? this.outcome.status}：${this.outcome.message ?? "操作结果尚未核实"}`;
    }
    section.append(title, note, name, goal, create, this.workflowSection(), draft, submit, status, receipt);
    return section;
  }

  private workflowSection(): HTMLElement {
    const section=document.createElement("section"); section.dataset.testid="workflow";
    const title=document.createElement("h3"); title.textContent="计划 → 执行 → 核验 → 报告";
    const note=document.createElement("p");
    note.textContent="逐项准备输入，再请求核心确认。解析、授权、执行同一步骤必须沿用同一业务意图；"
      + "其他动作使用各自意图。回执未知时保留原意图，不把新意图当重试。计划/准备仍需准确已保存材料。";
    const choice=document.createElement("select"); choice.setAttribute("aria-label","流程动作");
    choice.disabled=this.pending;
    for (const item of workflowActions) {
      const option=document.createElement("option"); option.value=item.action; option.textContent=item.title;
      choice.append(option);
    }
    choice.value=this.workflowAction;
    choice.onchange=()=>{
      const execution=["prepare_execution","authorize_step","execute_step"];
      if (!(execution.includes(this.workflowAction) && execution.includes(choice.value))) {
        this.workflowValues["intent_id"]="";
      }
      this.workflowValues["expected_revision"]="0";
      this.workflowAction=choice.value; this.render();
    };
    section.append(title,note,choice);
    const fields=[{key:"intent_id",label:"本动作稳定业务意图"},
      {key:"expected_revision",label:"预期仓库修订（首写为 0）"},
      ...workflowActions.find(item=>item.action===this.workflowAction)!.fields];
    for (const field of fields) {
      const label=document.createElement("label"); label.textContent=field.label;
      const input=document.createElement("textarea"); input.rows=("kind" in field && field.kind==="json") ? 5 : 1;
      input.setAttribute("aria-label",field.label); input.value=this.workflowValues[field.key] ?? "";
      input.disabled=this.pending; input.oninput=()=>{this.workflowValues[field.key]=input.value;};
      label.append(input); section.append(label);
    }
    const fill=document.createElement("button"); fill.textContent="准备本步骤操作";
    fill.disabled=this.pending || !this.project || !this.host;
    fill.onclick=()=>{
      try {
        this.commandDraft=JSON.stringify(workflowCommand(this.workflowAction,this.workflowValues),null,2);
        this.commandNote="本步骤输入已准备；核对下方内容后请求核心确认。尚未提交。";
        this.outcome=null;
      } catch(error) {this.commandNote=error instanceof Error ? error.message : "流程输入无效"; this.outcome=null;}
      this.render();
    };
    const useReceipt=document.createElement("button"); useReceipt.textContent="从本次成功回执填入记录编号";
    useReceipt.disabled=this.pending || this.outcome?.status!=="ok" || this.outcome?.action==="doctor";
    useReceipt.onclick=()=>{
      const result=this.outcome?.response?.["result"] as Record<string,unknown> | undefined;
      if (result) {
        if (this.outcome?.action==="prepare_run" && result["status"]==="prepared"
            && typeof result["prepared_run_id"]==="string") {
          this.workflowValues["prepared_run_id"]=result["prepared_run_id"];
        }
        if (this.outcome?.action==="prepare_execution" && typeof result["execution_action_id"]==="string") {
          this.workflowValues["execution_action_id"]=result["execution_action_id"];
        }
        const facts=(result["execution_facts"] ?? (this.outcome?.action==="register_run" ? result : null)) as Record<string,unknown> | null;
        if (facts && facts["project_id"]===this.project?.project_id) {
          for (const key of ["run_id","snapshot_commit_id"]) {
            if (typeof facts[key]==="string") {
              this.workflowValues[key==="snapshot_commit_id" ? "base_snapshot_commit_id" : key]=facts[key];
            }
          }
          const attempts=facts["current_attempt_by_step"] as Record<string,unknown> | undefined;
          const attempt=attempts?.[this.workflowValues["step_id"] ?? ""];
          if (typeof attempt==="string") {this.workflowValues["attempt_id"]=attempt;}
        }
      }
      this.commandNote="已填入回执明确给出的编号；步骤选择和业务意图仍需核对。";
      this.render();
    };
    section.append(fill,useReceipt);
    return section;
  }

  /** 已提交事件增量刷新（设计 §12）：展示真实事件行与游标，界面不重算业务顺序。 */
  private eventStreamSection(): HTMLElement {
    const section = document.createElement("section");
    section.dataset.testid = "event-stream";
    const title = document.createElement("h2"); title.textContent = "已提交事件（增量续读）";
    const cursor = document.createElement("p");
    cursor.className = "muted";
    cursor.dataset.testid = "event-cursor";
    cursor.textContent = this.eventCursor === null
      ? "尚未取得游标（首次读取从头开始）。"
      : `当前游标：${this.eventCursor}`;
    section.append(title, cursor);
    const outcome = this.eventOutcome;
    const resultStatus = (outcome?.response?.["result"] as Record<string, unknown> | undefined)
      ?.["status"];
    if (
      outcome !== null
      && (outcome.status !== "ok" || (typeof resultStatus === "string" && resultStatus !== "ok"))
    ) {
      const failed = document.createElement("p");
      failed.className = "muted";
      failed.textContent = this.eventNote ?? "核心未通过。";
      section.append(failed);
    }
    if (this.eventLog.length === 0) {
      const empty = document.createElement("p"); empty.className = "muted";
      empty.dataset.testid = "event-empty";
      empty.textContent = outcome === null
        ? "尚未读取事件。"
        : this.eventNote ?? "回执里没有事件行（空结果如实显示，不补造）。";
      section.append(empty);
      return section;
    }
    const list = document.createElement("ul");
    for (const row of this.eventLog) {
      const item = document.createElement("li");
      const sequence = row.eventSequence === null ? "?" : String(row.eventSequence);
      const commit = row.commitSequence === null ? "?" : String(row.commitSequence);
      const revision = row.revision === null ? "?" : String(row.revision);
      item.dataset.eventSequence = sequence;
      // 业务顺序看提交序号（设计 §12 只读规则）；不计算时长、不做聚合。
      item.textContent =
        `#${sequence} · 提交 ${commit} · ${row.eventType} · ${row.recordId}@${revision}`;
      list.append(item);
    }
    section.append(list);
    if (this.eventNote !== null) {
      const note = document.createElement("p");
      note.className = "muted";
      note.dataset.testid = "event-note";
      note.textContent = this.eventNote;
      section.append(note);
    }
    return section;
  }

  /** 最近一次回执的原始结果：**原样展示**，界面不重算任何业务结论。 */
  private resultSection(): HTMLElement {
    const section = document.createElement("section");
    section.dataset.testid = "last-result";
    const title = document.createElement("h2"); title.textContent = "最近回执";
    const outcome = this.outcome;
    if (outcome === null || outcome.response === undefined) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = outcome === null
        ? "尚无回执。"
        : `未取得结果：${outcome.code ?? outcome.status}`;
      section.append(title, empty);
      return section;
    }
    const body = document.createElement("pre");
    body.dataset.testid = "last-result-body";
    body.style.whiteSpace = "pre-wrap";
    body.style.maxHeight = "240px";
    body.style.overflow = "auto";
    body.textContent = JSON.stringify(outcome.response, null, 2);
    section.append(title, body);
    return section;
  }

  /** 核心能力：只显示**真实回执**里的 supported_actions，不推断、不补造。 */
  private capabilitySection(): HTMLElement {
    const section = document.createElement("section");
    section.dataset.testid = "core-capability";
    const title = document.createElement("h2"); title.textContent = "核心能力（真实回执）";
    const outcome = this.doctorOutcome ?? (this.outcome?.action === "doctor" ? this.outcome : null);
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
    // 项目作用域的查询只在宿主声明项目后可用：面板绝不猜项目身份。
    const scoped = this.project !== null;
    const entries: ReadonlyArray<
      readonly [string, string, Record<string, unknown>, "issues" | "reports" | null]
    > = [
      ["问题清单（未关闭）", "query", { view: "OPEN", limit: 20 }, "issues"],
      ["报告修订", "query", this.project?.run_id
        ? { run_id: this.project.run_id, limit: 5 }
        : { report_id: "unknown", limit: 5 }, "reports"],
      ["空间诊断", "storage_usage", {}, null],
      ["事件续读", "events", { limit: 20 }, null],
    ];
    for (const [label, action, parameters, scope] of entries) {
      const button = document.createElement("button");
      button.textContent = label;
      button.dataset.action = action;
      const isScoped = action === "query";
      button.disabled = this.pending || this.host === null || (isScoped && !scoped);
      button.title = this.host === null
        ? "不在编辑器宿主中，面板未连接核心"
        : (isScoped && !scoped)
          ? "宿主尚未声明当前项目；面板不猜项目身份"
          : "";
      button.onclick = () => {
        if (isScoped) {
          this.pendingQuery = scope;
          this.dispatchScoped(action, parameters);
        } else if (action === "events") {
          // 增量续读：有游标就带游标（不重头读），没有就不带。
          this.dispatch(action, this.eventCursor === null
            ? { limit: 20 }
            : { limit: 20, cursor: this.eventCursor });
        } else {
          this.dispatch(action, parameters);
        }
      };
      actions.append(button);
    }    const note = document.createElement("p"); note.className = "muted";
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
    const response = this.viewOutcomes.get("panel")?.response;
    const result = response?.["result"] as Record<string, unknown> | undefined;
    const view = result?.["view"] as Record<string, unknown> | undefined;
    const stageNames: Record<string, string> = { pre_run: "运行前", runtime: "运行时", post_run: "运行后" };
    const active = typeof view?.["stage"] === "string" ? stageNames[view["stage"]] : undefined;
    const grid = document.createElement("div"); grid.className = "phases";
    for (const [phase, state, description] of phases) {
      const card = document.createElement("article"); card.className = "phase";
      card.dataset.phase = phase;
      const name = document.createElement("h3"); name.textContent = phase;
      const current = document.createElement("p"); current.textContent = state;
      const detail = document.createElement("p"); detail.className = "muted"; detail.textContent = description;
      if (active === phase && view) {
        card.setAttribute("aria-current", "step");
        current.textContent = `当前 · ${String(view["control_state"] ?? "待核实")}`;
        detail.textContent = `业务结论：${String(view["business_outcome"] ?? "待核实")} · 档位：${String(view["tier"] ?? "待核实")} · 驱动：${String(view["driver"] ?? "待核实")}`;
      }
      card.append(name, current, detail); grid.append(card);
    }
    section.append(grid);
    const refresh = document.createElement("button"); refresh.textContent = "刷新本次视图";
    refresh.dataset.testid = "refresh-panel-view";
    const capabilities = this.doctorOutcome?.response?.["result"] as Record<string, unknown> | undefined;
    refresh.disabled = this.pending || this.host === null || !this.project?.run_id
      || !Array.isArray(capabilities?.["supported_actions"]) || !capabilities["supported_actions"].includes("read_view");
    refresh.onclick = () => this.dispatchScoped("read_view", { view: "panel", run_id: this.project?.run_id ?? "" });
    const observed = document.createElement("p"); observed.className = "muted"; observed.dataset.testid = "panel-view-boundary";
    observed.textContent = active && result ? `已保存视图 · 提交 ${String(result["commit_id"])} · 事件边界 ${String(result["event_cursor"])}` : "尚未取得当前运行的准确视图。";
    section.append(refresh, observed);
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
    section.append(back, title);
    if (this.detail === "review") {
      section.append(...this.reviewDetail());
      return section;
    }
    const description = document.createElement("p"); description.textContent = selected[2];
    const note = document.createElement("p"); note.className = "muted";
    note.textContent = "详情骨架：等待核心 DTO 与事件接入；本面板不计算业务结论。";
    section.append(description, note);
    return section;
  }

  /** 问题与报告：把**真实只读回执**里的记录行原样列出，不重算结论。 */
  private reviewDetail(): HTMLElement[] {
    const blocks: HTMLElement[] = [];
    const actions = document.createElement("div"); actions.className = "actions";
    const issueButton = document.createElement("button");
    issueButton.textContent = "读取问题清单";
    issueButton.dataset.action = "query";
    issueButton.dataset.scope = "issues";
    issueButton.disabled = this.pending || this.host === null || this.project === null;
    issueButton.onclick = () => {
      this.pendingQuery = "issues";
      this.dispatchScoped("query", { view: "OPEN", limit: 20 });
    };
    const reportButton = document.createElement("button");
    reportButton.textContent = "读取报告修订";
    reportButton.dataset.action = "query";
    reportButton.dataset.scope = "reports";
    reportButton.disabled = this.pending || this.host === null || this.project === null
      || !this.project.run_id;
    reportButton.onclick = () => {
      if (this.project?.run_id) {
        this.pendingQuery = "reports";
        this.dispatchScoped("query", { run_id: this.project.run_id, limit: 5 });
      }
    };
    actions.append(issueButton, reportButton);
    blocks.push(actions);
    if (this.project === null) {
      const blocked = document.createElement("p"); blocked.className = "muted";
      blocked.dataset.testid = "review-blocked";
      blocked.textContent = "宿主尚未声明当前项目：面板不猜项目身份，问题与报告读取保持不可用。";
      blocks.push(blocked);
      return blocks;
    }
    blocks.push(
      this.rowsBlock("问题清单（未关闭）", this.issueOutcome, "review-issues", true),
      this.rowsBlock("报告修订", this.reportOutcome, "review-reports"),
    );
    if (this.issueDetail !== null) {
      blocks.push(this.issueDetailBlock(this.issueDetail));
    }
    return blocks;
  }

  /** 问题详情（设计 §10/§12）：**只读**回执原样展示 + 复制说明（不执行修复）。 */
  private issueDetailBlock(row: Row): HTMLElement {
    const block = document.createElement("div");
    block.dataset.testid = "issue-detail";
    const heading = document.createElement("h2");
    heading.textContent = `问题详情 ${row.record_id}@${row.revision}`;
    block.append(heading);
    const outcome = this.issueDetailOutcome;
    // 复测简报只需要**问题身份**（来自清单回执），不依赖详情载荷：
    // 详情回执缺失或只含记录行时，简报入口同样可用。
    const brief = this.briefBlock(row);
    if (outcome === null) {
      const empty = document.createElement("p"); empty.className = "muted";
      empty.textContent = "尚未读取该问题详情。";
      block.append(empty, this.copyButton(row, null), brief);
      return block;
    }
    if (outcome.status !== "ok") {
      const failed = document.createElement("p"); failed.className = "muted";
      failed.textContent =
        `核心未通过 · ${outcome.code ?? outcome.status}：${outcome.message ?? ""}`;
      block.append(failed, this.copyButton(row, null), brief);
      return block;
    }
    const result = (outcome.response?.["result"] ?? {}) as Record<string, unknown>;
    const nested = result[String(result["aggregate_kind"] ?? "")];
    // 只有回执**真的带回一条记录**时才算详情载荷：B `query` 目前只回
    // `{aggregate_kind, record_id, revision}` 行（列表读摘要），此时不得把
    // 分页对象当成问题记录，也不得让"复制说明"复制一份页码。
    const candidate = result["record"] ?? nested;
    const record = (
      candidate !== undefined && candidate !== null && typeof candidate === "object"
        ? candidate
        : null
    ) as Record<string, unknown> | null;
    if (record === null) {
      const rowsOnly = document.createElement("p");
      rowsOnly.className = "muted";
      rowsOnly.dataset.testid = "issue-detail-rows-only";
      rowsOnly.textContent =
        "该回执只含记录行（列表读摘要），未包含详情载荷：字段不推断，详情读取需按引用取。";
      block.append(rowsOnly);
    }
    const fields = document.createElement("dl");
    for (const [key, label] of issueFields) {
      const value = record?.[key];
      const term = document.createElement("dt"); term.textContent = label;
      const detail = document.createElement("dd");
      detail.dataset.field = key;
      // 回执里没有该字段时如实说明"未提供"，绝不按状态推断内容。
      detail.textContent = value === undefined || value === null
        ? "回执未提供该字段（不推断）。"
        : typeof value === "string" ? value : JSON.stringify(value);
      fields.append(term, detail);
    }
    const raw = document.createElement("pre");
    raw.dataset.testid = "issue-detail-body";
    raw.style.whiteSpace = "pre-wrap";
    raw.style.maxHeight = "200px";
    raw.style.overflow = "auto";
    raw.textContent = JSON.stringify(outcome.response, null, 2);
    block.append(fields, this.copyButton(row, record), raw, brief);
    return block;
  }

  /**
   * 复测简报（只读，`copy_repair_brief`）：按**当前问题身份**与宿主声明的 run 读取，
   * 简报正文与元数据原样展示；未知位置与排除项如实列出（设计 §10「复制说明不执行修复」同理）。
   */
  private briefBlock(row: Row): HTMLElement {
    const wrapper = document.createElement("div");
    wrapper.dataset.testid = "repair-brief";
    const read = document.createElement("button");
    read.textContent = "读取复测简报";
    read.dataset.action = "copy_repair_brief";
    read.disabled = this.pending || this.host === null || this.project === null;
    read.title = this.project === null
      ? "宿主尚未声明当前项目；面板不猜项目身份"
      : "按当前问题身份读取复测简报（只读，不修改任何记录）";
    read.onclick = () => {
      const parameters: Record<string, unknown> = { issue_id: row.record_id };
      if (this.project?.run_id) { parameters["run_id"] = this.project.run_id; }
      this.briefIssueId = row.record_id;
      this.briefOutcome = null;
      this.briefNote = null;
      this.dispatchScoped("copy_repair_brief", parameters);
    };
    wrapper.append(read);
    const outcome = this.briefOutcome;
    if (outcome === null) {
      const empty = document.createElement("p"); empty.className = "muted";
      empty.dataset.testid = "repair-brief-empty";
      empty.textContent = "尚未读取复测简报。";
      wrapper.append(empty);
      return wrapper;
    }
    if (outcome.status !== "ok") {
      const failed = document.createElement("p"); failed.className = "muted";
      failed.dataset.testid = "repair-brief-error";
      failed.textContent =
        `核心未通过 · ${outcome.code ?? outcome.status}：${outcome.message ?? ""}`
        + "（未取得简报正文）";
      wrapper.append(failed);
      return wrapper;
    }
    const result = (outcome.response?.["result"] ?? {}) as Record<string, unknown>;
    const brief = result["brief"];
    const body = document.createElement("pre");
    body.dataset.testid = "repair-brief-body";
    body.style.whiteSpace = "pre-wrap";
    body.style.maxHeight = "240px";
    body.style.overflow = "auto";
    // 简报正文原样展示；回执没给正文时如实说明，不用别的内容代替。
    body.textContent = typeof brief === "string" && brief.length > 0
      ? brief
      : "回执未提供简报正文（不补造）。";
    const meta = document.createElement("dl");
    const metaFields: ReadonlyArray<readonly [string, string]> = [
      ["facts_state", "事实状态"],
      ["unknown_positions", "待核实位置"],
      ["excluded", "已排除项"],
      ["redaction_policy_version", "脱敏策略"],
    ];
    for (const [key, label] of metaFields) {
      const value = result[key];
      const term = document.createElement("dt"); term.textContent = label;
      const detail = document.createElement("dd");
      detail.dataset.briefField = key;
      detail.textContent = value === undefined || value === null
        ? "回执未提供该字段（不推断）。"
        : typeof value === "string" ? value : JSON.stringify(value);
      meta.append(term, detail);
    }
    const copy = document.createElement("button");
    copy.textContent = "复制简报";
    copy.dataset.action = "copy-brief";
    copy.disabled = typeof brief !== "string" || brief.length === 0;
    copy.title = "复制简报正文；本按钮不派发核心动作、不执行修复";
    copy.onclick = () => {
      const clipboard = (globalThis as {
        navigator?: { clipboard?: { writeText?: (v: string) => Promise<void> } };
      }).navigator?.clipboard;
      if (clipboard?.writeText && typeof brief === "string") {
        void clipboard.writeText(brief);
        this.briefNote = "已复制简报（未派发核心动作、未执行修复）。";
      } else {
        this.briefNote = "当前环境没有剪贴板或回执无正文：未复制，也未执行任何动作。";
      }
      this.render();
    };
    wrapper.append(body, meta, copy);
    if (this.briefNote !== null) {
      const note = document.createElement("p"); note.className = "muted";
      note.dataset.testid = "brief-note";
      note.textContent = this.briefNote;
      wrapper.append(note);
    }
    return wrapper;
  }

  /** 复制说明：只把回执里的文字放进剪贴板，**不派发任何动作**（设计 §10）。 */
  private copyButton(row: Row, record: Record<string, unknown> | null): HTMLElement {
    const wrapper = document.createElement("div");
    const button = document.createElement("button");
    button.textContent = "复制说明";
    button.dataset.action = "copy-description";
    button.disabled = record === null;
    button.title = record === null
      ? "回执未包含详情载荷：没有可复制的说明，本按钮不猜内容"
      : "复制该问题的说明文字；本按钮不执行修复、不派发核心动作";
    button.onclick = () => {
      const text = typeof record?.["description"] === "string"
        ? String(record["description"])
        : JSON.stringify(record ?? {}, null, 2);
      const clipboard = (globalThis as { navigator?: { clipboard?: { writeText?: (v: string) => Promise<void> } } })
        .navigator?.clipboard;
      if (clipboard?.writeText) {
        void clipboard.writeText(text);
        this.copyNote = "已复制说明（未执行修复、未派发核心动作）。";
      } else {
        this.copyNote = "当前环境没有剪贴板权限：未复制，也未执行任何动作。";
      }
      this.render();
    };
    wrapper.append(button);
    if (this.copyNote !== null) {
      const note = document.createElement("p");
      note.className = "muted";
      note.dataset.testid = "copy-note";
      note.textContent = this.copyNote;
      wrapper.append(note);
    }
    return wrapper;
  }

  /** 一个只读列表块：只渲染回执里的 record_id/修订，缺回执即如实说明。 */
  private rowsBlock(
    titleText: string, outcome: Outcome | null, testId: string, selectable = false,
  ): HTMLElement {
    const block = document.createElement("div");
    block.dataset.testid = testId;
    const heading = document.createElement("h2"); heading.textContent = titleText;
    block.append(heading);
    if (outcome === null) {
      const empty = document.createElement("p"); empty.className = "muted";
      empty.textContent = "尚未读取。";
      block.append(empty);
      return block;
    }
    if (outcome.status !== "ok") {
      const failed = document.createElement("p"); failed.className = "muted";
      failed.textContent = `核心未通过 · ${outcome.code ?? outcome.status}：${outcome.message ?? ""}`;
      block.append(failed);
      return block;
    }
    const rows = rowsOf(outcome.response);
    if (rows.length === 0) {
      const none = document.createElement("p"); none.className = "muted";
      none.textContent = "回执里没有记录行（空结果如实显示，不补造）。";
      block.append(none);
      return block;
    }
    const list = document.createElement("ul");
    for (const row of rows) {
      const item = document.createElement("li");
      item.dataset.recordId = row.record_id;
      if (selectable) {
        // 可选中：点击按**回执行给出的身份**读取该记录详情（不猜 id、不猜修订）。
        const open = document.createElement("button");
        open.textContent = `${row.record_id}@${row.revision}（${row.aggregate_kind}）`;
        open.dataset.action = "open-issue";
        open.dataset.recordId = row.record_id;
        open.disabled = this.pending || this.project === null;
        open.onclick = () => {
          this.issueDetail = row;
          this.issueDetailOutcome = null;
          this.copyNote = null;
          this.pendingQuery = "issue-detail";
          this.dispatchScoped("query", {
            aggregate_kind: "issue", record_id: row.record_id, revision: row.revision, limit: 1,
          });
        };
        item.append(open);
      } else {
        item.textContent = `${row.record_id}@${row.revision}（${row.aggregate_kind}）`;
      }
      list.append(item);
    }
    block.append(list);
    return block;
  }
}

customElements.define("ai-test-panel", AITestPanel);
