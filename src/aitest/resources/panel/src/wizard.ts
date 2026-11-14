/** First-run wizard: project -> environment/model -> analysis -> plan -> review/publish -> prepare/register.
 *
 * The wizard only prepares bounded commands. Every command still goes through the
 * ordinary core guards; publish/authorize actions use the core-owned confirmation
 * window and are never signed by the panel.
 */

import type { WizardHost } from "./context";
import type { Json, Outcome, Session, WizardCommand, WizardResult, WizardState } from "./types";

export const PLAIN_WRITE_ACTIONS: ReadonlySet<string> = new Set([
  "save_context",
  "save_environment",
  "save_case",
  "save_task",
  "save_delivery",
  "save_acceptance",
  "save_dependency_graph",
  "save_project_view_preference",
  "save_rules",
  "import_rules_markdown",
  "generate_draft",
  "request_model_draft",
  "resolve_model_response",
  "save_plan",
  "analyze_project",
  "prepare_run",
  "register_run",
  "prepare_execution",
  "execute_step",
  "start_run",
  "pause_run",
  "resume_run",
  "cancel_run",
  "retry_step",
  "return_stage",
  "verify_pending",
  "attach_evidence",
  "record_agent_evidence",
  "import_external_result",
  "create_report",
  "export_report",
  "record_fix",
  "request_regression",
]);

/** `HUMAN_ACTIONS` from the core contract: these use the native confirmation window. */
export const CONTROLLED_ACTIONS: ReadonlySet<string> = new Set([
  "authorize_step",
  "create_plan_grant",
  "revoke_plan_grant",
  "revise_pending_steps",
  "narrow_driver",
  "publish_plan",
  "publish_rules",
  "submit_delivery",
  "record_local_review",
  "save_model_outbound_policy",
  "close_issue",
  "update_issue",
  "save_binding",
  "bind_project",
  "confirm_basis",
  "prepare_approval",
  "revoke_approval",
  "select_case_reuse",
]);

export const WIZARD_STEPS = ["选择项目", "准备环境", "发现测试", "生成计划", "核对发布", "准备运行"] as const;

const WIZARD_READ_ACTIONS = new Set(["discover_tests"]);

const ACTION_LABELS: Record<string, string> = {
  save_context: "保存项目",
  save_binding: "绑定目录",
  save_environment: "登记环境",
  analyze_project: "分析源码",
  save_model_outbound_policy: "保存模型材料范围",
  generate_draft: "生成计划提案",
  save_case: "保存用例",
  save_acceptance: "保存验收范围",
  confirm_basis: "确认断言依据",
  publish_plan: "发布计划",
  prepare_run: "准备运行",
  register_run: "登记运行",
};

export const DEFAULT_AI_GOAL = "由 AI 根据项目理解提出验证目标";
const DEFAULT_DATA_POLICY = "纯内存计算，不连接外部系统，结束后无需清理";

function initialState(): WizardState {
  return {
    stage: "choose",
    busy: false,
    note: "",
    error: "",
    session: null,
    name: "",
    goal: DEFAULT_AI_GOAL,
    runTier: "on_demand",
    driver: "planned",
    dataPolicy: DEFAULT_DATA_POLICY,
    confirmed: false,
    useModel: true,
    endpoint: "https://api.deepseek.com",
    model: "deepseek-flash",
    modelTimeout: "60",
    reference: "",
    workspace: "",
    pythonVersion: "3.11.9",
    sourceSnippets: true,
    testMode: false,
    testInventory: null,
    receipts: {},
    proposal: null,
    expectedFactsText: "",
    preview: null,
    issues: [],
    catalogConfirmed: false,
    commandProgress: null,
    stageProgress: [],
  };
}

export class WizardController {
  state: WizardState = initialState();

  constructor(private readonly host: WizardHost) {}

  reset(): void {
    this.state = initialState();
    this.render();
  }

  stageIndex(): number {
    switch (this.state.stage) {
      case "choose":
      case "configure":
        return 0;
      case "restart":
      case "project":
        return 1;
      case "discover":
        return 2;
      case "goals":
      case "model":
      case "manual":
        return 3;
      case "review":
      case "commit":
        return 4;
      case "prepare":
      case "register":
        return 5;
      case "done":
        return 6;
    }
  }

  completedSteps(): Set<number> {
    const active = this.stageIndex();
    return new Set(Array.from({ length: Math.min(active, WIZARD_STEPS.length) }, (_, index) => index));
  }

  private render(): void {
    this.host.render();
  }

