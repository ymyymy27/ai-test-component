/**
 * New editor-native panel. The host protocol is the only contract; all business
 * conclusions are read from core outcomes and never recalculated here.
 */
import { styles } from "./styles";

type Page = "home" | "run" | "plan" | "report" | "settings" | "diagnostics";
type Outcome = {
  kind: "outcome"; action: string; status: string;
  response?: { result?: Record<string, unknown> };
  code?: string; message?: string; clientRequestId?: string;
};
type Project = { project_id: string; run_id?: string | null; scope_name?: string | null; context_id?: string | null };
type WizardCommand = {
  key: string; action: string; intent_id: string; expected_revision: number;
  binding_revision: number; target?: string; parameters: Record<string, unknown>;
};
type WizardState = {
  stage: "choose" | "model" | "environment" | "review" | "done";
  session: Record<string, unknown> | null;
  endpoint: string; model: string; timeout: string; reference: string;
  useModel: boolean; dataPolicy: string; confirmed: boolean;
  proposal: Record<string, unknown> | null; preview: Record<string, unknown> | null;
  queue: WizardCommand[]; index: number; receipts: Record<string, unknown>;
  pendingStage: string; busy: boolean; note: string;
};

const PLAIN = new Set([
  "save_context", "save_case", "save_task", "save_delivery", "save_acceptance",
  "save_dependency_graph", "save_project_view_preference", "save_rules",
  "generate_draft", "request_model_draft", "resolve_model_response", "save_plan",
  "analyze_project", "prepare_run", "register_run", "prepare_execution",
  "execute_step", "start_run", "pause_run", "resume_run", "cancel_run",
  "retry_step", "return_stage", "verify_pending", "attach_evidence",
  "record_agent_evidence", "import_external_result", "create_report",
  "export_report", "record_fix", "request_regression",
]);

function el<K extends keyof HTMLElementTagNameMap>(tag: K, value = "", cls = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.textContent = value;
  if (cls) node.className = cls;
  return node;
}
function button(label: string, click: () => void, disabled = false, primary = false): HTMLButtonElement {
  const node = el("button", label, primary ? "primary" : "");
  node.disabled = disabled;
  node.onclick = click;
  return node;
}
function obj(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}
function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}
function textValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
function friendly(value: unknown): string {
  const map: Record<string, string> = {
    passed: "通过", failed: "失败", incomplete: "未完成", not_applicable: "不适用",
    running: "运行中", paused: "已暂停", completed: "已完成", pending: "待处理",
    blocked: "受阻", cancelled: "已取消", ready: "可继续", verified: "已核验",
    pending_verification: "待核验", not_started: "未开始", quick: "快速检查",
    on_demand: "按需自测", full: "完整验证", planned: "按计划执行", stepwise: "逐步执行",
    matched: "匹配", mismatched: "不匹配", no_result: "无结果",
  };
  return value == null ? "待核实" : map[String(value)] ?? String(value);
}
function resultOf(outcome: Outcome | undefined): Record<string, unknown> | null {
  return obj(obj(outcome?.response)?.["result"]);
}
function view(outcome: Outcome | undefined): Record<string, unknown> | null {
  return obj(resultOf(outcome)?.["view"]);
}

