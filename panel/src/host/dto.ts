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
  archived?: boolean;
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
  assertions?: Array<Record<string, unknown>>;
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
  archived?: boolean;
  modules: Module[];
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

export interface CaseGenerationDto {
  source?: "invariant" | "model" | "explicit" | string;
  generator_id?: string;
  generator_version?: string;
}

export interface CapabilitiesDependencyDto {
  type?: string;
  level?: string;
  source?: string;
  status?: string;
}

export interface CapabilitiesSnapshotDto {
  backend?: string[];
  router?: string[];
  auth?: string[];
  frontend_calls?: string[];
  services?: string[];
  start_adapters?: string[];
  dependencies?: CapabilitiesDependencyDto[];
  external_systems?: CapabilitiesDependencyDto[];
  hosts?: string[];
  level?: string;
  unknowns?: string[];
  unsupported?: string[];
  source?: string;
  degraded?: boolean;
  evidence?: string[];
  fingerprint?: string;
}

export interface ProjectHealthSummaryDto {
  level?: string;
  source?: string;
  degraded?: boolean;
  service_count?: number;
  dependencies?: CapabilitiesDependencyDto[];
  route_count?: number;
  uncovered_route_count?: number;
  uncovered_routes_preview?: string[];
  unknowns_count?: number;
  unsupported_count?: number;
  unknowns?: string[];
  unsupported?: string[];
  conflicts_count?: number;
  fingerprint?: string;
}

export interface PlanRecipeDto {
  generator_id?: string;
  generator_version?: string;
  applies_to?: string[];
  invariants?: string[];
  status?: string;
  skip_reason?: string;
}

export interface CoverageLedgerItemDto {
  dimension?: string;
  status?: string;
  required_items?: string[];
  covered_case_ids?: string[];
  note?: string;
}

export interface PlanReviewItemDto {
  review_id?: string;
  kind?: string;
  severity?: "high" | "medium" | "low" | string;
  statement?: string;
  evidence?: string[];
  patch_cases?: Array<Record<string, unknown>>;
}

export interface PlanReviewResultDto {
  plan_id: string;
  plan_revision: number;
  input_hash?: string;
  ai_used?: boolean;
  cached?: boolean;
  high_severity_count?: number;
  reviews: PlanReviewItemDto[];
}

export interface ScriptRepairDto {
  status?: string;
  reason?: string;
  unified_diff?: string;
  guard_blocked?: string[];
  script_name?: string;
  at?: string;
}

export interface PlanRepairCaseResultDto {
  mode: "preview" | "apply";
  ok: boolean;
  blocked: boolean;
  guard_blocked: string[];
  plan_id: string;
  plan_revision: number;
  previous_revision?: number;
  case_id: string;
  script_name?: string;
  unified_diff: string;
  repaired_script?: string;
  error_hint?: string;
  acceptance_item_ids?: string[];
}

export interface AiHypothesisDto {
  case_id?: string;
  hypothesis?: string;
  evidence?: string[];
  confidence?: string;
  next_check?: string;
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
  generation?: CaseGenerationDto | null;
  assertion_strength?: "strong" | "weak" | "unverified" | null;
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

export interface ExcludedItemDto {
  item_id: string;
  no?: string;
  text: string;
  module_id?: string | null;
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
  item_texts?: Record<string, string>;
  capabilities?: CapabilitiesSnapshotDto | null;
  recipes?: PlanRecipeDto[];
  coverage_ledger?: CoverageLedgerItemDto[];
  ai_reviews?: PlanReviewItemDto[];
  unselected_by_module?: UnselectedHint[];
  excluded_items?: ExcludedItemDto[] | null;
  status: "draft" | "published";
  source_signature?: string;
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
  stage?: string;
  started_at?: string;
  finished_at?: string;
  evidence_refs?: string[];
  message?: string;
}

export interface CaseJudgementDto {
  verdict?: string;
  source?: string;
  reason?: string;
  evidence_points?: string[];
  suggested_fix?: string;
  convention_id?: string;
}

export interface RunCase {
  case_id: string;
  result: CaseResult;
  actual?: string;
  error_type?: string;
  stack_key?: string;
  attempts: Attempt[];
  evidence_refs: string[];
  confidence_tier?: string;
  confidence_reason?: string;
  judgement?: CaseJudgementDto | null;
  service_log_issues?: string[];
  resumed_from_run_id?: string;
  script_repair?: ScriptRepairDto | null;
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
  finished_at?: string;
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
  excluded_items?: ExcludedItemDto[] | null;
  token_usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    calls?: number;
  };
  preflight?: PreflightReportDto | null;
  probe_results?: ProbeResultDto[];
  resumed_from_run_id?: string;
  ai_hypotheses?: AiHypothesisDto[];
}