  private setBusy(busy: boolean, note = ""): void {
    this.state.busy = busy;
    if (note) this.state.note = note;
    this.render();
  }

  private fail(message: string, code?: string): void {
    this.state.busy = false;
    this.state.commandProgress = null;
    this.state.error = code ? `${message}（${code}）` : message;
    this.render();
  }

  async chooseProject(): Promise<void> {
    if (this.state.busy) return;
    this.setBusy(true, "正在打开项目选择…");
    const result = await this.host.wizardRequest("wizardChoose");
    if (!this.resultOk(result)) return;
    if (result.cancelled) {
      this.setBusy(false, "已取消选择。");
      return;
    }
    const session = asRecord(result.session) as Session | undefined;
    if (!session || typeof session.project_id !== "string") {
      this.fail("项目选择没有返回有效的项目身份。");
      return;
    }
    this.state.session = session;
    this.state.testMode = typeof session.entry === "string"
      && session.entry.length > 0
      && !session.entry.toLowerCase().endsWith(".py");
    this.state.testInventory = null;
    this.state.name = typeof session.name === "string" ? session.name : "";
    this.state.endpoint = typeof session.endpoint === "string" ? session.endpoint : this.state.endpoint;
    this.state.model = typeof session.model === "string" ? session.model : this.state.model;
    this.state.modelTimeout = session.timeout !== undefined ? String(session.timeout) : this.state.modelTimeout;
    this.state.reference = typeof session.reference === "string" ? session.reference : "";
    // 换项目后不能沿用上一个项目的提案、依据回执或覆盖确认。
    this.resetDerived();
    this.state.goal = DEFAULT_AI_GOAL;
    this.state.dataPolicy = DEFAULT_DATA_POLICY;
    this.state.confirmed = false;
    this.state.stage = "configure";
    this.state.error = "";
    this.setBusy(false, "项目已选择。准备环境后，AI 会给出验证目标候选。");
  }

  private resetDerived(): void {
    this.state.workspace = "";
    this.state.receipts = {};
    this.state.proposal = null;
    this.state.expectedFactsText = "";
    this.state.preview = null;
    this.state.issues = [];
    this.state.catalogConfirmed = false;
    this.state.commandProgress = null;
  }

  async loadModelSettings(): Promise<void> {
    if (this.state.busy) return;
    const result = await this.host.wizardRequest("wizardModelLoad");
    if (!this.resultOk(result, false)) return;
    const settings = asRecord(result.modelSettings);
    if (settings) {
      if (typeof settings["endpoint"] === "string") this.state.endpoint = settings["endpoint"];
      if (typeof settings["model"] === "string") this.state.model = settings["model"];
      if (settings["timeout"] !== undefined) this.state.modelTimeout = String(settings["timeout"]);
      if (typeof settings["reference"] === "string") this.state.reference = settings["reference"];
    }
    this.render();
  }

  async saveCredential(): Promise<void> {
    if (this.state.busy) return;
    this.setBusy(true, "正在打开 API Key 输入…");
    const result = await this.host.wizardRequest("wizardCredential");
    if (!this.resultOk(result)) return;
    if (result.cancelled) {
      this.setBusy(false, "已取消凭据配置。");
      return;
    }
    if (typeof result.reference === "string") this.state.reference = result.reference;
    this.setBusy(false, "凭据已保存到系统安全存储，正文不会进入面板。");
  }

  async prepareEnvironment(): Promise<void> {
    if (this.state.busy) return;
    if (!this.state.name.trim() || !this.state.goal.trim() || !this.state.dataPolicy.trim() || !this.state.confirmed) {
      this.fail("请填写项目名称、验证目标与数据复位说明，并确认数据隔离。");
      return;
    }
    if (this.state.useModel) {
      const timeout = Number(this.state.modelTimeout);
      if (!Number.isInteger(timeout) || timeout < 10 || timeout > 120) {
        this.fail("模型请求超时请填写 10–120 之间的整数秒。");
        return;
      }
      if (!this.state.reference) {
        this.fail("请填写一次 API Key，或关闭模型辅助使用手工计划。");
        return;
      }
    }
    this.setBusy(true, "正在准备独立测试环境…");
    const result = await this.host.wizardRequest("wizardSetup", {
      dataConfirmed: true,
      useModel: this.state.useModel,
      endpoint: this.state.endpoint,
    });
    if (!this.resultOk(result)) return;
    const setup = asRecord(result.setup);
    if (setup && typeof setup["workspace"] === "string") this.state.workspace = setup["workspace"];
    if (setup && typeof setup["python_version"] === "string") this.state.pythonVersion = setup["python_version"];
    this.state.stage = "restart";
    this.state.error = "";
    this.state.note = "环境与模型配置已写入，正在受控重启核心…";
    this.state.busy = false;
    this.render();
    await this.restartAndContinue();
  }