class AITestPanel extends HTMLElement {
  private readonly shadow = this.attachShadow({ mode: "open" });
  private readonly host = (globalThis as unknown as {
    acquireVsCodeApi?: () => { postMessage(message: unknown): void };
  }).acquireVsCodeApi?.() ?? null;
  private project: Project | null = null;
  private page: Page = "home";
  private busy = false;
  private requestSeq = 0;
  private pendingId: string | null = null;
  private pendingAction: string | null = null;
  private doctor: Outcome | null = null;
  private outcomes = new Map<string, Outcome>();
  private views = new Map<string, Outcome>();
  private lastOutcome: Outcome | null = null;
  private selectedStep: string | null = null;
  private stepActions = new Map<string, Record<string, unknown>>();
  private lastReport: Record<string, unknown> | null = null;
  private readonly wizard: WizardState = {
    stage: "choose", session: null, endpoint: "https://api.deepseek.com",
    model: "deepseek-flash", timeout: "60", reference: "", useModel: true,
    dataPolicy: "", confirmed: false, proposal: null, preview: null,
    queue: [], index: 0, receipts: {}, pendingStage: "", busy: false, note: "",
  };
  private wizardRequest: string | null = null;
  private wizardAction = "";
  private settingsRequest: string | null = null;
  private modelProfiles: { profile_id: string; name: string }[] = [];
  private activeProfile = "";
  private profileName = "";
  private readonly receive = (event: MessageEvent): void => {
    const data = event.data as Record<string, unknown> | undefined;
    if (!data || typeof data !== "object") return;
    if (data["kind"] === "projectContext") {
      const projectId = textValue(data["project_id"]).trim();
      this.project = projectId ? {
        project_id: projectId,
        run_id: textValue(data["run_id"]) || null,
        scope_name: textValue(data["scope_name"]) || null,
        context_id: textValue(data["context_id"]) || null,
      } : null;
      this.views.clear(); this.outcomes.clear(); this.selectedStep = null; this.stepActions.clear();
      this.render();
      if (this.project && !this.doctor) this.dispatch("doctor");
      if (this.project?.run_id) this.readView("run");
      return;
    }
    if (data["kind"] === "wizardResult") {
      this.receiveWizard(data as unknown as Outcome);
      return;
    }
    if (data["kind"] !== "outcome") return;
    const outcome = data as unknown as Outcome;
    this.lastOutcome = outcome;
    const id = outcome.clientRequestId;
    if (id && this.pendingId && id !== this.pendingId) return;
    this.busy = false; this.pendingId = null; this.pendingAction = null;
    this.outcomes.set(outcome.action, outcome);
    if (outcome.action === "doctor") this.doctor = outcome;
    if (outcome.action === "read_view") {
      const result = obj(outcome.response)?.result;
      const kind = textValue(obj(result)?.["view_type"]);
      if (kind) this.views.set(kind, outcome);
    }
    if (this.wizard.queue.length && outcome.action === this.wizard.queue[this.wizard.index]?.action) {
      this.receiveCommand(outcome);
      return;
    }
    this.render();
  };

  connectedCallback(): void {
    const preset = (window as unknown as { __AITEST_DETAIL?: unknown }).__AITEST_DETAIL;
    if (preset === "workbench" || preset === "review") {
      this.page = preset === "workbench" ? "run" : "report";
      this.classList.add("standalone");
    }
    window.addEventListener("message", this.receive);
    this.render();
    this.dispatch("doctor");
  }
  disconnectedCallback(): void { window.removeEventListener("message", this.receive); }

  private post(message: Record<string, unknown>): void {
    this.host?.postMessage(message);
  }
  private requestId(): string { return `panel-${++this.requestSeq}`; }
  private dispatch(action: string, parameters: Record<string, unknown> = {}, kind = "dispatch"): void {
    const id = this.requestId();
    this.busy = true; this.pendingId = id; this.pendingAction = action;
    this.post({
      kind, action, parameters, clientRequestId: id,
      ...(this.project ? { projectId: this.project.project_id, contextId: this.project.context_id } : {}),
    });
    this.render();
  }
  private controlled(action: string, parameters: Record<string, unknown>, command?: WizardCommand): void {
    const id = this.requestId();
    this.busy = true; this.pendingId = id; this.pendingAction = action;
    this.post({
      kind: "controlledDispatch", action, parameters, clientRequestId: id,
      projectId: this.project?.project_id,
      contextId: this.project?.context_id,
      intentId: command?.intent_id ?? `intent-${id}`,
      expectedRevision: command?.expected_revision ?? 0,
      bindingRevision: command?.binding_revision ?? 1,
      ...(command?.target ? { target: command.target } : {}),
    });
    this.render();
  }
  private readView(kind: string): void {
    if (!this.project?.run_id) return;
    this.dispatch("read_view", { view: kind, run_id: this.project.run_id });
  }
  private query(parameters: Record<string, unknown>): void {
    this.dispatch("query", parameters);
  }
  private sendWizard(kind: string, extra: Record<string, unknown> = {}): void {
    const id = this.requestId();
    this.wizardRequest = id;
    this.wizardAction = kind;
    this.wizard.busy = true;
    this.post({ kind, clientRequestId: id, ...extra });
    this.render();
  }

