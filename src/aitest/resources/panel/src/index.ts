/** AI Test panel: flow-first shell around the local core's read-only DTOs. */

import { HostClient } from "./host";
import { icon, type IconName } from "./icons";
import { asRecord, asRecords, shortId } from "./labels";
import { shellStyles } from "./styles";
import type { AppContext } from "./context";
import type {
  AppState,
  Json,
  ModelProfile,
  Outcome,
  ProjectContext,
  Route,
  Row,
  Tone,
  WizardResult,
  WriteOptions,
} from "./types";
import { CONTROLLED_ACTIONS, WizardController } from "./wizard";
import { div, iconButton } from "./ui";
import { renderDiagnostics } from "./views/diagnostics";
import { renderHome } from "./views/home";
import { renderPlan } from "./views/plan";
import { renderRules } from "./views/rules";
import { renderReports } from "./views/reports";
import { renderSettings } from "./views/settings";
import { renderWizard } from "./views/wizard";
import { renderWorkbench } from "./views/workbench";

const NAV: ReadonlyArray<readonly [Route, string, IconName]> = [
  ["home", "本次测试", "layers"],
  ["reports", "问题与报告", "report"],
  ["settings", "设置", "settings"],
];

type VSCodeApi = { postMessage: (message: unknown) => void };

class AiTestPanel extends HTMLElement implements AppContext {
  private readonly shadow = this.attachShadow({ mode: "open" });
  private readonly vscodeApi: VSCodeApi | null;
  private readonly client: HostClient | null;
  readonly hostAvailable: boolean;
  readonly wizard: WizardController;
  state: AppState;
  private readonly intents = new Map<string, string>();
  private noticeTimer: number | null = null;
  private booted = false;

  constructor() {
    super();
    const acquire = (globalThis as { acquireVsCodeApi?: () => VSCodeApi }).acquireVsCodeApi;
    this.vscodeApi = typeof acquire === "function" ? acquire() : null;
    this.hostAvailable = this.vscodeApi !== null;
    this.state = initialState(this.hostAvailable);
    this.client = this.vscodeApi === null
      ? null
      : new HostClient(
          message => this.vscodeApi?.postMessage(message),
          context => this.applyProjectContext(context),
          message => this.setNotice(message, "warning"),
          message => this.setNotice(message, "info"),
        );
    this.wizard = new WizardController({
      hostAvailable: this.hostAvailable,
      wizardRequest: (kind, extra) => this.wizardRequest(kind, extra),
      write: (action, parameters, options) => this.write(action, parameters, options),
      restartCore: () => this.restartCore(),
      render: () => this.render(),
      navigate: route => this.navigate(route),
      setNotice: (message, tone) => this.setNotice(message, tone),
      projectId: () => this.state.project?.project_id ?? null,
      workspaceId: () => this.state.workspaceId,
      contextId: () => this.client?.context ?? "",
    });
  }

  connectedCallback(): void {
    this.render();
    this.addEventListener("keydown", this.onKeyDown);
    if (this.client && !this.booted) {
      this.booted = true;
      void this.bootstrap();
    }
  }

  disconnectedCallback(): void {
    this.removeEventListener("keydown", this.onKeyDown);
    this.client?.dispose();
    if (this.noticeTimer !== null) window.clearTimeout(this.noticeTimer);
  }

  private readonly onKeyDown = (event: KeyboardEvent): void => {
    if (event.key === "Escape" && this.state.dialog) this.closeDialog();
  };

  // ---------------------------------------------------------------- connect

  private async bootstrap(): Promise<void> {
    this.state.connection = "connecting";
    this.state.busy = "connect";
    this.render();
    await this.ensureCore();
  }

  async ensureCore(): Promise<void> {
    if (!this.client) return;
    this.state.connection = "connecting";
    this.state.connectionMessage = "";
    this.state.busy = "connect";
    this.render();
    const outcome = await this.client.ensureCore();
    this.state.busy = null;
    if (outcome.status === "ok") {
      const workspace = outcome.response?.["workspace_id"];
      if (typeof workspace === "string") this.state.workspaceId = workspace;
      this.state.connection = "ready";
      this.state.connectionMessage = "";
      this.render();
      await this.doctor();
      return;
    }
    this.state.connection = "unavailable";
    this.state.connectionMessage = outcome.message ?? "核心未连接。";
    this.state.doctor = null;
    this.render();
  }