  /** Restart applies carriers/model configuration; the core loads it only at startup. */
  async restartAndContinue(): Promise<void> {
    if (this.state.busy) return;
    this.state.stage = "restart";
    this.state.busy = true;
    this.state.error = "";
    this.state.note = "正在受控重启核心…";
    this.render();
    const outcome = await this.host.restartCore();
    this.state.busy = false;
    if (outcome.status !== "ok") {
      this.fail(outcome.message ?? "核心重启未完成；请处理活动执行后重试。", outcome.code);
      return;
    }
    this.state.stage = "project";
    this.state.note = "核心已重启。正在保存项目并分析源码…";
    this.render();
    await this.runStage("project");
  }

  async runStage(stage: "project" | "discover" | "model" | "commit" | "prepare" | "register"): Promise<void> {
    if (this.state.busy) return;
    this.state.stage = stage;
    this.state.busy = true;
    this.state.error = "";
    this.state.note = "正在生成这一阶段的受控命令…";
    this.render();
    let result: WizardResult;
    try {
      result = await this.host.wizardRequest("wizardCommands", {
        stage,
        form: this.form(),
        receipts: this.state.receipts,
      });
    } catch (error) {
      this.fail(error instanceof Error ? error.message : "向导命令生成失败。");
      return;
    }
    if (!this.resultOk(result)) return;
    const commands = Array.isArray(result.commands) ? result.commands : [];
    if (commands.length === 0) {
      this.fail("这一阶段没有生成任何受控命令，请核对表单。");
      return;
    }
    const queue = commands as WizardCommand[];
    for (let index = 0; index < queue.length; index += 1) {
      const command = queue[index];
      this.state.commandProgress = {
        done: index,
        total: queue.length,
        label: ACTION_LABELS[command.action] ?? command.action,
      };
      this.state.note = `正在执行：${ACTION_LABELS[command.action] ?? command.action}`;
      this.render();
      const outcome = WIZARD_READ_ACTIONS.has(command.action)
        ? await this.host.readOnly(command.action, command.parameters)
        : await this.host.write(command.action, command.parameters, {
            intentId: command.intent_id,
            expectedRevision: command.expected_revision,
            target: command.target,
            bindingRevision: command.binding_revision ?? null,
            controlled: CONTROLLED_ACTIONS.has(command.action) || !PLAIN_WRITE_ACTIONS.has(command.action),
          });
      if (!this.outcomeOk(outcome, command.action)) {
        const resultBody = asRecord(outcome.response?.["result"]);
        const reason = outcome.message?.trim()
          || textOf(resultBody?.["message"])
          || textOf(resultBody?.["reason"])
          || textOf(resultBody?.["blocked_reason"])
          || proposalErrorText(resultBody?.["proposal_error"])
          || blockedReason(resultBody?.["blocked_by"])
          || "这一步没有完成。";
        this.fail(reason, outcome.code);
        return;
      }
      const resultBody = asRecord(outcome.response?.["result"]) ?? {};
      this.state.receipts[command.key] = resultBody;
      if (command.key === "suggestions") {
        const proposal = asRecord(resultBody["proposal"]);
        const selection = asRecord(resultBody["test_selection"]);
        if (proposal) this.state.proposal = proposal;
        if (selection) this.state.proposal = selection;
      }
    }
    this.state.commandProgress = { done: queue.length, total: queue.length, label: "完成" };
    this.state.busy = false;
    this.state.note = "本阶段已保存。";

    if (stage === "project") {
      const snapshot = asRecord(this.state.receipts["snapshot"]);
      const understanding = snapshot ? asRecord(snapshot["understanding"]) : undefined;
      const understandingBody = understanding ? asRecord(understanding["body"]) : undefined;
      // analyze_project 把 goal_candidates 放在结果顶层；理解载荷中则位于
      // body 内。两种都读，避免把有候选的项目误判成“没有目标”。
      const rawGoals = snapshot?.["goal_candidates"]
        ?? understandingBody?.["goal_candidates"]
        ?? understanding?.["goal_candidates"];
      const goals = Array.isArray(rawGoals)
        ? rawGoals.filter((item): item is Record<string, unknown> =>
            item !== null && typeof item === "object" && !Array.isArray(item))
        : [];
      if (this.state.testMode) {
        this.state.stage = "discover";
        this.render();
        await this.runStage("discover");
        return;
      }
      this.state.stage = this.state.useModel
        ? (goals.length > 0 ? "goals" : "model")
        : "manual";
      this.render();
      return;
    }
    if (stage === "discover") {
      const inventory = asRecord(this.state.receipts["discover"]);
      if (!inventory) {
        this.fail("没有取得测试清单，请核对 pytest 与环境。");
        return;
      }
      this.state.testInventory = inventory;
      this.state.stage = this.state.useModel ? "model" : "manual";
      this.setBusy(false, "测试清单已发现，可以生成测试范围计划。");
      return;
    }
    if (stage === "model") {
      if (this.state.proposal) {
        await this.review();
      } else {
        this.setBusy(false, "模型没有返回结构化提案，请重试或核对模型配置。");
      }
      return;
    }
    if (stage === "commit") {
      await this.runStage("prepare");
      return;
    }
    if (stage === "prepare") {
      await this.runStage("register");
      return;
    }
    if (stage === "register") {
      this.state.stage = "done";
      this.state.note = "运行已登记。打开工作台，逐项确认操作、执行并核验。";
      this.host.setNotice("测试计划与运行已准备完成。", "success");
      this.render();
    }
  }

