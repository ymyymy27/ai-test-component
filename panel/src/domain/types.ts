/** 面板领域类型。
 *
 * 字段与 CLI 数据契约保持同义；面板内部使用 camelCase，Repository 负责与
 * 宿主 DTO 做边界映射。结论、覆盖、问题门禁和报告内容只在 domain 层计算一次。
 */

export type ThemeChoice = "auto" | "light" | "dark";
export type AsyncStatus = "idle" | "loading" | "ready" | "error";
export type AnalysisStatus = "idle" | "analyzing" | "ready" | "failed";
export type TaskStage = "fresh" | "scope" | "planned" | "running" | "result";
export type TaskIntent = "smoke" | "business";
export type TaskOrigin = "manual" | "goal" | "range";
export type ScopePreset = "all_applicable" | "selected";
export type Layer = "L1" | "L2" | "L3";
export type RunKind = "task" | "project_check";
export type RunStatus = "running" | "completed" | "cancelled" | "interrupted" | "execution_error";
export type Verdict = "passed" | "failed" | "incomplete" | "not_applicable";
export type CaseResult = "passed" | "failed" | "blocked" | "error" | "not_run" | "manual_pending";
export type IssueStatus = "pending" | "in_progress" | "retest" | "closed";
export type IssueDisposition = "fixed" | "non_defect" | "duplicate" | "deferred";
export type Severity = "P0" | "P1" | "P2" | "P3";
export type MockKind = "real" | "mock" | "mixed" | "unknown";
export type AnalysisRunStatus = "generated" | "not_generated" | "invalid";
export type TemplateDisposition = "applicable" | "not_applicable" | "unsupported" | "unknown";
export type Confidence = "high" | "medium" | "low";

export interface ModuleDependency {
  to: string;
  type: "calls" | "interface" | "shared_data";
  note: string;
}

export interface Module {
  id: string;
  name: string;
  responsibility: string;
  owner: string;
  status: string;
  dependencies: ModuleDependency[];
}

export interface Feature {
  id: string;
  name: string;
  modules: string[];
  entryPoints: string[];
  testCommands: string[];
  testableItems: string[];
  evidence: string[];
  confidence: Confidence;
}

export interface UnknownArea {
  id: string;
  area: string;
  reason: string;
}

export interface ProjectMap {
  revision: number;
  sourceCommit: string;
  generatedAt: string;
  features: Feature[];
  ignoredFeatures: string[];
  unknowns: UnknownArea[];
  stale: boolean;
  staleReason: string;
}

export interface AcceptanceItem {
  id: string;
  no: string;
  text: string;
  observable: boolean;
  status: "active" | "pending";
  source: string;
  moduleId: string | null;
  moduleName: string;
  featureName: string;
}

export interface Candidate {
  id: string;
  title: string;
  reason: string;
  source: string;
  intent: TaskIntent;
  featureNames: string[];
  itemCount: number;
  estimatedScope: string;
  unknown: string;
}

export interface ProjectConfig {
  model?: {
    name?: string;
    [key: string]: unknown;
  };
  services?: {
    healthUrl?: string;
    baseUrl?: string;
    [key: string]: unknown;
  };
  l1?: {
    build?: string;
    entry?: string;
    smokeCommand?: string;
  };
  runners?: {
    test?: string;
    build?: string;
    [key: string]: unknown;
  };
  evidence?: {
    singleFileMb?: number;
    mediaMb?: number;
    video?: boolean;
    [key: string]: unknown;
  };
  link?: {
    logPath?: string;
    query?: string;
    [key: string]: unknown;
  };
  data?: {
    cleanupCommand?: string;
    dataPrefix?: string;
    [key: string]: unknown;
  };
}

export interface RuleVersion {
  id: string;
  projectId: string;
  name: string;
  version: string;
  stepsMd: string;
  aiRules: string[];
  evidenceRequirements: string[];
  revision: number;
  createdAt: string;
}

export interface Delivery {
  id: string;
  taskId: string;
  done: string[];
  notDone: string[];
  changedModules: string[];
  interfaceChanges: string[];
  runCommands: string[];
  testData: string;
  dependencies: string[];
  mocks: string[];
  selfTestEvidence: string[];
  commit: string;
  complete: boolean;
  revision: number;
  updatedAt: string;
}

export interface PlanCase {
  id: string;
  itemId: string;
  extraItemIds: string[];
  layer: Layer;
  title: string;
  preconditions: string[];
  steps: string[];
  expected: string;
  expectationSource: string;
  expectationVerified: boolean;
  verifyMethod: string;
  dependencies: string[];
  mockAllowed: boolean;
  importance: Confidence;
  dependsOn: "build" | "service" | null;
  scriptName: string | null;
  scriptContent: string | null;
  manual: boolean;
}

export interface Scope {
  preset: ScopePreset;
  moduleIds: string[];
  acceptanceItemIds: string[];
  coverage: {
    selected: number;
    applicable: number;
  };
}