  private receiveWizard(data: Outcome): void {
    if (this.wizardRequest && data.clientRequestId !== this.wizardRequest) return;
    const action = data.action || this.wizardAction;
    this.wizardRequest = null; this.wizardAction = ""; this.wizard.busy = false;
    if (data.message || (data.status && data.status !== "ok")) { this.wizard.note = data.message || "向导请求未完成"; this.render(); return; }
    const result = resultOf(data) ?? (data as unknown as Record<string, unknown>);
    if (action === "wizardChoose") {
      const session = obj(result?.["session"]);
      if (session) { this.wizard.session = session; this.wizard.reference = textValue(session["reference"]); this.wizard.stage = "model"; }
    } else if (action === "wizardModelCredential") {
      const reference = textValue(result?.["modelCredential"]);
      if (reference) { this.wizard.reference = reference; this.wizard.note = "密钥已保存到系统安全存储。"; }
    } else if (action === "wizardModelLoad") {
      const settings = obj(result?.["modelSettings"]);
      if (settings) {
        this.wizard.endpoint = textValue(settings["endpoint"]) || this.wizard.endpoint;
        this.wizard.model = textValue(settings["model"]) || this.wizard.model;
        this.wizard.timeout = String(settings["timeout"] ?? this.wizard.timeout);
        this.wizard.reference = textValue(settings["reference"]);
      }
      this.modelProfiles = list(result?.["modelProfiles"]).map(obj).filter((item): item is Record<string, unknown> => item !== null).map(item => ({ profile_id: textValue(item["profile_id"]), name: textValue(item["name"]) })).filter(item => item.profile_id);
      this.activeProfile = textValue(result?.["activeProfileId"]);
      this.profileName = this.modelProfiles.find(item => item.profile_id === this.activeProfile)?.name ?? "";
    } else if (action === "wizardModelSave" || action === "wizardModelSelect") {
      this.wizard.note = "配置已保存；重启核心后生效。";
      const settings = obj(result?.["modelSettings"]);
      if (settings) { this.wizard.endpoint = textValue(settings["endpoint"]); this.wizard.model = textValue(settings["model"]); this.wizard.timeout = String(settings["timeout"] ?? 60); this.wizard.reference = textValue(settings["reference"]); }
    } else if (action === "wizardSetup") {
      const setup = obj(result?.["setup"]);
      if (setup) { this.wizard.stage = "environment"; this.startWizardStage("project"); }
    } else if (action === "wizardCommands") {
      const commands = list(result?.["commands"]).map(obj).filter((item): item is Record<string, unknown> => item !== null) as WizardCommand[];
      this.wizard.queue = commands; this.wizard.index = 0; this.runNextCommand();
    } else if (action === "wizardReview") {
      const preview = obj(result?.["preview"]);
      if (preview) { this.wizard.preview = preview; this.wizard.stage = "review"; }
      else this.wizard.note = "审阅未完成，请检查 AI 提案。";
    }
    this.render();
  }