  async review(): Promise<void> {
    if (this.state.busy) return;
    if (!this.state.testMode && this.state.expectedFactsText.trim() && !this.expectedFacts()) {
      this.fail("验证预期必须是 {用例编号: {字段: 预期值}} 的非空 JSON 对象。");
      return;
    }
    this.setBusy(true, "正在校验计划提案…");
    const result = await this.host.wizardRequest("wizardReview", { form: this.form() });
    if (!this.resultOk(result)) return;
    const issues = Array.isArray(result.issues) ? result.issues : [];
    if (result.valid === false || issues.length > 0) {
      this.state.issues = issues.map(issueText).filter(Boolean);
      this.state.preview = null;
      this.state.stage = "review";
      this.state.busy = false;
      this.state.note = "计划校验未通过，请按提示修改后重新校验。";
      this.render();
      return;
    }
    const preview = asRecord(result.preview) ?? (this.state.testMode ? asRecord(result) : undefined);
    if (!preview) {
      this.fail("计划校验没有返回可核对的预览。");
      return;
    }
    this.state.preview = preview;
    this.state.issues = [];
    this.state.stage = "review";
    this.setBusy(false, "请核对计划用例、覆盖与依据，然后确认发布。");
  }

  async publish(): Promise<void> {
    if (!this.state.preview) {
      this.fail("请先校验计划提案。");
      return;
    }
    if (!this.state.testMode && !this.state.catalogConfirmed) {
      this.fail("发布前请确认业务功能目录完整、unknown 数量已知。");
      return;
    }
    await this.runStage("commit");
  }

  backToReview(): void {
    if (!this.state.proposal) return;
    this.state.stage = "review";
    this.state.error = "";
    this.render();
  }

  async prepareAndRegister(): Promise<void> {
    await this.runStage("prepare");
  }

  complete(): void {
    this.host.navigate("workbench");
  }