  private async doctor(): Promise<void> {
    if (!this.client) return;
    const outcome = await this.client.read("doctor", {}, null);
    if (outcome.status === "ok") {
      this.state.doctor = asRecord(outcome.response?.["result"]) ?? null;
      const workspace = outcome.response?.["workspace_id"];
      if (typeof workspace === "string") this.state.workspaceId = workspace;
      this.state.connection = "ready";
      this.state.connectionMessage = "";
      this.render();
      if (this.state.project?.run_id) void this.refreshCurrent();
      return;
    }
    this.state.connection = "unavailable";
    this.state.connectionMessage = outcome.message ?? "核心自检未通过。";
    this.state.doctor = null;
    this.render();
  }

  async restartCore(): Promise<Outcome> {
    if (!this.client) {
      return { action: "restart-core", status: "refused", code: "NO_HOST", message: "未连接编辑器宿主。" };
    }
    this.state.busy = "restart";
    this.render();
    const outcome = await this.client.restartCore();
    this.state.busy = null;
    if (outcome.status === "ok") {
      const workspace = outcome.response?.["workspace_id"];
      if (typeof workspace === "string") this.state.workspaceId = workspace;
      this.state.connection = "ready";
      this.state.connectionMessage = "";
      await this.doctor();
      if (this.state.connection === "ready") {
        this.setNotice("核心已受控重启并重新连接。", "success");
      }
    } else {
      if (outcome.code === "CORE_RESTART_BLOCKED") {
        // 活动执行未排空时核心会拒绝停机；此时旧核心仍然可用，不把它标成断连。
        this.state.connection = this.state.connection === "unavailable" ? "unavailable" : "ready";
      } else {
        // 停机已发生或结果未知：不把旧连接事实继续当成可用核心。
        this.state.connection = "unavailable";
        this.state.connectionMessage = outcome.message ?? "核心重启未完成。";
        this.state.doctor = null;
      }
      this.setNotice(outcome.message ?? "核心重启未完成。", "danger");
      this.render();
    }
    return outcome;
  }

  private applyProjectContext(context: ProjectContext): void {
    const previous = this.state.project;
    const previousProject = previous?.project_id ?? "";
    const previousRun = previous?.run_id ?? "";
    const nextRun = context.run_id ?? "";
    this.state.project = context;
    if (context.project_id !== previousProject) {
      this.resetProjectViews();
      this.intents.clear();
    } else if (nextRun !== previousRun) {
      this.state.panel = null;
      this.state.run = null;
      this.state.workbench = initialWorkbench();
    }
    if (context.project_id !== previousProject || nextRun !== previousRun) {
      this.state.dialog = null;
    }
    if (this.state.route === "plan" && !this.state.plansLoaded) void this.loadPlans();
    else if (this.state.route === "rules" && !this.state.rulesLoaded) void this.loadRules();
    else if (this.state.route === "reports") {
      if (this.state.reportsTab === "reports") void this.loadReports();
      else if (this.state.reportsTab === "issues") void this.loadIssues();
      else void this.loadDeliveries();
    } else if (this.state.route === "workbench" && nextRun) void this.refreshCurrent();
    else if (nextRun && !this.state.panel) void this.refreshCurrent();
    else this.render();
  }

  private resetProjectViews(): void {
    this.state.panel = null;
    this.state.run = null;
    this.state.plans = [];
    this.state.plansLoaded = false;
    this.state.planDetail = null;
    this.state.selectedPlan = null;
    this.state.ruleDrafts = [];
    this.state.ruleVersions = [];
    this.state.ruleDetail = null;
    this.state.ruleDetailKind = null;
    this.state.rulesLoaded = false;
    this.state.reports = [];
    this.state.reportsLoaded = false;
    this.state.reportDetail = null;
    this.state.selectedReport = null;
    this.state.reviewIds = {};
    this.state.reviewDetails = {};
    this.state.issues = [];
    this.state.issuesLoaded = false;
    this.state.issueDetail = null;
    this.state.selectedIssue = null;
    this.state.deliveries = [];
    this.state.deliveriesLoaded = false;
    this.state.selectedDelivery = null;
    this.state.deliveryDetail = null;
    this.state.deliverySubmit = null;
    this.state.events = [];
    this.state.eventCursor = null;
    this.state.eventNote = "";
    this.state.workbench = initialWorkbench();
    this.state.lastPublish = null;
    this.state.advanced.result = "";
    this.state.advanced.tone = "neutral";
  }