  private startWizardStage(stage: string): void {
    this.wizard.pendingStage = stage;
    this.sendWizard("wizardCommands", { stage, form: this.wizardForm(), receipts: this.wizard.receipts });
  }
  private runNextCommand(): void {
    const command = this.wizard.queue[this.wizard.index];
    if (!command) {
      const stage = this.wizard.pendingStage;
      if (stage === "project") {
        if (this.wizard.useModel) this.startWizardStage("model");
        else { this.wizard.stage = "review"; this.wizard.note = "未启用模型时请使用高级入口生成计划。"; }
      } else if (stage === "model") {
        this.sendWizard("wizardReview", { form: this.wizardForm() });
      } else if (stage === "commit") this.startWizardStage("prepare");
      else if (stage === "prepare") this.startWizardStage("register");
      else if (stage === "register") {
        this.wizard.stage = "done"; this.wizard.note = "测试计划与运行已准备。";
        if (this.project?.run_id) this.readView("run");
      }
      this.render(); return;
    }
    this.wizard.busy = true;
    const parameters = command.parameters;
    if (PLAIN.has(command.action)) {
      const id = this.requestId();
      this.wizardRequest = id;
      this.post({
        kind: "writeDispatch", action: command.action, parameters, clientRequestId: id,
        intentId: command.intent_id, expectedRevision: command.expected_revision,
        bindingRevision: command.binding_revision, target: command.target,
        projectId: this.project?.project_id, contextId: this.project?.context_id,
      });
    } else {
      const id = this.requestId();
      this.wizardRequest = id;
      this.post({
        kind: "controlledDispatch", action: command.action, parameters, clientRequestId: id,
        intentId: command.intent_id, expectedRevision: command.expected_revision,
        bindingRevision: command.binding_revision, target: command.target,
        projectId: this.project?.project_id, contextId: this.project?.context_id,
      });
    }
    this.render();
  }
  private receiveCommand(outcome: Outcome): void {
    const command = this.wizard.queue[this.wizard.index];
    if (!command) return;
    if (outcome.status !== "ok") {
      this.wizard.note = `步骤${command.action}未完成：${outcome.message || outcome.code || "请核对"}`;
      this.wizard.busy = false; this.render(); return;
    }
    this.wizard.receipts[command.key] = obj(outcome.response)?.result ?? {};
    this.wizard.index += 1;
    this.runNextCommand();
  }
  private wizardForm(): Record<string, unknown> {
    const session = this.wizard.session ?? {};
    const preview = this.wizard.preview;
    return {
      project_id: session["project_id"], workspace_id: this.project?.context_id,
      source: session["source"], entry: session["entry"],
      name: session["name"] || "本地项目", goal: session["name"] || "验证本地项目",
      data_policy: this.wizard.dataPolicy || "仅在本地独立环境运行",
      dependency_declaration: "独立 Python 3.13 环境，仅标准库",
      endpoint: this.wizard.endpoint, model: this.wizard.model,
      model_timeout: Number(this.wizard.timeout) || 60,
      proposal: this.wizard.proposal, checks: [],
      run_tier: "full", initial_driver: "planned",
      understanding: preview?.["understanding_ref"] ?? null,
      coverage_contract: preview?.["coverage_contract"] ?? null,
      understanding_ref: preview?.["understanding_ref"] ?? null,
      catalog_confirmed: true,
      source_snippets_enabled: false,
      allowed_material_kinds: ["project_context", "template_content"],
      source_scope: ".",
    };
  }

  private render(): void {
    this.shadow.replaceChildren();
    const style = el("style"); style.textContent = styles;
    const app = el("div", "", "app");
    app.append(this.tabs(), this.main());
    this.shadow.append(style, app);
  }
  private tabs(): HTMLElement {
    const tabs = el("nav", "", "tabs");
    for (const [id, label] of [["home", "首页"], ["run", "运行"], ["plan", "计划"], ["report", "报告"], ["settings", "设置"], ["diagnostics", "诊断"]] as [Page, string][]) {
      const node = button(label, () => {
        this.page = id;
        if (id === "settings" && !this.wizardRequest) this.sendWizard("wizardModelLoad");
        this.render();
      });
      node.className = this.page === id ? "tab active" : "tab";
      tabs.append(node);
    }
    return tabs;
  }
  private main(): HTMLElement {
    const main = el("main", "", "main");
    main.append(this.projectBar());
    if (this.page === "home") main.append(this.home());
    if (this.page === "run") main.append(this.runPage());
    if (this.page === "plan") main.append(this.planPage());
    if (this.page === "report") main.append(this.reportPage());
    if (this.page === "settings") main.append(this.settingsPage());
    if (this.page === "diagnostics") main.append(this.diagnosticsPage());
    main.append(this.status());
    return main;
  }
  private projectBar(): HTMLElement {
    const bar = el("div", "", "project");
    const dot = el("span", "", this.project ? "dot ready" : "dot");
    bar.append(dot, el("span", this.project ? (this.project.scope_name || this.project.project_id) : "未选择项目", "name"));
    const actions = el("div", "", "actions");
    if (!this.project) actions.append(button("新建测试", () => { this.page = "home"; this.wizard.stage = "choose"; this.render(); }, false, true));
    else actions.append(button("读取运行", () => this.readView("run")), button("新测试", () => { this.page = "home"; this.wizard.stage = "choose"; this.render(); }));
    bar.append(actions);
    return bar;
  }
  private status(): HTMLElement {
    const line = el("div", this.busy ? "正在与核心通信…" : this.doctor?.status === "ok" ? "核心已连接" : "核心未连接", "status");
    return line;
  }