  setProposalFromJson(raw: string): void {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("提案必须是 JSON 对象。");
      }
      this.state.proposal = parsed as Json;
      this.state.preview = null;
      this.state.catalogConfirmed = false;
      this.state.error = "";
      this.state.note = "提案已修改，请重新校验后再发布。";
    } catch (error) {
      this.state.error = error instanceof Error ? error.message : "提案 JSON 无效。";
    }
    this.render();
  }

  form(): Record<string, unknown> {
    const snapshot = asRecord(this.state.receipts["snapshot"]);
    const understanding = snapshot ? asRecord(snapshot["understanding"]) : undefined;
    const preview = this.state.preview ?? {};
    const expected = this.expectedFacts();
    let proposal = this.state.proposal;
    if (proposal && expected) {
      const cloned = JSON.parse(JSON.stringify(proposal)) as Record<string, unknown>;
      const cases = Array.isArray(cloned["cases"]) ? cloned["cases"] : [];
      for (const item of cases) {
        const record = asRecord(item);
        const caseId = record && typeof record["case_id"] === "string" ? record["case_id"] : "";
        const facts = caseId ? expected[caseId] : undefined;
        if (record && facts) {
          record["expected_facts"] = facts;
          record["assertion_basis_text"] = JSON.stringify({
            schema_version: "aitest.pure-function-basis/1",
            expected_facts: facts,
          });
        }
      }
      proposal = cloned;
    }
    return {
      project_id: this.state.session?.project_id,
      workspace_id: this.host.workspaceId(),
      source: this.state.session?.source,
      entry: this.state.session?.entry,
      name: this.state.name.trim(),
      goal: this.state.goal.trim(),
      data_policy: this.state.dataPolicy.trim(),
      python_version: this.state.pythonVersion,
      dependency_declaration: "独立 Python 3.11+ 环境，仅标准库",
      endpoint: this.state.endpoint,
      model: this.state.model,
      model_timeout: Number(this.state.modelTimeout),
      understanding,
      proposal,
      coverage_contract: preview["coverage_contract"] ?? null,
      understanding_ref: preview["understanding_ref"] ?? null,
      catalog_confirmed: this.state.catalogConfirmed,
      allowed_material_kinds: this.state.sourceSnippets
        ? ["project_context", "template_content", "source_snippet"]
        : ["project_context", "template_content"],
      source_snippets_enabled: this.state.sourceSnippets,
      source_scope: ".",
      test_mode: this.state.testMode,
      test_inventory: this.state.testMode ? this.state.testInventory : null,
      run_tier: this.state.runTier,
      initial_driver: this.state.driver,
    };
  }

  /** Parse the optional machine-readable expected facts map. */
  private expectedFacts(): Record<string, Record<string, unknown>> | undefined {
    const text = this.state.expectedFactsText.trim();
    if (!text) return undefined;
    try {
      const parsed: unknown = JSON.parse(text);
      const map = asRecord(parsed);
      if (!map) return undefined;
      const result: Record<string, Record<string, unknown>> = {};
      for (const [caseId, value] of Object.entries(map)) {
        const facts = asRecord(value);
        if (!caseId.trim() || !facts || Object.keys(facts).length === 0) return undefined;
        result[caseId] = facts;
      }
      return Object.keys(result).length > 0 ? result : undefined;
    } catch {
      return undefined;
    }
  }

  private resultOk(result: WizardResult, report = true): boolean {
    if (result.error) {
      this.fail(String(result.error));
      return false;
    }
    if (report && result.cancelled) {
      this.setBusy(false, "已取消当前操作。");
      return false;
    }
    return true;
  }

  private outcomeOk(outcome: Outcome, action: string): boolean {
    if (outcome.status !== "ok" || !outcome.response) {
      return false;
    }
    const result = asRecord(outcome.response["result"]);
    if (result && result["blocked"] === true) return false;
    if (result && result["published"] === false && action === "publish_plan") return false;
    return true;
  }
}

function proposalErrorText(value: unknown): string | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  const message = textOf(record["message"]) ?? textOf(record["code"]);
  const issues = Array.isArray(record["issues"])
    ? record["issues"]
        .map(issue => {
          const item = asRecord(issue);
          if (!item) return "";
          const path = textOf(item["path"]);
          const detail = textOf(item["message"]);
          return [path, detail].filter(Boolean).join("：");
        })
        .filter(Boolean)
    : [];
  // `message` 已经是 issues 的连接串；两者同时拼接会把同一原因显示两遍。
  const text = issues.length > 0 ? issues.join("；") : (message ?? "");
  return text ? `计划结构未通过校验：${text}` : undefined;
}

const BLOCKED_REASONS: Record<string, string> = {  model_not_configured: "模型未配置或凭据不可用；请重新填写 API Key 并重启核心。",
  stale_outbound_policy: "模型材料范围已变化；请重新确认发送范围后重试。",
  model_basis_unverified: "源码分析依据无法核对；请重新分析项目后再生成计划。",
  source_reference_required: "模型材料引用了源码，但没有可核对的快照；请重新分析项目。",
  structured_output_invalid: "模型返回的计划结构不符合约定；可以重试或核对模型配置。",
  response_store_unavailable: "模型响应的本地保存不可用；请检查工作空间后重试。",
};

function blockedReason(value: unknown): string | undefined {
  if (!Array.isArray(value) || value.length === 0) return undefined;
  const reasons = value
    .map(item => typeof item === "string" ? BLOCKED_REASONS[item] ?? `核心阻塞：${item}` : "")
    .filter(Boolean);
  return reasons.length > 0 ? reasons.join("；") : undefined;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function issueText(value: unknown): string {
  if (typeof value === "string") return value;
  const record = asRecord(value);
  if (!record) return "";
  const field = typeof record["field"] === "string" ? record["field"] : "";
  const message = typeof record["message"] === "string" ? record["message"] : "";
  return [field, message].filter(Boolean).join("：");
}

function textOf(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}