export interface Confirmation {
  id: string;
  planId: string;
  planRevision: number;
  acceptanceItemIds: string[];
  environment: Record<string, string>;
  credentials: Record<string, string>;
  sideEffects: string[];
  dependencies: string[];
  fingerprint: string;
  confirmedBy: string;
  confirmedAt: string;
}

export interface TemplateItemResult {
  id: string;
  layer: Layer;
  objective: string;
  disposition: TemplateDisposition;
  reason: string;
}

export interface TemplateResult {
  templateId: string;
  name: string;
  version: string;
  appliedAt: string;
  items: TemplateItemResult[];
  summary: Record<TemplateDisposition, number>;
}

export interface Plan {
  id: string;
  projectId: string;
  taskId: string;
  no: number;
  revision: number;
  status: "draft" | "published";
  taskRevision: number;
  taskContract: string;
  createdAt: string;
  scope: Scope;
  applicableCount: number;
  rulesVersionId: string | null;
  cases: PlanCase[];
  itemTexts?: Record<string, string>;
  templates: TemplateResult[];
  unselectedByModule: Array<{ moduleId: string; moduleName: string; count: number }>;
  warnings: string[];
  sourceSignature: string;
  confirmation: Confirmation | null;
  stale: boolean;
  staleReason: string;
}

export interface EvidenceRef {
  id: string;
  name: string;
  mediaType: string;
  bytes: number;
  sha256: string;
  note: string;
  missing: boolean;
  truncated: boolean;
  addedAt: string;
  sourcePath: string;
}

export interface ProjectCheck {
  key: string;
  name: string;
  method: string;
  result: CaseResult;
  detail: string;
  time: string;
  reason?: string;
  evidenceRef?: string;
}

export interface RunCase {
  id: string;
  caseId: string;
  itemId: string;
  caseItemIds?: string[];
  title: string;
  method: string;
  layer: Layer;
  expected: string;
  expectationVerified: boolean;
  dependsOn: "build" | "service" | null;
  actual: string;
  result: CaseResult;
  evidence: EvidenceRef[];
  message: string;
  errorType: string;
  stackKey: string;
  attempts: number;
  manualSteps: Array<{
    step: string;
    result: CaseResult;
    note: string;
    at: string;
  }>;
}

export interface RunItem {
  itemId: string;
  no: string;
  text: string;
  module: string;
  feature: string;
  outcome: CaseResult;
  caseIds: string[];
}

export interface Counts {
  selected: number;
  applicable: number;
  covered: number;
  passed: number;
  failed: number;
  incomplete: number;
}

export interface DependencySuggestion {
  module: string;
  package: string;
  source: string;
  reason: string;
  caseId: string;
  installed: boolean;
}

export interface DependencyRepair {
  at: string;
  confirmedBy: string;
  packages: Record<string, string>;
  ok: boolean;
  evidenceRef: string;
}

export interface Run {
  id: string;
  projectId: string;
  taskId: string;
  /** 启动时任务标题快照；旧运行可能缺失。 */
  taskTitle?: string;
  planId: string;
  kind: RunKind;
  status: RunStatus;
  postprocess?: "pending" | "running" | "done" | "error";
  startedAt: string;
  finishedAt: string;
  elapsedMs: number;
  confirmedBy: string;
  confirmationId: string;
  confirmationFingerprint: string;
  scope: Scope;
  cases: RunCase[];
  l1: ProjectCheck[];
  items: RunItem[];
  evidence: EvidenceRef[];
  link: LinkData | null;
  verdict: Verdict;
  counts: Counts;
  conclusion: string;
  reportIds: string[];
  currentCaseId: string;
  simulated: boolean;
  heartbeatAt: string;
  cancelRequested: boolean;
  environment?: Record<string, string>;
  dependencySuggestions?: DependencySuggestion[];
  dependencyRepairs?: DependencyRepair[];
}

export interface IssueOccurrence {
  runId: string;
  caseId: string;
  time: string;
  result: CaseResult;
  note: string;
  evidenceRefs: string[];
}

