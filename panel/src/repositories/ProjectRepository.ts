import type { ProjectAnalysis } from "@/domain/analysis";
import type { EvidencePreview, RunLogTailEntry } from "@/host/dto";
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
  ProjectConvention,
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
  /** 范围测试不把系统生成的说明写进项目目标。 */
  writeProjectGoal?: boolean;
  /** 任务来源：按目标测试或按范围测试。 */
  origin?: "goal" | "range";
  /** 粘贴或摘要生成的需求描述，用于后端 AI 生成交付与计划。 */
  requirement?: string;
  /** 业务建测入口方式。 */
  intent?: "changes" | "flow" | "requirement";
  /** 选中的业务功能名称。 */
  featureNames?: string[];
  /** 按流程测试自动串联的端到端步骤。 */
  flowSteps?: string[];
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

export interface ConventionInput {
  pattern: string;
  reason: string;
  matchType?: "contains" | "regex";
  scope?: string;
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
  /** 宿主工作空间路径；浏览器演示模式为空。 */
  workspacePath?(): string;
  listProjects(): Promise<Project[]>;
  getProject(projectId: string): Promise<Project | null>;
  /** 仅宿主页内存同步：写动作刚通过 getProject 更新缓存时直接复用，避免二次 snapshot。 */
  cachedProject?(projectId: string): Project | null;
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
  deleteTask(projectId: string, taskId: string): Promise<void>;
  selectScope(projectId: string, taskId: string, itemIds: string[]): Promise<Task>;
  reviseScope(projectId: string, taskId: string): Promise<Task>;
  cancelRevision(projectId: string, taskId: string): Promise<Task>;
  prepareRerun(projectId: string, taskId: string, itemIds?: string[]): Promise<Plan>;
  repairRunDependencies(
    projectId: string,
    runId: string,
    packages: string[],
    confirmedBy: string,
  ): Promise<Run>;
  setActiveTask(projectId: string, taskId: string): Promise<Project>;

  generatePlan(projectId: string, taskId: string): Promise<Plan>;
  confirmPlan(
    projectId: string,
    taskId: string,
    planId: string,
    input: ConfirmPlanInput,
  ): Promise<Confirmation>;
  startRun(projectId: string, taskId: string, planId: string, confirmedBy?: string): Promise<Run>;
  startTargetedRerun?(
    projectId: string,
    taskId: string,
    planId: string,
    itemIds: string[],
    confirmedBy?: string,
  ): Promise<Run>;
  detectProjectConfig?(projectId: string): Promise<ProjectConfig>;
  previewEvidence?(
    projectId: string,
    runId: string,
    ref: string,
  ): Promise<EvidencePreview | null>;
  fetchRunLogTail?(projectId: string, runId: string): Promise<RunLogTailEntry[]>;
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
  addConvention?(projectId: string, input: ConventionInput): Promise<ProjectConvention[]>;
  removeConvention?(projectId: string, conventionId: string): Promise<ProjectConvention[]>;
  saveGithubSnapshot(projectId: string, snapshot: GithubSnapshot | null): Promise<Project>;
  addModule(projectId: string, input: ModuleInput): Promise<Module>;
  updateModule(projectId: string, moduleId: string, patch: Partial<ModuleInput>): Promise<Module>;

  importLink(projectId: string, runId: string, link: LinkData): Promise<Run>;
  assertLink(projectId: string, runId: string, input: LinkAssertInput): Promise<LinkAssertion>;

  recordQuickCheck(projectId: string, quick: QuickCheck): Promise<Project>;

  getRun?(projectId: string, runId: string, includeEvidence?: boolean): Promise<Run | null>;
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