  async refreshCurrent(): Promise<void> {
    const project = this.state.project;
    const runId = project?.run_id;
    if (!this.client || !project?.project_id || !runId) {
      this.state.panel = null;
      this.state.run = null;
      this.render();
      return;
    }
    const [panel, run] = await Promise.all([
      this.read("panel", { view: "panel", run_id: runId }),
      this.read("run", { view: "run", run_id: runId }),
    ]);
    this.state.panel = panel;
    this.state.run = run;
    this.render();
  }

  // ---------------------------------------------------------------- requests

  async read(view: string, parameters: Record<string, unknown> = {}): Promise<Json | null> {
    if (!this.client) return null;
    const projectId = this.state.project?.project_id ?? null;
    if (!projectId) {
      this.setNotice("尚未取得宿主项目上下文。", "warning");
      return null;
    }
    const outcome = await this.client.read("read_view", { view, ...parameters }, projectId);
    if (outcome.status !== "ok") {
      this.setNotice(outcome.message ?? `读取 ${view} 未完成。`, "danger");
      return null;
    }
    const result = asRecord(outcome.response?.["result"]);
    return asRecord(result?.["view"]) ?? null;
  }

  async readOnly(action: string, parameters: Record<string, unknown> = {}): Promise<Json | null> {
    if (!this.client) return null;
    const outcome = await this.client.read(action, parameters, this.state.project?.project_id ?? null);
    if (outcome.status !== "ok") {
      this.setNotice(outcome.message ?? `${action} 未完成。`, "danger");
      return null;
    }
    return asRecord(outcome.response?.["result"]) ?? {};
  }

  async batch(items: ReadonlyArray<import("./context").BatchAuthorizationItem>): Promise<Outcome> {
    if (!this.client) {
      return {
        action: "authorize_batch",
        status: "refused",
        code: "HOST_UNAVAILABLE",
        message: "未检测到编辑器宿主，批量授权未派发。",
      };
    }
    return this.client.batch(items);
  }

  async query(parameters: Record<string, unknown>): Promise<Row[]> {
    if (!this.client) return [];
    const projectId = this.state.project?.project_id ?? null;
    if (!projectId) {
      this.setNotice("尚未取得宿主项目上下文。", "warning");
      return [];
    }
    const outcome = await this.client.read("query", parameters, projectId);
    if (outcome.status !== "ok") {
      this.setNotice(outcome.message ?? "列表读取未完成。", "danger");
      return [];
    }
    const result = asRecord(outcome.response?.["result"]);
    return asRecords(result?.["items"]).flatMap(item => {
      const recordId = item["record_id"];
      if (typeof recordId !== "string" || !recordId) return [];
      const revision = item["revision"];
      return [{
        record_id: recordId,
        revision: typeof revision === "number" && Number.isInteger(revision) ? revision : 1,
        aggregate_kind: typeof item["aggregate_kind"] === "string" ? item["aggregate_kind"] : "",
      }];
    });
  }

  async write(
    action: string,
    parameters: Record<string, unknown>,
    options: WriteOptions = {},
  ): Promise<Outcome> {
    const projectId = this.state.project?.project_id ?? null;
    if (!this.client) {
      return { action, status: "refused", code: "NO_HOST", message: "未连接编辑器宿主。" };
    }
    const expected = options.expectedRevision ?? 0;
    const controlled = options.controlled ?? CONTROLLED_ACTIONS.has(action);
    const intentId = options.intentId ?? this.intent(action, parameters, options.target ?? undefined, expected);
    return this.client.write(
      action,
      parameters,
      { ...options, controlled, intentId, expectedRevision: expected },
      projectId,
    );
  }

  async wizardRequest(kind: string, extra: Record<string, unknown> = {}): Promise<WizardResult> {
    if (!this.client) {
      return { kind: "wizardResult", error: "未连接编辑器宿主，不能准备测试。" };
    }
    return this.client.requestWizard({ kind, ...extra });
  }

