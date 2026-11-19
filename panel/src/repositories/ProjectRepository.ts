import type { ProjectAnalysis } from "@/domain/analysis";
import type {
  AcceptanceItem,
  BackupResult,
  Confirmation,
  Delivery,
  EnvironmentCheck,
  EvidenceRef,
  ExportResult,
  GithubSnapshot,
  Issue,
  IssueDisposition,
  IssueStatus,
  LinkAssertion,
  LinkData,
  Module,
  ModuleDependency,
  Plan,
  Project,
  ProjectConfig,
  QuickCheck,
  Report,
  RuleVersion,
  Run,
  RunCase,
  Severity,
  Task,
} from "@/domain/types";

export interface CreateProjectInput {
  root: string;
  name: string;
  goal?: string;
}

export interface CreateTaskInput {
  goal: string;
  candidateId?: string;
}

export interface TaskPatch {
  title?: string;
  goal?: string;
  intent?: Task["intent"];
  origin?: Task["origin"];
  scope?: string;
  inputs?: string;
  outputs?: string;
  preconditions?: string[];
  executor?: string;
  reviewer?: string;
  acceptanceItems?: AcceptanceItem[];
}

export interface ConfirmPlanInput {
  operator: string;
  environment: EnvironmentCheck[];
  sideEffects: string[];
}

export interface RunCaseUpdate {
  result: RunCase["result"];
  actual: string;
  message: string;
  errorType: string;
  stackKey: string;
  evidence: EvidenceRef[];
}

export interface ManualResultInput {
  result: "passed" | "failed";
  note: string;
  evidence: EvidenceRef[];
}

export interface EvidenceInput {
  caseId: string;
  name: string;
  mediaType: string;
  bytes: number;
  sha256: string;
  note: string;
}

export interface IssueCreateInput {
  taskId: string;
  itemId: string;
  title: string;
  expected: string;
  actual: string;
  steps: string[];
  severity: Severity;
}

export interface IssuePatch {
  status?: IssueStatus;
  reason?: string;
  disposition?: IssueDisposition | null;
  fixVersion?: string;
  severity?: Severity;
}

export interface DeliveryInput {
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
}

export interface ModuleInput {
  name: string;
  responsibility: string;
  owner: string;
  status: string;
  dependencies: ModuleDependency[];
}

export interface RuleInput {
  name: string;
  version: string;
  stepsMd: string;
  aiRules: string[];
  evidenceRequirements: string[];
}

export interface LinkAssertInput {
  nodeId: string;
  expected: string;
  actual: string;
  operator: string;
}

export interface RepositoryCapabilities {
  hostGithub: boolean;
  evidenceFiles: boolean;
  browserBackup: boolean;
  linkJson: boolean;
}

export interface BackupPayload {
  data: Uint8Array;
  result: BackupResult;
}

export interface ProjectRepository {
  readonly mode: "local" | "host";
  readonly capabilities: RepositoryCapabilities;
  listProjects(): Promise<Project[]>;
  getProject(projectId: string): Promise<Project | null>;
  createProject(input: CreateProjectInput): Promise<Project>;
  /** 宿主支持时弹出原生目录选择器；浏览器模式返回 null。 */
  pickDirectory?(): Promise<string | null>;
  updateProject(
    projectId: string,
    patch: Partial<Pick<Project, "name" | "goal" | "archived">>,
  ): Promise<Project>;
  saveAnalysis(projectId: string, analysis: ProjectAnalysis): Promise<Project>;
  setFeatureIgnored(projectId: string, featureName: string, ignored: boolean): Promise<Project>;
  beginAnalysis(projectId: string): Promise<Project>;
  failAnalysis(projectId: string, message: string): Promise<Project>;

  ensureDraftTask(projectId: string): Promise<Task>;
  createTask(projectId: string, input: CreateTaskInput): Promise<Task>;
  updateTask(projectId: string, taskId: string, patch: TaskPatch): Promise<Task>;
  selectScope(projectId: string, taskId: string, itemIds: string[]): Promise<Task>;
  reviseScope(projectId: string, taskId: string): Promise<Task>;
  cancelRevision(projectId: string, taskId: string): Promise<Task>;
  prepareRerun(projectId: string, taskId: string, itemIds?: string[]): Promise<Plan>;
  setActiveTask(projectId: string, taskId: string): Promise<Project>;

  generatePlan(projectId: string, taskId: string): Promise<Plan>;
  confirmPlan(
    projectId: string,
    taskId: string,
    planId: string,
    input: ConfirmPlanInput,
  ): Promise<Confirmation>;
  startRun(projectId: string, taskId: string, planId: string): Promise<Run>;
  recordRunCase(
    projectId: string,
    runId: string,
    caseId: string,
    update: RunCaseUpdate,
  ): Promise<Run>;
  completeRun(projectId: string, runId: string): Promise<Run>;
  cancelRun(projectId: string, runId: string): Promise<Run>;
  markRunInterrupted(projectId: string, runId: string): Promise<Run>;
  recordManual(
    projectId: string,
    runId: string,
    caseId: string,
    input: ManualResultInput,
  ): Promise<Run>;
  addEvidence(projectId: string, runId: string, input: EvidenceInput): Promise<Run>;

  createIssue(projectId: string, input: IssueCreateInput): Promise<Issue>;
  updateIssue(projectId: string, issueId: string, patch: IssuePatch): Promise<Issue>;
  closeIssue(projectId: string, issueId: string, runId: string, reason: string): Promise<Issue>;

  saveReport(projectId: string, report: Report): Promise<Report>;
  regenerateReport(projectId: string, runId: string): Promise<Report>;
  saveOpinion(projectId: string, reportId: string, by: string, text: string): Promise<Report>;
  exportReport(projectId: string, reportId: string): Promise<ExportResult>;

  saveDelivery(projectId: string, taskId: string, input: DeliveryInput): Promise<Task>;
  draftDelivery(projectId: string, taskId: string): Promise<Delivery>;
  applyTemplate(projectId: string, taskId: string, templateId: string): Promise<Task>;

  saveRule(projectId: string, input: RuleInput): Promise<RuleVersion>;
  bindRule(projectId: string, ruleId: string): Promise<Project>;
  saveConfig(projectId: string, config: ProjectConfig): Promise<Project>;
  saveGithubSnapshot(projectId: string, snapshot: GithubSnapshot | null): Promise<Project>;
  addModule(projectId: string, input: ModuleInput): Promise<Module>;
  updateModule(projectId: string, moduleId: string, patch: Partial<ModuleInput>): Promise<Module>;

  importLink(projectId: string, runId: string, link: LinkData): Promise<Run>;
  assertLink(projectId: string, runId: string, input: LinkAssertInput): Promise<LinkAssertion>;

  recordQuickCheck(projectId: string, quick: QuickCheck): Promise<Project>;

  getRun?(projectId: string, runId: string): Promise<Run | null>;
  runQuickCheck?(projectId: string): Promise<QuickCheck>;
  readGithub?(projectId: string, repo: string, ref: string): Promise<GithubSnapshot>;
  listReports?(projectId: string, runId: string): Promise<Report[]>;
  getReport?(projectId: string, reportId: string): Promise<Report | null>;

  backup(): Promise<BackupPayload>;
  restore(data: string | Uint8Array | ArrayBuffer): Promise<Project[]>;
  clear(): Promise<void>;
  getLastProjectId(): Promise<string | null>;
  setLastProjectId(projectId: string): Promise<void>;
}
