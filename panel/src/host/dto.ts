/** 与一期《CLI 与数据契约》对齐的只读 DTO 类型。
 *
 * 面板只消费 CLI 返回的字段，不推导结论：结论、覆盖、计数都取后端字段。
 */

export type RunStatus =
  | "running"
  | "completed"
  | "cancelled"
  | "interrupted"
  | "execution_error";

export type Verdict = "passed" | "failed" | "incomplete" | "not_applicable";

export type CaseResult =
  | "passed"
  | "failed"
  | "blocked"
  | "error"
  | "not_run"
  | "manual_pending";

export type Layer = "L1" | "L2" | "L3";

export type IssueStatus = "pending" | "in_progress" | "retest" | "closed";

export type Severity = "P0" | "P1" | "P2" | "P3";

export interface ProjectSummary {
  project_id: string;
  name: string;
  root: string;
  revision?: number;
}

export interface Module {
  module_id: string;
  name: string;
  responsibility?: string;
  owner?: string;
  status?: string;
  deps?: ModuleDependency[];
}

export interface ModuleDependency {
  to: string;
  type: "calls" | "interface" | "shared_data";
  note?: string;
}

export interface ModuleSuggestion {
  name: string;
  responsibility: string;
  owner: string;
  deps: ModuleDependency[];
}

export interface DeliveryDraft {
  done: string[];
  not_done: string[];
  changed_modules: string[];
  interface_changes: string[];
  run_commands: string[];
  test_data: string[];
  dependencies: string[];
  mocks: string[];
  self_test_evidence: string[];
}

export interface Delivery {
  delivery_id: string;
  task_id: string;
  done: string[];
  not_done: string[];
  changed_modules: string[];
  interface_changes: string[];
  run_commands: string[];
  test_data: string;
  dependencies: string[];
  mocks: string[];
  self_test_evidence: string[];
  commit: string;
  complete: boolean;
  revision: number;
}

export interface TaskSummary {
  task_id: string;
  title: string;
  goal?: string;
  revision: number;
}

export type TaskIntent = "smoke" | "business";

export type TemplateDisposition =
  | "applicable"
  | "not_applicable"
  | "unsupported"
  | "unknown";

export interface TemplateSummary {
  template_id: string;
  name: string;
  version: string;
  item_count: number;
}

export interface TemplateItemResult {
  item_id: string;
  layer: string;
  objective: string;
  disposition: TemplateDisposition;
  reason: string;
}

export interface TemplateResult {
  template_id: string;
  name: string;
  version: string;
  applied_at: string;
  items: TemplateItemResult[];
  summary: Record<TemplateDisposition, number>;
}

export interface RuleSummary {
  rules_version_id: string;
  name: string;
  version: string;
}

export interface Rule extends RuleSummary {
  project_id: string;
  steps_md: string;
  ai_rules: string[];
  evidence_requirements: string[];
  revision: number;
}

export interface GithubChange {
  repo: string;
  branch: string;
  commit: string;
  files: string[];
  prs: Record<string, unknown>[];
  checks: Record<string, unknown>[];
  deployment: Record<string, unknown> | null;
  account?: string;
  host?: string;
  fetched_at?: string;
}

export interface LinkNode {
  node_id: string;
  type?: string;
  status?: string;
  message?: string;
  evidence_ref?: string;
  parent_id?: string;
}

export interface LinkImportResult {
  nodes: LinkNode[];
  assertions: Record<string, unknown>[];
  gaps: { node_id: string; reason: string }[];
  location: LinkLocation;
}

export interface LinkLocation {
  first_anomaly: LinkNode | null;
  upstream: LinkNode[];
  downstream: LinkNode[];
  location:
    | { known: true; path: string; line: number }
    | { known: false; reason: string };
}

export interface Project {
  project_id: string;
  name: string;
  root: string;
  goal?: string;
  modules: Module[];
  rules_version_id?: string | null;
  revision: number;
  updated_at?: string;
}

export interface AcceptanceItem {
  item_id: string;
  text: string;
  observable: boolean;
  status: "active" | "pending";
  source?: string;
  module_id?: string | null;
}

export interface Task {
  task_id: string;
  project_id: string;
  title: string;
  goal?: string;
  intent?: TaskIntent;
  origin?: "manual" | "goal" | "range";
  source_map_revision?: number;
  scope?: string;
  inputs?: string;
  outputs?: string;
  preconditions?: string[];
  executor?: string;
  reviewer?: string;
  acceptance_items: AcceptanceItem[];
  revision: number;
  stage?: "fresh" | "scope" | "planned" | "running" | "result";
  previous_stage?: "scope" | "planned" | "running" | "result" | null;
  current_plan_id?: string | null;
  current_run_id?: string | null;
}

export interface TargetCandidate {
  title: string;
  reason?: string;
  source?: string;
  intent?: TaskIntent;
}

export interface SuggestResult {
  candidates: TargetCandidate[];
  fallback: string;
  fallback_intent?: TaskIntent;
  ai_generated?: boolean;
}