  intent(
    action: string,
    parameters: Record<string, unknown>,
    target?: string,
    expectedRevision = 0,
  ): string {
    const key = JSON.stringify([action, target ?? null, expectedRevision, parameters]);
    const known = this.intents.get(key);
    if (known) return known;
    const created = randomHex(24);
    this.intents.set(key, created);
    return created;
  }

  // ---------------------------------------------------------------- loads

  async loadPlans(): Promise<void> {
    if (!this.client || !this.state.project?.project_id) {
      this.state.plans = [];
      this.state.plansLoaded = true;
      this.render();
      return;
    }
    this.state.busy = "plans";
    this.render();
    const rows = await this.query({ aggregate_kind: "plan", limit: 20 });
    this.state.plans = rows;
    this.state.plansLoaded = true;
    this.state.busy = null;
    this.render();
  }

  async loadRules(): Promise<void> {
    if (!this.client || !this.state.project?.project_id) {
      this.state.ruleDrafts = [];
      this.state.ruleVersions = [];
      this.state.rulesLoaded = true;
      this.render();
      return;
    }
    this.state.busy = "rules";
    this.render();
    const [drafts, versions] = await Promise.all([
      this.query({ aggregate_kind: "rule_draft", limit: 50 }),
      this.query({ aggregate_kind: "rule_version", limit: 50 }),
    ]);
    this.state.ruleDrafts = drafts;
    this.state.ruleVersions = versions;
    this.state.rulesLoaded = true;
    this.state.busy = null;
    this.render();
  }

  async loadReports(): Promise<void> {
    if (!this.client || !this.state.project?.project_id) {
      this.state.reports = [];
      this.state.reportsLoaded = true;
      this.render();
      return;
    }
    this.state.busy = "reports";
    this.render();
    const runId = this.state.project.run_id;
    const rows = await this.query(runId
      ? { run_id: runId, limit: 10 }
      : { aggregate_kind: "report", limit: 20 });
    rows.sort((left, right) => right.revision - left.revision);
    this.state.reports = rows;
    this.state.reportsLoaded = true;
    this.state.busy = null;
    this.render();
  }

  async loadIssues(): Promise<void> {
    if (!this.client || !this.state.project?.project_id) {
      this.state.issues = [];
      this.state.issuesLoaded = true;
      this.render();
      return;
    }
    this.state.busy = "issues";
    this.render();
    const rows = await this.query({ view: "OPEN", limit: 20 });
    rows.sort((left, right) => right.revision - left.revision);
    this.state.issues = rows;
    this.state.issuesLoaded = true;
    this.state.busy = null;
    this.render();
  }

  async loadDeliveries(): Promise<void> {
    if (!this.client || !this.state.project?.project_id) {
      this.state.deliveries = [];
      this.state.deliveriesLoaded = true;
      this.render();
      return;
    }
    this.state.busy = "deliveries";
    this.render();
    const rows = await this.query({ aggregate_kind: "delivery", limit: 20 });
    rows.sort((left, right) => right.revision - left.revision);
    this.state.deliveries = rows;
    this.state.deliveriesLoaded = true;
    this.state.busy = null;
    this.render();
  }

  async loadEvents(): Promise<void> {
    this.state.busy = "events";
    this.render();
    const parameters: Record<string, unknown> = { limit: 20 };
    if (this.state.eventCursor) parameters["cursor"] = this.state.eventCursor;
    const result = await this.readOnly("events", parameters);
    this.state.busy = null;
    if (!result) {
      this.state.eventNote = "读取事件未完成。";
      this.render();
      return;
    }
    const resultStatus = result["status"];
    if (typeof resultStatus === "string" && resultStatus !== "ok") {
      if (resultStatus === "invalid_cursor") {
        this.state.eventCursor = null;
        this.state.eventNote = "事件游标无效，已清除游标；再次读取会从头开始。";
      } else if (resultStatus === "maintenance_required") {
        this.state.eventNote = "事件日志需要维护，请查看诊断或空间状态。";
      } else {
        this.state.eventNote = `事件读取未通过：${resultStatus}`;
      }
      this.render();
      return;
    }
    const incoming = asRecords(result["events"]);
    const seen = new Set(this.state.events.map(eventKey));
    for (const event of incoming) {
      const key = eventKey(event);
      if (seen.has(key)) continue;
      seen.add(key);
      this.state.events.push(event);
    }
    const cursor = result["next_cursor"];
    // 游标只在回执明确给出新值时前移；缺失时保持原游标。
    if (typeof cursor === "string" && cursor) this.state.eventCursor = cursor;
    this.state.eventNote = incoming.length === 0
      ? (this.state.events.length ? "没有新事件。" : "回执里没有事件行。")
      : "";
    this.render();
  }