export interface PreflightReportDto {
  ok?: boolean;
  ordered_services?: string[];
  conflicted_services?: string[];
  reused_services?: string[];
  port_checks?: Array<Record<string, unknown>>;
  env_checks?: Array<Record<string, unknown>>;
  tool_checks?: Array<Record<string, unknown>>;
  blocking_reasons?: string[];
  warnings?: string[];
}

export interface ProbeResultDto {
  probe_id?: string;
  name?: string;
  status?: string;
  checked?: number;
  summary?: string;
  findings?: Array<Record<string, unknown>>;
  suppressed_findings?: Array<Record<string, unknown>>;
}

export interface RunSummary {
  run_id: string;
  status: RunStatus;
  current_case_id?: string;
  started_at: string;
  finished_at?: string;
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

export interface RunLogTailEntry {
  name: string;
  text: string;
}

export interface EvidencePreview {
  ref: string;
  kind: "text" | "image" | "binary" | "missing";
  text: string;
  data_url: string;
  truncated: boolean;
}

export interface DetectProjectConfigResult {
  draft: ProjectConfig;
  detected: {
    framework: string[];
    test_commands: string[];
    services: Record<string, unknown>;
  };
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
  /** 运行中可选尾部日志片段。 */
  log_tail?: RunLogTailEntry[];
  /** 运行创建时固化的未纳入适用验收项。 */
  excluded_items?: ExcludedItemDto[] | null;
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
  item_no?: string;
  item_text?: string;
  steps: string[];
  expected: string;
  actual: string;
  file?: string | null;
  line?: number | null;
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
  confidence_tier?: string;
  confidence_reason?: string;
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

export interface ProjectConventionDto {
  id: string;
  pattern: string;
  reason?: string;
  match_type?: "contains" | "regex";
  scope?: string;
}

export type ConfigSection = "model" | "services" | "link" | "runners" | "evidence" | "l1" | "data";

export type ProjectConfig = Partial<Record<ConfigSection, Record<string, unknown>>> & {
  conventions?: ProjectConventionDto[] | { items?: ProjectConventionDto[] };
  environments?: Record<string, unknown>;
};

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
  report?: Report;
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
  source_commit?: string | null;
  environment: Record<string, unknown>;
  github?: Record<string, unknown> | null;
  uncovered_items: string[];
  unverified_items: string[];
  plan_warnings?: string[];
  item_details: Record<string, unknown>[];
  case_details: Record<string, unknown>[];
  evidence_index: Record<string, unknown>[];
  l1?: Record<string, unknown> | null;
  preflight?: PreflightReportDto | null;
  probe_results?: ProbeResultDto[];
  confidence_summary?: Record<string, number> | null;
  token_usage?: Record<string, number> | null;
  capabilities?: CapabilitiesSnapshotDto | null;
  coverage_ledger?: CoverageLedgerItemDto[];
  source_summary?: Record<string, number>;
  assertion_strength?: Record<string, number>;
  revision: number;
}

export interface Opinion {
  by: string;
  at: string;
  text: string;
}

export interface LocalGitDto {
  is_git?: boolean;
  available?: boolean;
  reason?: string;
  branch?: string;
  detached?: boolean;
  head?: string;
  short_head?: string;
  head_author?: string;
  head_time?: string;
  head_subject?: string;
  base_branch?: string;
  remote_url?: string;
  latest_tag?: string;
  ahead?: number;
  behind?: number;
  dirty?: boolean;
  staged_files?: string[];
  unstaged_files?: string[];
  untracked_files?: string[];
  changed_files?: string[];
  diff_stat?: Array<Record<string, unknown>>;
  changed_hunks?: Array<Record<string, unknown>>;
  recent_commits?: Array<Record<string, string>>;
  hotspots?: Array<Record<string, unknown>>;
}

export interface CodeGraphDto {
  schema_version?: number;
  generated_at?: string;
  source_hash?: string;
  stats?: Record<string, number>;
  nodes?: Array<Record<string, unknown>>;
  edges?: Array<Record<string, unknown>>;
  conflicts?: Array<Record<string, unknown>>;
  module_chunks?: Array<Record<string, unknown>>;
  external_systems?: Array<Record<string, unknown>>;
  impact?: Record<string, unknown>;
}

export interface ProjectSnapshot {
  project: Project;
  modules: Module[];
  map: Record<string, unknown> | null;
  map_stale: boolean;
  local_git?: LocalGitDto | null;
  code_graph?: CodeGraphDto | null;
  capabilities?: CapabilitiesSnapshotDto | null;
  health_summary?: ProjectHealthSummaryDto | null;
  config: ProjectConfig;
  tasks: Array<{
    task: Task;
    plans: PlanDetail[];
    delivery: Delivery | null;
  }>;
  runs: RunDetail[];
  evidence: Record<string, EvidenceItem[]>;
  reports: Report[];
  issues: Issue[];
  github?: Record<string, unknown> | null;
}