export interface AddTaskResult {
  task_id: string;
  revision: number;
  intent?: TaskIntent;
  acceptance_items: { item_id: string; status: string; text: string }[];
}

export interface PlanCase {
  case_id: string;
  acceptance_item_id: string | null;
  acceptance_item_ids?: string[];
  layer: Layer;
  title?: string;
  preconditions?: string[];
  steps?: string[];
  dependencies?: string[];
  expected: string;
  expectation_source?: string;
  expectation_verified?: boolean;
  warnings?: string[];
  verify_method?: string;
  mock_allowed?: boolean;
  importance?: "high" | "medium" | "low";
  depends_on?: "build" | "service" | null;
  script_name?: string | null;
  script_content?: string | null;
}

export interface Scope {
  preset: "all_applicable" | "selected";
  module_ids: string[];
  acceptance_item_ids: string[];
  frozen_at?: string | null;
}

export interface UnselectedHint {
  module_id: string | null;
  module_name: string;
  count: number;
}

export interface Plan {
  plan_id: string;
  task_id: string;
  plan_no?: number;
  task_revision?: number;
  task_contract?: string;
  created_at?: string;
  scope: Scope;
  applicable_count: number;
  cases: PlanCase[];
  templates?: TemplateResult[];
  unselected_by_module?: UnselectedHint[];
  status: "draft" | "published";
  source_signature?: string;
  rules_version_id?: string | null;
  warnings?: string[];
  revision: number;
}

export interface GeneratedPlan {
  plan_id: string;
  plan_no?: number;
  task_revision?: number;
  created_at?: string;
  revision: number;
  status: "draft" | "published";
  scope: Scope;
  coverage: { selected: number; applicable: number };
  cases: number;
  case_count?: number;
  dependencies?: string[];
  ai_generated?: boolean;
  source_signature?: string;
}

export interface PlanDetail {
  plan: Plan;
  coverage: { selected: number; applicable: number };
  stale?: boolean;
  stale_reason?: string;
  task_updated?: boolean;
}

export interface Attempt {
  attempt: number;
  result: CaseResult;
  started_at?: string;
  finished_at?: string;
  evidence_refs?: string[];
  message?: string;
}

export interface RunCase {
  case_id: string;
  result: CaseResult;
  actual?: string;
  error_type?: string;
  stack_key?: string;
  attempts: Attempt[];
  evidence_refs: string[];
}

export type AnalysisReason =
  | "no_failures"
  | "model_missing"
  | "model_invalid"
  | "model_error"
  | "";

export interface Analysis {
  status: "generated" | "not_generated" | "invalid";
  reason?: AnalysisReason;
  model?: string;
  generated_at?: string;
  observed?: string;
  evidence_refs?: string[];
  candidate_causes?: { cause: string; confidence?: string }[];
  unverified?: string[];
  next_steps?: string[];
}

export interface Run {
  run_id: string;
  plan_id: string;
  project_id: string;
  scope: Scope;
  confirmed_by?: string;
  confirmed_at?: string;
  confirmation_id?: string;
  confirmation_fingerprint?: string;
  kind?: "task" | "project_check";
  status: RunStatus;
  postprocess?: "pending" | "running" | "done" | "error";
  current_case_id?: string;
  started_at: string;
  heartbeat_at?: string;
  elapsed_seconds?: number;
  cases: RunCase[];
  evidence_refs: string[];
  analysis: Analysis[];
  mock_judgement?: unknown[];
  latest_report_id?: string;
  dependency_install_ok?: boolean | null;
  l1?: Record<string, unknown> | null;
  environment?: Record<string, string>;
  dependency_suggestions?: Array<{
    module: string;
    package: string;
    source?: string;
    reason?: string;
    case_id?: string;
    installed?: string;
  }>;
  dependency_repairs?: Array<{
    at: string;
    confirmed_by: string;
    packages: Record<string, string>;
    ok: boolean;
    evidence_ref?: string;
  }>;
  environment_history?: Array<Record<string, unknown>>;
}

export interface RunSummary {
  run_id: string;
  status: RunStatus;
  current_case_id?: string;
  started_at: string;
  heartbeat_at?: string;
  elapsed_seconds?: number | null;
  plan_id?: string;
  plan_no?: number;
  plan_revision?: number;
  task_id?: string;
  task_title?: string;
  task_snapshot?: boolean;
  outcome?: Verdict | null;
  counts?: Record<string, number>;
  scope?: { selected: number; applicable: number };
  latest_report_id?: string;
}

export interface ItemOutcome {
  item_id: string;
  result: CaseResult;
  case_ids: string[];
}