  // ---------------------------------------------------------------- model settings

  async modelLoad(): Promise<void> {
    if (!this.client) return;
    this.state.model.busy = true;
    this.state.model.note = "";
    this.render();
    const result = await this.wizardRequest("wizardModelLoad");
    this.state.model.busy = false;
    if (result.error) {
      this.state.model.note = String(result.error);
      this.render();
      return;
    }
    const settings = asRecord(result["modelSettings"]);
    if (settings) {
      if (typeof settings["endpoint"] === "string") this.state.model.endpoint = settings["endpoint"];
      if (typeof settings["model"] === "string") this.state.model.model = settings["model"];
      if (settings["timeout"] !== undefined) this.state.model.timeout = String(settings["timeout"]);
      if (typeof settings["reference"] === "string") this.state.model.reference = settings["reference"];
    }
    this.state.model.profiles = parseProfiles(result["modelProfiles"]);
    const active = result["activeProfileId"];
    if (typeof active === "string") this.state.model.activeProfileId = active;
    this.state.model.loaded = true;
    this.render();
  }

  async modelSave(): Promise<void> {
    const model = this.state.model;
    const endpoint = model.endpoint.trim();
    const name = model.model.trim();
    const timeout = Number(model.timeout);
    try {
      const url = new URL(endpoint);
      if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash) throw new Error();
    } catch {
      model.note = "填写完整的 HTTPS 地址，不要附带账号、密码或查询参数。";
      this.render();
      return;
    }
    if (!name || name.length > 128 || /[\u0000-\u001f]/.test(name)) {
      model.note = "填写供应方提供的模型名称。";
      this.render();
      return;
    }
    if (!Number.isInteger(timeout) || timeout < 10 || timeout > 120) {
      model.note = "请求超时请填写 10–120 之间的整数秒。";
      this.render();
      return;
    }
    if (!model.reference) {
      model.note = "请先配置安全凭据。";
      this.render();
      return;
    }
    model.busy = true;
    this.render();
    const result = await this.wizardRequest("wizardModelSave", {
      endpoint,
      model: name,
      timeout,
      reference: model.reference,
      ...(model.activeProfileId ? { profile_id: model.activeProfileId } : {}),
    });
    model.busy = false;
    if (result.error) {
      model.note = String(result.error);
      this.render();
      return;
    }
    const settings = asRecord(result["modelSettings"]);
    if (settings) {
      if (typeof settings["endpoint"] === "string") model.endpoint = settings["endpoint"];
      if (typeof settings["model"] === "string") model.model = settings["model"];
      if (settings["timeout"] !== undefined) model.timeout = String(settings["timeout"]);
      if (typeof settings["reference"] === "string") model.reference = settings["reference"];
    }
    model.profiles = parseProfiles(result["modelProfiles"]);
    const active = result["activeProfileId"];
    if (typeof active === "string") model.activeProfileId = active;
    model.note = result["requiresCoreRestart"] === true
      ? "默认模型配置已保存；受控重启核心后才会用于新建测试。已有运行仍使用原配置。"
      : "默认模型配置已保存；新建测试会使用该配置。已有运行仍使用原配置。";
    this.render();
  }

  async modelSelect(profileId: string): Promise<void> {
    const model = this.state.model;
    model.busy = true;
    this.render();
    const result = await this.wizardRequest("wizardModelSelect", { profile_id: profileId });
    model.busy = false;
    if (result.error) {
      model.note = String(result.error);
      this.render();
      return;
    }
    const settings = asRecord(result["modelSettings"]);
    if (settings) {
      if (typeof settings["endpoint"] === "string") model.endpoint = settings["endpoint"];
      if (typeof settings["model"] === "string") model.model = settings["model"];
      if (settings["timeout"] !== undefined) model.timeout = String(settings["timeout"]);
      if (typeof settings["reference"] === "string") model.reference = settings["reference"];
    }
    model.profiles = parseProfiles(result["modelProfiles"]);
    if (typeof result["activeProfileId"] === "string") model.activeProfileId = result["activeProfileId"];
    model.loaded = true;
    model.note = "已切换模型配置。受控重启核心后才会用于新建测试。";
    this.render();
  }

  async modelCredential(): Promise<void> {
    const model = this.state.model;
    model.busy = true;
    this.render();
    const result = await this.wizardRequest("wizardModelCredential");
    model.busy = false;
    if (result.error) {
      model.note = String(result.error);
      this.render();
      return;
    }
    if (result.cancelled) {
      model.note = "已取消凭据配置。";
      this.render();
      return;
    }
    const reference = result["modelCredential"] ?? result["reference"];
    if (typeof reference === "string") model.reference = reference;
    model.note = "密钥已保存到系统安全存储，正文不会进入面板。";
    this.render();
  }

  // ---------------------------------------------------------------- navigation & notices

  navigate(route: Route): void {
    this.state.route = route;
    this.state.dialog = null;
    if (route === "plan" && !this.state.plansLoaded) void this.loadPlans();
    else if (route === "rules" && !this.state.rulesLoaded) void this.loadRules();
    else if (route === "reports") {
      if (this.state.reportsTab === "reports") void this.loadReports();
      else if (this.state.reportsTab === "issues") void this.loadIssues();
      else void this.loadDeliveries();
    } else if (route === "settings" && this.hostAvailable && !this.state.model.loaded && !this.state.model.busy) {
      void this.modelLoad();
    } else if (route === "workbench" && this.state.project?.run_id && !this.state.run) {
      void this.refreshCurrent();
    } else if (route === "home" && this.state.project?.run_id && !this.state.run) {
      void this.refreshCurrent();
    }
    this.render();
    window.scrollTo(0, 0);
  }

  setNotice(message: string, tone: Tone = "info"): void {
    if (this.noticeTimer !== null) window.clearTimeout(this.noticeTimer);
    this.state.notice = { message, tone };
    this.render();
    this.noticeTimer = window.setTimeout(() => {
      this.state.notice = null;
      this.noticeTimer = null;
      this.render();
    }, tone === "danger" ? 10_000 : 6_000);
  }

  clearNotice(): void {
    if (this.noticeTimer !== null) window.clearTimeout(this.noticeTimer);
    this.noticeTimer = null;
    this.state.notice = null;
    this.render();
  }

  showDialog(title: string, content: HTMLElement): void {
    this.state.dialog = { title, content };
    this.render();
  }

  closeDialog(): void {
    this.state.dialog = null;
    this.render();
  }

  openHostSettings(): void {
    this.client?.send({ kind: "openSettings" });
  }

  openDetail(detail: "workbench" | "review"): void {
    this.client?.send({ kind: "openDetail", detail });
  }

  // ---------------------------------------------------------------- render

  render(): void {
    const focus = this.captureFocus();
    const scrollY = window.scrollY;
    this.shadow.replaceChildren();
    const style = document.createElement("style");
    style.textContent = shellStyles;
    const app = div("app", this.topbar(), this.nav(), this.content(), this.footer());
    this.shadow.append(style, app);
    if (this.state.dialog) this.shadow.appendChild(this.dialog());
    if (this.state.notice) this.shadow.appendChild(this.toast());
    this.restoreFocus(focus);
    window.scrollTo(0, scrollY);
  }

  private topbar(): HTMLElement {
    const projectName = typeof this.state.panel?.["project_name"] === "string" && this.state.panel["project_name"]
      ? String(this.state.panel["project_name"])
      : this.state.project?.scope_name || (this.state.project?.project_id
        ? shortId(this.state.project.project_id, 18)
        : "尚未选择项目");
    const brand = div(
      "brand",
      div("brand-mark", icon("spark", 16)),
      div("brand-copy", div("brand-title", "AI 测试"), div("brand-sub", projectName)),
    );
    const actions = div(
      "top-actions",
      iconButton("zap", "连接核心", () => void this.ensureCore(), this.state.connection === "connecting"),
      iconButton("settings", "设置", () => this.navigate("settings")),
      iconButton("sliders", "诊断", () => this.navigate("diagnostics")),
    );
    return div("topbar", brand, actions);
  }

  private nav(): HTMLElement {
    const nav = document.createElement("nav");
    nav.className = "nav";
    nav.setAttribute("aria-label", "主导航");
    const active = this.state.route === "wizard"
      || this.state.route === "plan"
      || this.state.route === "rules"
      || this.state.route === "workbench"
      ? "home"
      : this.state.route === "diagnostics"
        ? "settings"
        : this.state.route;
    for (const [route, label, iconName] of NAV) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `nav-btn${route === active ? " active" : ""}`;
      button.append(icon(iconName, 15), document.createTextNode(label));
      if (route === active) button.setAttribute("aria-current", "page");
      button.onclick = () => this.navigate(route);
      nav.appendChild(button);
    }
    return nav;
  }

  private content(): HTMLElement {
    const main = document.createElement("main");
    main.className = "content";
    main.id = "main";
    main.appendChild(this.renderRoute());
    return main;
  }

  private renderRoute(): HTMLElement {
    switch (this.state.route) {
      case "wizard":
        return renderWizard(this);
      case "plan":
        return renderPlan(this);
      case "rules":
        return renderRules(this);
      case "workbench":
        return renderWorkbench(this);
      case "reports":
        return renderReports(this);
      case "settings":
        return renderSettings(this);
      case "diagnostics":
        return renderDiagnostics(this);
      default:
        return renderHome(this);
    }
  }

  private footer(): HTMLElement {
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.dataset.state = this.state.connection === "ready"
      ? "ready"
      : this.state.connection === "connecting" ? "connecting" : "unavailable";
    const text = this.state.connection === "ready"
      ? "核心已连接 · 本地工作空间"
      : this.state.connection === "connecting" ? "正在连接核心…" : "未连接核心";
    const status = div("footer-status", dot, document.createTextNode(text));
    const right = document.createElement("button");
    right.type = "button";
    right.className = "btn btn-ghost btn-sm";
    right.textContent = "诊断";
    right.onclick = () => this.navigate("diagnostics");
    return div("footer", status, right);
  }

  private dialog(): HTMLElement {
    const title = this.state.dialog?.title ?? "详情";
    const close = iconButton("x", "关闭", () => this.closeDialog());
    const head = div("dialog-head", div("dialog-title", title), close);
    const body = div("dialog-body");
    if (this.state.dialog) body.appendChild(this.state.dialog.content);
    const card = div("dialog", head, body);
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-label", title);
    const overlay = div("overlay", card);
    overlay.onclick = event => {
      if (event.target === overlay) this.closeDialog();
    };
    return overlay;
  }

  private toast(): HTMLElement {
    const notice = this.state.notice;
    if (!notice) return div("");
    const toneIcon: IconName = notice.tone === "success" ? "check-circle"
      : notice.tone === "danger" ? "alert"
        : notice.tone === "warning" ? "alert" : "info";
    const toast = div(
      "toast",
      icon(toneIcon, 15),
      div("grow", notice.message),
      iconButton("x", "关闭提示", () => this.clearNotice()),
    );
    toast.dataset.tone = notice.tone;
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    return toast;
  }

  // ---------------------------------------------------------------- focus/scroll

  private captureFocus(): { path: number[]; start: number | null; end: number | null; direction: "forward" | "backward" | "none" | null } | null {
    const active = this.shadow.activeElement;
    if (!(active instanceof HTMLElement)) return null;
    const path: number[] = [];
    let node: Element | null = active;
    while (node !== null && node.parentNode !== null) {
      const parent = node.parentNode as ParentNode;
      const index = parent.children ? Array.prototype.indexOf.call(parent.children, node) : -1;
      if (index < 0) return null;
      path.unshift(index);
      node = node.parentElement;
    }
    if (path.length === 0) return null;
    let start: number | null = null;
    let end: number | null = null;
    let direction: "forward" | "backward" | "none" | null = null;
    if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) {
      start = active.selectionStart;
      end = active.selectionEnd;
      direction = active.selectionDirection;
    }
    return { path, start, end, direction };
  }

  private restoreFocus(state: { path: number[]; start: number | null; end: number | null; direction: "forward" | "backward" | "none" | null } | null): void {
    if (state === null) return;
    let container: ParentNode = this.shadow;
    let target: Element | null = null;
    for (const index of state.path) {
      const child: Element | undefined = container.children?.[index];
      if (child === undefined) return;
      target = child;
      container = child;
    }
    if (!(target instanceof HTMLElement)) return;
    target.focus({ preventScroll: true });
    if ((target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)
        && state.start !== null && state.end !== null) {
      try {
        target.setSelectionRange(state.start, state.end, state.direction ?? undefined);
      } catch {
        // Number/date inputs do not support setSelectionRange; focus is still restored.
      }
    }
  }
}