  private home(): HTMLElement {
    const page = el("div");
    if (!this.project) {
      page.append(this.onboarding());
      return page;
    }
    const panel = view(this.views.get("panel"));
    const run = view(this.views.get("run"));
    const state = el("section", "", "section");
    state.append(el("h2", this.project.scope_name || this.project.project_id), el("p", "当前测试", "muted"));
    const badges = el("div", "", "actions");
    if (panel) {
      const outcome = textValue(panel["business_outcome"]);
      if (outcome) badges.append(el("span", friendly(outcome), `badge ${outcome === "passed" ? "ok" : outcome === "failed" ? "bad" : "warn"}`));
      const control = textValue(panel["control_state"] || run?.["control_state"]);
      if (control) badges.append(el("span", friendly(control), `badge ${control === "running" ? "ok" : ""}`));
    }
    state.append(badges);
    const counts = obj(run?.["selected_summary"]);
    if (counts) {
      const split = el("div", "", "split");
      for (const [label, key] of [["执行", "executed_count"], ["验证", "verified_count"], ["未验证", "unverified_count"]] as [string, string][]) {
        const box = el("div", "", "metric"); box.append(el("b", String(counts[key] ?? 0)), el("span", label)); split.append(box);
      }
      state.append(split);
    }
    if (!run) state.append(button("读取本次运行", () => this.readView("run"), this.busy, true));
    page.append(state);

    const next = el("section", "", "section");
    next.append(el("h3", "下一步"));
    next.append(this.nextActions(panel, run));
    page.append(next);

    const steps = list(run?.["steps"]).map(obj).filter((item): item is Record<string, unknown> => item !== null);
    if (steps.length) {
      const pending = steps.filter(step => ["pending", "blocked", "pending_verification"].includes(textValue(step["state"])));
      if (pending.length) {
        const box = el("section", "", "section"); box.append(el("h3", `待处理 ${pending.length}`));
        const rows = el("div", "", "rows");
        for (const step of pending.slice(0, 8)) {
          const row = el("div", "", "row");
          row.append(el("span", `${textValue(step["case_id"])} · ${friendly(step["state"])}`, "grow"));
          row.append(button("打开", () => { this.page = "run"; this.selectedStep = textValue(step["step_id"]); this.render(); }));
          rows.append(row);
        }
        box.append(rows); page.append(box);
      }
    }
    const reports = el("section", "", "section");
    reports.append(el("h3", "最近报告"));
    reports.append(button("读取最近报告", () => this.loadReports(), this.busy));
    if (this.lastReport) reports.append(el("p", `${friendly(this.lastReport["business_outcome"])} · ${textValue(this.lastReport["record_id"])}`, "muted"));
    page.append(reports);
    return page;
  }
  private nextActions(panel: Record<string, unknown> | null, run: Record<string, unknown> | null): HTMLElement {
    const box = el("div", "", "actions");
    if (!run) { box.append(button("读取本次运行", () => this.readView("run"), this.busy, true)); return box; }
    const control = textValue(run["control_state"]);
    if (control === "not_started" || control === "ready") box.append(button("开始执行", () => this.runControl("start_run", run), this.busy, true));
    if (control === "running") box.append(button("暂停", () => this.runControl("pause_run", run), this.busy), button("取消", () => this.runControl("cancel_run", run), this.busy));
    if (control === "paused" || control === "pause_requested") box.append(button("继续", () => this.runControl("resume_run", run), this.busy, true));
    box.append(button("打开运行", () => { this.page = "run"; this.render(); }));
    if (panel) box.append(button("保存报告", () => this.createReport(), this.busy));
    return box;
  }
  private runControl(action: string, run: Record<string, unknown>): void {
    const runId = textValue(this.project?.run_id || run["run_id"]);
    const snapshot = textValue(run["snapshot_commit_id"]);
    if (!runId || !snapshot) return;
    this.dispatch(action, { run_id: runId, base_snapshot_commit_id: snapshot }, "writeDispatch");
  }