export interface RunDetail {
  run: Run;
  /** Exact plan snapshot used by this run. */
  plan?: Plan | null;
  /** Task snapshot captured at run start; null for old runs without one. */
  task?: Task | null;
  task_snapshot?: boolean;
  outcome: Verdict | null;
  /** Core-derived counts; the panel must not recompute them. */
  counts?: Record<string, number>;
  /** Core-derived worst result per selected acceptance item. */
  item_outcomes?: ItemOutcome[];
  pending_items?: number;
  manual_cases?: number;
  l1?: Record<string, unknown> | null;
  /** "id：text" rows for applicable acceptance items this run did not select. */
  uncovered_items?: string[];
  /** Imported link localisation, or null when no link.json exists. */
  link?: LinkLocation | null;
}

export interface IssueSummary {
  issue_id: string;
  title: string;
  status: IssueStatus;
  severity: Severity;
  occurrences: number;
  last_at?: string;
  last_result?: string;
  unstable?: boolean;
  disposition?: IssueDisposition | null;
  reason?: string;
  verification_run_id?: string;
  last_case_id?: string;
  last_run_id?: string;
}

export type IssueDisposition = "fixed" | "non_defect" | "duplicate" | "deferred";

export interface IssueOccurrence {
  run_id: string;
  case_id: string;
  at: string;
  evidence_refs: string[];
}

export interface Issue {
  issue_id: string;
  project_id: string;
  signature: string;
  title: string;
  steps: string[];
  expected: string;
  actual: string;
  status: IssueStatus;
  severity: Severity;
  disposition?: IssueDisposition | null;
  reason: string;
  unstable: boolean;
  last_result: string;
  verification_run_id: string;
  fix_version?: string | null;
  revision: number;
  occurrences: IssueOccurrence[];
}

export interface EvidenceItem {
  ref: string;
  name?: string;
  path?: string;
  bytes: number;
  exists: boolean;
  missing_reason?: string;
  sha256?: string;
  media_type?: string;
  truncated?: boolean;
  note?: string;
}

export type ConfigSection = "model" | "services" | "link" | "runners" | "evidence" | "l1" | "data";

export type ProjectConfig = Partial<Record<ConfigSection, Record<string, unknown>>>;

export interface BackupResult {
  path: string;
  sha256: string;
  bytes: number;
  includes_evidence: boolean;
  files: number;
  estimated_evidence_bytes?: number;
  estimated_evidence_mb?: number;
}

export interface DoctorFacts {
  workspace: { ok: boolean; path: string; workspace_id?: string };
  python: { ok: boolean; version: string; executable?: string };
  package: { ok: boolean; version: string };
  model: { ok: boolean; source: string; status?: number; base_url?: string; error?: string };
  github: { ok: boolean; path?: string | null; account?: string; host?: string };
  proxy: { ok: boolean; configured?: Record<string, unknown> };
  project_config?: { ok: boolean; problems?: unknown[] };
  references?: { ok: boolean; missing?: unknown[] };
  repairs: RepairItem[];
  repairs_done?: string[];
  explanation?: string;
}

export interface RepairItem {
  risk: "low" | "medium" | "high";
  command: string;
  auto?: boolean;
}

export interface ReportSummary {
  report_id: string;
  run_id?: string;
  generated_at?: string;
  outcome: Verdict;
  conclusion?: string;
  counts: Record<string, number>;
  coverage?: { selected: number; applicable: number };
  uncovered_items?: string[];
  unverified_items?: string[];
}

export interface ReportVersion {
  report_id: string;
  run_id: string;
  generated_at: string;
  outcome: Verdict;
  revision: number;
  has_opinion: boolean;
}

export interface Report {
  report_id: string;
  run_id: string;
  generated_at?: string;
  outcome: Verdict;
  conclusion: string;
  counts: Record<string, number>;
  coverage: Record<string, number>;
  changed_modules: string[];
  unmapped_files: string[];
  evidence_refs: string[];
  mock_judgement: Record<string, unknown>[];
  real_chain: boolean;
  summary?: string | null;
  summary_source?: "ai" | "fallback" | "none";
  opinion?: Opinion | null;
  issues: Record<string, unknown>[];
  issue_count: number;
  failure_occurrences: number;
  analysis: Record<string, unknown>[];
  layers: Record<string, number>;
  rules_version_id?: string | null;
  source_commit?: string | null;
  templates: Record<string, unknown>[];
  environment: Record<string, unknown>;
  github?: Record<string, unknown> | null;
  uncovered_items: string[];
  unverified_items: string[];
  plan_warnings?: string[];
  item_details: Record<string, unknown>[];
  case_details: Record<string, unknown>[];
  evidence_index: Record<string, unknown>[];
  l1?: Record<string, unknown> | null;
  revision: number;
}

export interface Opinion {
  by: string;
  at: string;
  text: string;
}

export interface ProjectSnapshot {
  project: Project;
  modules: Module[];
  map: Record<string, unknown> | null;
  map_stale: boolean;
  config: ProjectConfig;
  rules: Rule[];
  tasks: Array<{
    task: Task;
    plans: PlanDetail[];
    delivery: Delivery | null;
  }>;
  runs: RunDetail[];
  evidence: Record<string, EvidenceItem[]>;
  reports: Report[];
  issues: Issue[];
}
