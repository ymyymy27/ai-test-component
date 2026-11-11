/** 面板协议、视图 DTO 与本地面板状态。所有业务值都来自核心回执。 */

export type Json = Record<string, unknown>;

export type Route = "home" | "wizard" | "plan" | "rules" | "workbench" | "reports" | "settings" | "diagnostics";

export type Tone = "neutral" | "info" | "success" | "warning" | "danger";

export type OutcomeStatus = "ok" | "refused" | "unavailable";

/** A core receipt exactly as the host relays it; the panel never invents success. */
export interface Outcome {
  kind?: "outcome";
  action: string | null;
  status: OutcomeStatus;
  code?: string;
  message?: string;
  response?: Record<string, unknown>;
  clientRequestId?: string;
}

export interface ProjectContext {
  kind: "projectContext";
  project_id: string;
  run_id?: string | null;
  scope_name?: string | null;
  context_id?: string;
}

export interface WizardResult {
  kind: "wizardResult";
  clientRequestId?: string;
  error?: string;
  cancelled?: boolean;
  [key: string]: unknown;
}

export interface Row {
  record_id: string;
  revision: number;
  aggregate_kind: string;
}

export interface Session {
  project_id: string;
  root: string;
  source: string;
  entry: string;
  reference?: string;
  endpoint?: string;
  model?: string;
  timeout?: number;
  name?: string;
  existing?: boolean;
}

export interface WizardCommand {
  key: string;
  action: string;
  intent_id: string;
  expected_revision: number;
  binding_revision?: number;
  target?: string;
  parameters: Record<string, unknown>;
}

export interface ExecutionState {
  intent: string;
  actionId?: string;
  authorized?: boolean;
  executed?: boolean;
}

export interface Notice {
  message: string;
  tone: Tone;
}

export interface DoctorDependency {
  key?: unknown;
  state?: unknown;
  reason?: unknown;
  classification?: unknown;
}

export interface DoctorSnapshot {
  dependencies?: DoctorDependency[];
  action_dependencies?: Record<string, string[]>;
  [key: string]: unknown;
}

export interface DoctorResult {
  status?: string;
  protocol?: string;
  supported_actions?: string[];
  dependencies?: DoctorSnapshot;
  model?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ModelProfile {
  profile_id: string;
  name: string;
  settings: ModelProfileSettings;
  config_file?: string;
}

export interface ModelProfileSettings {
  endpoint: string;
  model: string;
  reference: string;
  timeout: number;
}

export interface ModelState {
  loaded: boolean;
  busy: boolean;
  endpoint: string;
  model: string;
  timeout: string;
  reference: string;
  profiles: ModelProfile[];
  activeProfileId: string;
  note: string;
}

export interface WorkbenchState {
  selectedStep: string | null;
  executions: Record<string, ExecutionState>;
  conclusions: Record<string, string>;
  evidence: Record<string, unknown> | null;
  evidenceRef: { evidenceId: string; revision: number } | null;
  /** 按需读取的计划确认状态，用于运行中修订。 */
  revisionBasis: Record<string, unknown> | null;
  /** 复用核对输入与只读结果；登记前不产生选择记录。 */
  reuse: {
    sourceRunId: string;
    caseId: string;
    inspection: Record<string, unknown> | null;
    note: string;
  };
  note: string;
}

export interface AdvancedState {
  action: string;
  parameters: string;
  target: string;
  expected: string;
  result: string;
  tone: Tone;
}

export interface AppState {
  route: Route;
  hostAvailable: boolean;
  connection: "idle" | "connecting" | "ready" | "unavailable";
  connectionMessage: string;
  doctor: DoctorResult | null;
  workspaceId: string;
  project: ProjectContext | null;
  panel: Record<string, unknown> | null;
  run: Record<string, unknown> | null;
  plans: Row[];
  planDetail: Record<string, unknown> | null;
  plansLoaded: boolean;
  ruleDrafts: Row[];
  ruleVersions: Row[];
  ruleDetail: Record<string, unknown> | null;
  ruleDetailKind: "rule_draft" | "rule_version" | null;
  rulesLoaded: boolean;
  issues: Row[];
  reports: Row[];
  issuesLoaded: boolean;
  reportsLoaded: boolean;
  selectedPlan: { record_id: string; revision: number } | null;
  selectedReport: { record_id: string; revision: number } | null;
  selectedIssue: { record_id: string; revision: number } | null;
  reportDetail: Record<string, unknown> | null;
  issueDetail: Record<string, unknown> | null;
  /** 本次会话保存的本地复核编号，按报告分组；报告导出时按此引用。 */
  reviewIds: Record<string, string[]>;
  /** 按需读取到的历史复核详情，按报告分组。 */
  reviewDetails: Record<string, Record<string, unknown>[]>;
  reportsTab: "reports" | "issues" | "delivery";
  deliveries: Row[];
  deliveriesLoaded: boolean;
  selectedDelivery: { record_id: string; revision: number } | null;
  deliveryDetail: Record<string, unknown> | null;
  deliverySubmit: {
    projectRevision: number;
    snapshots: Row[];
    snapshotId: string;
    note: string;
  } | null;
  events: Record<string, unknown>[];
  eventCursor: string | null;
  eventNote: string;
  workbench: WorkbenchState;
  notice: Notice | null;
  dialog: { title: string; content: HTMLElement } | null;
  advanced: AdvancedState;
  model: ModelState;
  busy: string | null;
  lastPublish: Record<string, unknown> | null;
}

export interface WriteOptions {
  target?: string | null;
  expectedRevision?: number;
  controlled?: boolean;
  intentId?: string;
  bindingRevision?: number | null;
}

export type WizardStage =
  | "choose"
  | "configure"
  | "restart"
  | "project"
  | "goals"
  | "model"
  | "review"
  | "manual"
  | "commit"
  | "prepare"
  | "register"
  | "done";

export interface WizardCheck {
  objective: string;
  inputs: string;
  key: string;
  value: string;
  type: "text" | "number" | "boolean";
}

export interface WizardState {
  stage: WizardStage;
  busy: boolean;
  note: string;
  error: string;
  session: Session | null;
  name: string;
  goal: string;
  runTier: "full" | "on_demand";
  dataPolicy: string;
  confirmed: boolean;
  useModel: boolean;
  endpoint: string;
  model: string;
  modelTimeout: string;
  reference: string;
  workspace: string;
  receipts: Record<string, Record<string, unknown>>;
  proposal: Record<string, unknown> | null;
  preview: Record<string, unknown> | null;
  issues: string[];
  catalogConfirmed: boolean;
  checks: WizardCheck[];
  commandProgress: { done: number; total: number; label: string } | null;
  stageProgress: string[];
}