  private runPage(): HTMLElement {
    const page = el("div");
    const run = view(this.views.get("run"));
    const panel = view(this.views.get("panel"));
    const head = el("section", "", "section");
    head.append(el("h2", "运行"), el("p", "步骤、授权、执行与核验", "muted"));
    if (!run) head.append(button("读取运行", () => this.readView("run"), this.busy, true));
    else {
      const control = textValue(run["control_state"]);
      head.append(button("刷新", () => this.readView("run"), this.busy));
      if (control === "running") head.append(button("暂停", () => this.runControl("pause_run", run)), button("取消", () => this.runControl("cancel_run", run)));
      if (control === "paused") head.append(button("继续", () => this.runControl("resume_run", run), false, true));
    }
    page.append(head);
    if (!run) return page;
    const steps = list(run["steps"]).map(obj).filter((item): item is Record<string, unknown> => item !== null);
    const listBox = el("div", "", "steps");
    for (const step of steps) {
      const id = textValue(step["step_id"]);
      const row = el("div", "", this.selectedStep === id ? "step active" : "step");
      row.append(
        el("div", "", "step-title"),
        el("span", `${textValue(step["case_id"])} · ${friendly(step["state"])}`, "step-meta"),
      );
      row.onclick = () => { this.selectedStep = id; this.render(); };
      listBox.append(row);
    }
    page.append(el("h3", "步骤"), listBox);
    if (!this.selectedStep && steps[0]) this.selectedStep = textValue(steps[0]["step_id"]);
    const step = steps.find(item => textValue(item["step_id"]) === this.selectedStep);
    if (step) page.append(this.stepDetail(step, run));
    return page;
  }
  private stepDetail(step: Record<string, unknown>, run: Record<string, unknown>): HTMLElement {
    const box = el("section", "", "section");
    const id = textValue(step["step_id"]);
    box.append(el("h3", `步骤 ${id}`));
    const facts = el("dl", "", "facts");
    for (const [label, value] of [
      ["状态", friendly(step["state"])], ["尝试", friendly(step["attempt_state"])],
      ["用例", textValue(step["case_id"])], ["依据", textValue(step["basis_state"]) || "以保存计划为准"],
    ]) facts.append(el("dt", label), el("dd", value));
    box.append(facts);
    const gaps = list(step["gap_ids"]).map(String);
    if (gaps.length) { const gap = el("div", "", "gap"); gap.append(el("p", "当前缺口"), ...gaps.map(code => el("p", code))); box.append(gap); }
    const actions = el("div", "", "actions");
    const actionState = this.stepActions.get(id);
    if (!actionState) {
      actions.append(button("准备执行", () => this.dispatch("prepare_execution", { run_id: run["run_id"], step_id: id }, "writeDispatch"), this.busy, true));
    } else {
      const actionId = textValue(actionState["execution_action_id"]);
      const grantId = textValue(run["plan_grant_id"]);
      actions.append(button("授权", () => this.controlled("authorize_step", grantId ? { execution_action_id: actionId, record_revision: 1, grant_id: grantId } : { execution_action_id: actionId, record_revision: 1 }), this.busy));
      actions.append(button("执行", () => this.dispatch("execute_step", { execution_action_id: actionId, record_revision: 1 }, "writeDispatch"), this.busy, true));
    }
    actions.append(button("核验", () => this.dispatch("verify_pending", {
      run_id: run["run_id"], step_id: id, attempt_id: step["current_attempt_id"],
      base_snapshot_commit_id: run["snapshot_commit_id"],
    }, "writeDispatch"), this.busy || !step["current_attempt_id"]));
    box.append(actions);
    return box;
  }