function initialState(hostAvailable: boolean): AppState {
  const preset = (globalThis as { __AITEST_DETAIL?: unknown }).__AITEST_DETAIL;
  const route: Route = preset === "workbench" ? "workbench" : preset === "review" ? "reports" : "home";
  return {
    route,
    hostAvailable,
    connection: hostAvailable ? "idle" : "unavailable",
    connectionMessage: hostAvailable ? "" : "未连接编辑器宿主，面板只做界面预览。",
    doctor: null,
    workspaceId: "",
    project: null,
    panel: null,
    run: null,
    plans: [],
    planDetail: null,
    plansLoaded: false,
    ruleDrafts: [],
    ruleVersions: [],
    ruleDetail: null,
    ruleDetailKind: null,
    rulesLoaded: false,
    issues: [],
    reports: [],
    issuesLoaded: false,
    reportsLoaded: false,
    selectedPlan: null,
    selectedReport: null,
    selectedIssue: null,
    reportDetail: null,
    issueDetail: null,
    reviewIds: {},
    reviewDetails: {},
    reportsTab: "reports",
    deliveries: [],
    deliveriesLoaded: false,
    selectedDelivery: null,
    deliveryDetail: null,
    deliverySubmit: null,
    events: [],
    eventCursor: null,
    eventNote: "",
    workbench: initialWorkbench(),
    notice: null,
    dialog: null,
    advanced: { action: "", parameters: "{}", target: "", expected: "0", result: "", tone: "neutral" },
    model: {
      loaded: false,
      busy: false,
      endpoint: "https://api.deepseek.com",
      model: "deepseek-flash",
      timeout: "60",
      reference: "",
      profiles: [],
      activeProfileId: "",
      note: "",
    },
    busy: null,
    lastPublish: null,
  };
}

