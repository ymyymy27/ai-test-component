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

export interface LinkNode {
  node_id: string;
  type?: string;
  status?: string;
  message?: string;
  evidence_ref?: string;
  parent_id?: string;
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
  revision: number;
}

export interface AcceptanceItem {
  item_id: string;
  text: string;
  observable: boolean;
  status: "active" | "pending";
  source?: string;
}

export interface Task {
  task_id: string;
  project_id: string;
  title: string;
  goal?: string;
  intent?: TaskIntent;
  acceptance_items: AcceptanceItem[];
  revision: number;
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
  layer: Layer;
  title?: string;
  preconditions?: string[];
  steps?: string[];
  expected: string;
  expectation_source?: string;
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

export interface Plan {
  plan_id: string;
  task_id: string;
  scope: Scope;
  applicable_count: number;
  cases: PlanCase[];
  templates?: TemplateResult[];
  status: "draft" | "published";
  source_signature?: string;
  revision: number;
}

export interface GeneratedPlan {
  plan_id: string;
  revision: number;
  status: "draft" | "published";
  scope: Scope;
  coverage: { selected: number; applicable: number };
  cases: number;
  source_signature?: string;
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
  status: RunStatus;
  started_at: string;
  heartbeat_at?: string;
  cases: RunCase[];
  evidence_refs: string[];
  analysis: Analysis[];
  mock_judgement?: unknown[];
}

export interface RunSummary {
  run_id: string;
  status: RunStatus;
  started_at: string;
  heartbeat_at?: string;
}

export interface ItemOutcome {
  item_id: string;
  result: CaseResult;
  case_ids: string[];
}

export interface RunDetail {
  run: Run;
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
}

export interface EvidenceItem {
  ref: string;
  name?: string;
  path?: string;
  bytes: number;
  exists: boolean;
  missing_reason?: string;
}

export type ConfigSection = "model" | "services" | "link" | "runners" | "evidence" | "l1" | "data";

export type ProjectConfig = Partial<Record<ConfigSection, Record<string, unknown>>>;

export interface DoctorFacts {
  workspace: { ok: boolean; path: string; workspace_id?: string };
  python: { ok: boolean; version: string };
  package: { ok: boolean; version: string };
  model: { ok: boolean; source: string };
  github: { ok: boolean; path?: string | null };
  proxy: { ok: boolean };
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
  outcome: Verdict;
  counts: Record<string, number>;
}

export interface Opinion {
  by: string;
  at: string;
  text: string;
}