  private planPage(): HTMLElement {
    const page = el("div");
    const head = el("section", "", "section");
    head.append(el("h2", "计划"), el("p", "读取已保存计划修订与范围。", "muted"), button("读取计划", () => this.query({ aggregate_kind: "plan", limit: 20 }), this.busy));
    page.append(head);
    const rows = list(resultOf(this.outcomes.get("query"))?.["items"]).map(obj).filter((item): item is Record<string, unknown> => item !== null);
    if (!rows.length) { page.append(el("p", "尚未读取计划。", "muted")); return page; }
    const table = el("table", "", "table");
    for (const row of rows) {
      const tr = el("tr");
      tr.append(el("td", textValue(row["record_id"])), el("td", `修订 ${row["revision"] ?? "?"}`));
      table.append(tr);
    }
    page.append(table);
    return page;
  }
  private loadReports(): void {
    if (!this.project?.run_id) { this.lastReport = null; this.render(); return; }
    this.query({ run_id: this.project.run_id, limit: 5 });
  }
  private reportPage(): HTMLElement {
    const page = el("div");
    const head = el("section", "", "section");
    head.append(el("h2", "报告"), el("p", "保存并查看本次运行的结果。", "muted"));
    head.append(button("保存报告", () => this.createReport(), this.busy, true), button("读取报告", () => this.loadReports(), this.busy));
    page.append(head);
    const panel = view(this.views.get("panel"));
    if (panel?.["business_outcome"]) {
      const box = el("section", "", "section");
      box.append(el("h3", "业务结论"), el("p", friendly(panel["business_outcome"]), "badge"));
      page.append(box);
    }
    const items = list(resultOf(this.outcomes.get("query"))?.["items"]).map(obj).filter((item): item is Record<string, unknown> => item !== null);
    if (items.length) {
      const table = el("table", "", "table");
      for (const item of items) {
        const tr = el("tr"); tr.append(el("td", textValue(item["record_id"])), el("td", `修订 ${item["revision"] ?? "?"}`)); table.append(tr);
      }
      page.append(table);
    } else page.append(el("p", "尚未读取报告。", "muted"));
    return page;
  }
  private createReport(): void {
    const runId = this.project?.run_id;
    if (!runId) return;
    this.controlled("create_report", { run_id: runId, issue_ids: [] }, {
      key: "report", action: "create_report", intent_id: `report-${runId}`, expected_revision: 0,
      binding_revision: 1, parameters: {},
    });
  }

  private settingsPage(): HTMLElement {
    const page = el("div");
    const head = el("section", "", "section");
    head.append(el("h2", "设置"), el("p", "模型配置档与材料范围由项目确认；密钥只在宿主安全存储。", "muted"));
    head.append(button("读取配置", () => this.sendWizard("wizardModelLoad"), this.busy));
    page.append(head);
    const box = el("section", "", "section");
    box.append(el("h3", "模型配置"));
    if (this.modelProfiles.length) {
      const select = el("select");
      const fresh = el("option", "新建配置"); fresh.value = "__new__"; select.append(fresh);
      for (const profile of this.modelProfiles) { const option = el("option", profile.name); option.value = profile.profile_id; select.append(option); }
      select.value = this.activeProfile || "__new__";
      select.onchange = () => {
        if (select.value === "__new__") { this.activeProfile = ""; this.profileName = ""; this.render(); return; }
        this.activeProfile = select.value;
        this.sendWizard("wizardModelSelect", { profile_id: select.value });
      };
      box.append(select);
    }
    const field = (label: string, value: string, set: (value: string) => void) => {
      const row = el("label"); row.append(el("span", label));
      const input = el("input"); input.value = value; input.oninput = () => set(input.value); row.append(input); return row;
    };
    box.append(field("配置名称", this.profileName, value => this.profileName = value));
    box.append(field("API 地址", this.wizard.endpoint, value => this.wizard.endpoint = value));
    box.append(field("模型", this.wizard.model, value => this.wizard.model = value));
    box.append(field("超时（秒）", this.wizard.timeout, value => this.wizard.timeout = value));
    const actions = el("div", "", "actions");
    actions.append(button("配置密钥", () => this.sendWizard("wizardModelCredential"), this.busy));
    actions.append(button("保存配置", () => this.sendWizard("wizardModelSave", {
      endpoint: this.wizard.endpoint, model: this.wizard.model, reference: this.wizard.reference,
      timeout: Number(this.wizard.timeout) || 60, profile_id: this.activeProfile,
      profile_name: this.profileName || this.wizard.model,
    }), this.busy, true));
    box.append(actions);
    box.append(el("p", this.wizard.note || "源码片段默认关闭；材料范围在新建测试时确认。", "muted"));
    page.append(box);
    return page;
  }
  private diagnosticsPage(): HTMLElement {
    const page = el("div");
    const head = el("section", "", "section");
    head.append(el("h2", "诊断"), el("p", this.doctor?.status === "ok" ? "核心已连接" : "核心未连接", "muted"));
    head.append(button("检查核心", () => this.dispatch("doctor"), this.busy), button("读取运行", () => this.readView("run"), this.busy));
    page.append(head);
    if (this.lastOutcome) {
      const detail = el("details"); detail.append(el("summary", "最近回执"), el("pre", JSON.stringify(this.lastOutcome, null, 2)));
      page.append(detail);
    }
    return page;
  }