export interface Issue {
  id: string;
  projectId: string;
  taskId: string;
  planId: string;
  runId: string;
  itemId: string;
  itemNo?: string;
  itemText?: string;
  caseId: string;
  signature: string;
  title: string;
  expected: string;
  actual: string;
  steps: string[];
  severity: Severity;
  status: IssueStatus;
  disposition: IssueDisposition | null;
  reason: string;
  fixVersion: string;
  source: "auto" | "manual";
  occurrences: IssueOccurrence[];
  evidenceRefs: string[];
  verificationRunId: string;
  unstable: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface FailureAnalysis {
  status: AnalysisRunStatus;
  reason: "no_failures" | "model_missing" | "model_invalid" | "model_error" | "";
  observed: string;
  evidenceRefs: string[];
  candidateCauses: Array<{ cause: string; confidence: Confidence }>;
  unverified: string[];
  nextSteps: string[];
}

export interface LinkNode {
  id: string;
  name: string;
  state: "passed" | "failed" | "blocked" | "gap" | "not_run";
  detail: string;
  evidenceRef: string;
}

export interface LinkAssertion {
  nodeId: string;
  expected: string;
  actual: string;
  result: "passed" | "failed";
  operator: string;
}

export interface LinkData {
  nodes: LinkNode[];
  assertions: LinkAssertion[];
  gaps: Array<{ nodeId: string; reason: string }>;
  location: {
    known: boolean;
    path: string;
    line: number;
    reason: string;
  };
  unknown: string;
}

export interface MockSummary {
  real: number;
  mock: number;
  mixed: number;
  unknown: number;
  realChain: boolean;
  note: string;
  refs: string[];
}

export interface ReportItemDetail {
  itemId: string;
  no: string;
  text: string;
  outcome: CaseResult;
  caseIds: string[];
  selected: boolean;
}

export interface ReportCaseDetail {
  caseId: string;
  itemId: string;
  itemNo: string;
  title: string;
  method: string;
  expected: string;
  actual: string;
  result: CaseResult;
  evidence: EvidenceRef[];
  analysis: FailureAnalysis | null;
}

export interface Opinion {
  by: string;
  text: string;
  at: string;
}

export interface ReportSource {
  runTime: string;
  planLabel: string;
  rulesLabel: string;
  commit: string;
  model: string;
  github: string;
}

export interface Report {
  id: string;
  projectId: string;
  taskId: string;
  runId: string;
  version: number;
  generatedAt: string;
  verdict: Verdict;
  counts: Counts;
  conclusion: string;
  coverage: {
    selected: number;
    applicable: number;
    covered: number;
    unselected: number;
  };
  itemDetails: ReportItemDetail[];
  caseDetails: ReportCaseDetail[];
  l1: ProjectCheck[];
  layers: Record<Layer, { total: number; passed: number; failed: number; incomplete: number }>;
  issues: string[];
  mock: MockSummary;
  analysis: FailureAnalysis;
  link: LinkData | null;
  templates: TemplateResult[];
  modules: Array<{ name: string; count: number }>;
  unmapped: string[];
  uncoveredItems: string[];
  unverifiedItems: string[];
  planWarnings: string[];
  evidenceIndex: EvidenceRef[];
  source: ReportSource;
  summary: string;
  /** 摘要来源：ai / fallback / none；旧报告可能缺失。 */
  summarySource?: "ai" | "fallback" | "none";
  opinion: Opinion | null;
  status: "final" | "superseded";
}

export interface QuickCheck {
  id: string;
  projectId: string;
  startedAt: string;
  finishedAt: string;
  status: "running" | "completed" | "cancelled" | "execution_error";
  confirmedBy: string;
  checks: ProjectCheck[];
  simulated: boolean;
}

export interface EnvironmentCheck {
  key: string;
  name: string;
  value: string;
  ok: boolean;
  detail: string;
  repair: string;
  risk: "low" | "medium" | "high" | "none";
}

export interface GithubSnapshot {
  repo: string;
  branch: string;
  commit: string;
  files: string[];
  prs: string[];
  checks: string[];
  deployment: string;
  account: string;
  host: string;
  fetchedAt: string;
  cached: boolean;
  unavailableReason: string;
}

export interface Task {
  id: string;
  projectId: string;
  title: string;
  goal: string;
  intent: TaskIntent;
  origin: TaskOrigin;
  sourceMapRevision: number;
  scope: string;
  inputs: string;
  outputs: string;
  preconditions: string[];
  executor: string;
  reviewer: string;
  acceptanceItems: AcceptanceItem[];
  recommendedItemIds: string[];
  selectedItemIds: string[];
  stage: TaskStage;
  previousStage: TaskStage | null;
  revision: number;
  createdAt: string;
  updatedAt: string;
  plans: Plan[];
  currentPlanId: string | null;
  runs: Run[];
  currentRunId: string | null;
  delivery: Delivery | null;
  templates: TemplateResult[];
}

export interface Project {
  id: string;
  name: string;
  root: string;
  goal: string;
  createdAt: string;
  updatedAt: string;
  analysisStatus: AnalysisStatus;
  analysisStartedAt: string;
  analysisError: string;
  candidates: Candidate[];
  modules: Module[];
  map: ProjectMap | null;
  catalog: AcceptanceItem[];
  tasks: Task[];
  activeTaskId: string | null;
  rules: RuleVersion[];
  rulesVersionId: string | null;
  config: ProjectConfig;
  github: GithubSnapshot | null;
  quickChecks: QuickCheck[];
  issues: Issue[];
  reports: Report[];
  archived: boolean;
  /** 宿主项目列表只返回摘要；摘要里的任务/问题/计数不代表真实状态。 */
  summaryOnly?: boolean;
}

export interface WorkspaceEnvelope {
  schemaVersion: 1;
  savedAt: string;
  lastProjectId: string | null;
  projects: Project[];
}

export interface ExportResult {
  filename: string;
  bytes: number;
  lines: number;
  sha256: string;
  markdown: string;
  path?: string;
}

export interface BackupResult {
  filename: string;
  bytes: number;
  projects: number;
  includesEvidence: boolean;
  format: "zip" | "json" | "cli";
  path?: string;
}
