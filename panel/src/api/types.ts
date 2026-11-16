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
  acceptance_items: AcceptanceItem[];
  revision: number;
}

export interface TargetCandidate {
  title: string;
  reason?: string;
  source?: string;
}

export interface SuggestResult {
  candidates: TargetCandidate[];
  fallback: string;
  ai_generated?: boolean;
}

export interface AddTaskResult {
  task_id: string;
  revision: number;
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
  status: "draft" | "published";
  revision: number;
}

export interface GeneratedPlan {
  plan_id: string;
  revision: number;
  status: "draft" | "published";
  scope: Scope;
  coverage: { selected: number; applicable: number };
  cases: number;
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

export interface Analysis {
  status: "generated" | "not_generated" | "invalid";
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

export interface RunDetail {
  run: Run;
  outcome: Verdict | null;
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