  private onboarding(): HTMLElement {
    const page = el("div");
    const head = el("section", "", "section");
    head.append(el("h2", "新建测试"), el("p", "选择一个本地 Python 项目，AI 会读取项目并生成计划，你只确认一次。", "muted"));
    page.append(head);
    if (this.wizard.stage === "choose") {
      const box = el("section", "", "section");
      box.append(button("选择项目目录", () => this.sendWizard("wizardChoose"), this.wizard.busy, true));
      box.append(el("p", this.wizard.note || "需要 Python 3.13 项目目录和一个测试入口。", "muted"));
      page.append(box); return page;
    }
    const info = el("section", "", "section");
    const session = this.wizard.session ?? {};
    info.append(el("h3", textValue(session["name"]) || "已选择项目"), el("p", textValue(session["entry"]), "muted"));
    page.append(info);
    if (this.wizard.stage === "model") {
      const box = el("section", "", "section");
      box.append(el("h3", "模型"));
      const field = (label: string, value: string, set: (value: string) => void) => {
        const row = el("label"); row.append(el("span", label));
        const input = el("input"); input.value = value; input.oninput = () => set(input.value); row.append(input); return row;
      };
      box.append(field("API 地址", this.wizard.endpoint, value => this.wizard.endpoint = value));
      box.append(field("模型", this.wizard.model, value => this.wizard.model = value));
      const credential = el("p", this.wizard.reference ? "密钥已配置。" : "尚未配置密钥。", "muted");
      box.append(credential, button("配置密钥", () => this.sendWizard("wizardModelCredential"), this.wizard.busy));
      box.append(button("继续", () => this.sendWizard("wizardSetup", {
        dataConfirmed: true, useModel: true, endpoint: this.wizard.endpoint,
      }), this.wizard.busy || !this.wizard.reference, true));
      box.append(el("p", this.wizard.note, "muted"));
      page.append(box); return page;
    }
    if (this.wizard.stage === "review") {
      const box = el("section", "", "section");
      const preview = this.wizard.preview ?? {};
      const coverage = obj(preview["coverage"]);
      box.append(el("h3", "核对计划"));
      box.append(el("p", `模板项 ${coverage?.["template_items"] ?? "?"} · 已覆盖 ${coverage?.["covered_items"] ?? "?"} · 必测 ${coverage?.["required_items"] ?? "?"}`, "muted"));
      box.append(button("确认并发布", () => { this.startWizardStage("commit"); }, this.wizard.busy, true));
      box.append(el("p", this.wizard.note, "muted"));
      page.append(box); return page;
    }
    if (this.wizard.stage === "done") {
      const box = el("section", "", "section");
      box.append(el("h3", "已准备运行"), el("p", "打开运行页开始执行，或在计划页核对计划。", "muted"));
      box.append(button("打开运行", () => { this.page = "run"; this.readView("run"); this.render(); }, false, true));
      page.append(box); return page;
    }
    page.append(el("p", this.wizard.note || "正在准备…", "muted"));
    return page;
  }
}

if (!customElements.get("ai-test-panel")) customElements.define("ai-test-panel", AITestPanel);