function initialWorkbench(): AppState["workbench"] {
  return {
    selectedStep: null,
    executions: {},
    conclusions: {},
    evidence: null,
    evidenceRef: null,
    revisionBasis: null,
    reuse: { sourceRunId: "", caseId: "", inspection: null, note: "" },
    note: "",
  };
}

function parseProfiles(value: unknown): ModelProfile[] {
  return asRecords(value).flatMap(item => {
    const id = item["profile_id"];
    const name = item["name"];
    const settings = asRecord(item["settings"]);
    if (typeof id !== "string" || !id || typeof name !== "string" || !name || !settings) return [];
    const endpoint = settings["endpoint"];
    const model = settings["model"];
    const reference = settings["reference"];
    const timeout = settings["timeout"];
    if (typeof endpoint !== "string" || typeof model !== "string" || typeof reference !== "string"
        || typeof timeout !== "number" || !Number.isInteger(timeout)) return [];
    return [{
      profile_id: id,
      name,
      config_file: typeof item["config_file"] === "string" ? item["config_file"] : undefined,
      settings: { endpoint, model, reference, timeout },
    }];
  });
}

function randomHex(bytes: number): string {
  const values = new Uint8Array(bytes);
  crypto.getRandomValues(values);
  return Array.from(values, value => value.toString(16).padStart(2, "0")).join("");
}

function eventKey(event: Record<string, unknown>): string {
  return [
    event["event_sequence"],
    event["commit_sequence"],
    event["event_type"],
    event["record_id"],
    event["revision"],
  ].map(value => String(value)).join("|");
}

if (!customElements.get("ai-test-panel")) {
  customElements.define("ai-test-panel", AiTestPanel);
}
