import type {
  AcceptanceItem,
  Candidate,
  CaseJudgement,
  CaseResult,
  CodeGraph,
  CodeGraphChunk,
  CodeGraphConflict,
  CodeGraphEdge,
  CodeGraphLayer,
  CodeGraphNode,
  ConfidenceSummary,
  ConfidenceTier,
  Confirmation,
  Counts,
  Delivery,
  EvidenceRef,
  ExcludedItemSnapshot,
  FailureAnalysis,
  Feature,
  GithubSnapshot,
  Issue,
  IssueOccurrence,
  LinkData,
  LinkNode,
  LocalGitInfo,
  Module,
  Plan,
  PlanCase,
  PreflightReport,
  ProbeFinding,
  ProbeResult,
  Project,
  ProjectCheck,
  ProjectConfig,
  ProjectConvention,
  ProjectMap,
  QuickCheck,
  Report,
  ReportCaseDetail,
  ReportItemDetail,
  RuleVersion,
  Run,
  RunCase,
  RunItem,
  Task,
  TemplateResult,
  TokenUsage,
  UnknownArea,
} from "@/domain/types";
import { hashText, isoNow } from "@/domain/ids";
import { compareItemNo } from "@/domain/derive";

export { compareItemNo };
import type {
  CodeGraphDto,
  Delivery as DeliveryDto,
  EvidenceItem,
  ExcludedItemDto,
  Issue as IssueDto,
  LinkLocation,
  LocalGitDto,
  Module as ModuleDto,
  PlanCase as PlanCaseDto,
  PlanDetail,
  PreflightReportDto,
  ProbeResultDto,
  Project as ProjectDto,
  ProjectConfig as ProjectConfigDto,
  Report as ReportDto,
  Rule as RuleDto,
  RunCase as RunCaseDto,
  RunDetail,
  Task as TaskDto,
  TemplateResult as TemplateResultDto,
} from "./dto";
import type { HostSidecar } from "./sidecar";

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function asCaseResult(value: unknown, fallback: CaseResult = "not_run"): CaseResult {
  const allowed: CaseResult[] = [
    "passed",
    "failed",
    "blocked",
    "error",
    "not_run",
    "manual_pending",
  ];
  return allowed.includes(value as CaseResult) ? (value as CaseResult) : fallback;
}

function asLayer(value: unknown): "L1" | "L2" | "L3" {
  return value === "L1" || value === "L3" ? value : "L2";
}

function asConfidence(value: unknown): "high" | "medium" | "low" {
  return value === "high" || value === "low" ? value : "medium";
}

function asConfidenceTier(value: unknown): ConfidenceTier {
  const raw = str(value).trim().toLowerCase();
  if (raw === "confirmed" || raw === "confirmed_defect") return "confirmed";
  if (raw === "suspected" || raw === "suspected_issue") return "suspected";
  if (raw === "blocked") return "blocked";
  return "";
}

function asGraphLayer(value: unknown): CodeGraphLayer {
  if (value === "frontend" || value === "api" || value === "backend" || value === "storage") {
    return value;
  }
  return "backend";
}

function asGraphImpact(value: unknown): "none" | "direct" | "transitive" {
  if (value === "direct" || value === "transitive") {
    return value;
  }
  return "none";
}

/** 从堆栈片段或错误签名中安全提取相对源码文件路径与 1-based 行号。 */
export function extractSourceLocation(text: string): { file: string; line: number } | null {
  if (!text) {
    return null;
  }
  const pyFileMatch = /File\s+"([^"\n]+\.(?:py|ts|tsx|js|jsx|vue|html|sql))",\s*line\s*(\d+)/i.exec(text);
  if (pyFileMatch?.[1]) {
    const clean = pyFileMatch[1].replace(/\\/g, "/").replace(/^[./]+/, "");
    return { file: clean, line: Math.max(1, Number(pyFileMatch[2]) || 1) };
  }
  const relMatch = /(?:^|[\s(])([A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*\.(?:py|ts|tsx|js|jsx|vue|html|sql))(?::(\d+)|#L(\d+))?/i.exec(
    text.replace(/\\/g, "/"),
  );
  if (relMatch?.[1] && !relMatch[1].startsWith("http")) {
    const clean = relMatch[1].replace(/^[./]+/, "");
    const lineNum = Number(relMatch[2] ?? relMatch[3] ?? 1);
    return { file: clean, line: Number.isFinite(lineNum) && lineNum > 0 ? lineNum : 1 };
  }
  return null;
}

export function mapConventions(raw: unknown): ProjectConvention[] {
  const list = Array.isArray(raw)
    ? raw
    : isRecord(raw) && Array.isArray(raw.items)
      ? raw.items
      : [];
  return records(list)
    .map((entry, idx) => {
      const pattern = str(entry.pattern).trim();
      if (!pattern) return null;
      return {
        id: str(entry.id) || `conv_${idx}`,
        pattern,
        reason: str(entry.reason) || "项目正常约定",
        matchType: entry.match_type === "regex" ? ("regex" as const) : ("contains" as const),
        scope: str(entry.scope) || "all",
      };
    })
    .filter((item): item is ProjectConvention => item !== null);
}

export function mapPreflight(raw: PreflightReportDto | Record<string, unknown> | null | undefined): PreflightReport | null {
  if (!raw || !isRecord(raw)) {
    return null;
  }
  return {
    ok: raw.ok !== false,
    orderedServices: strList(raw.ordered_services),
    conflictedServices: strList(raw.conflicted_services),
    reusedServices: strList(raw.reused_services),
    portChecks: records(raw.port_checks),
    envChecks: records(raw.env_checks),
    toolChecks: records(raw.tool_checks),
    blockingReasons: strList(raw.blocking_reasons),
    warnings: strList(raw.warnings),
  };
}

function mapProbeFinding(entry: Record<string, unknown>, matchedDefault = false): ProbeFinding {
  return {
    kind: str(entry.kind),
    detail: str(entry.detail),
    source: str(entry.source),
    path: str(entry.path || entry.href || entry.url),
    line: num(entry.line, 0),
    method: str(entry.method),
    conventionMatched: Boolean(entry.convention_matched ?? matchedDefault),
    conventionReason: str(entry.convention_reason),
  };
}

export function mapProbes(raw: ProbeResultDto[] | unknown[] | undefined): ProbeResult[] {
  return records(raw).map((probe, idx) => ({
    probeId: str(probe.probe_id) || `probe_${idx}`,
    name: str(probe.name) || str(probe.probe_id) || `探针 ${idx + 1}`,
    status: str(probe.status) || "skipped",
    checked: num(probe.checked, 0),
    summary: str(probe.summary),
    findings: records(probe.findings).map((f) => mapProbeFinding(f, false)),
    suppressedFindings: records(probe.suppressed_findings).map((f) => mapProbeFinding(f, true)),
  }));
}

export function mapConfidenceSummary(
  raw: Record<string, unknown> | null | undefined,
  caseDetails?: ReportCaseDetail[],
): ConfidenceSummary | null {
  if (raw && isRecord(raw)) {
    return {
      confirmed: num(raw.confirmed, 0),
      suspected: num(raw.suspected, 0),
      blocked: num(raw.blocked, 0),
    };
  }
  if (!caseDetails || caseDetails.length === 0) {
    return null;
  }
  const summary: ConfidenceSummary = { confirmed: 0, suspected: 0, blocked: 0 };
  for (const item of caseDetails) {
    if (item.confidence === "confirmed") summary.confirmed += 1;
    else if (item.confidence === "suspected") summary.suspected += 1;
    else if (item.confidence === "blocked") summary.blocked += 1;
  }
  if (summary.confirmed + summary.suspected + summary.blocked === 0) {
    return null;
  }
  return summary;
}

function mapTokenUsage(raw: unknown): TokenUsage | null {
  if (!isRecord(raw)) {
    return null;
  }
  const calls = num(raw.calls, 0);
  const total = num(raw.total_tokens, 0);
  if (calls === 0 && total === 0) {
    return null;
  }
  return {
    promptTokens: num(raw.prompt_tokens, 0),
    completionTokens: num(raw.completion_tokens, 0),
    totalTokens: total,
    calls,
  };
}

export function mapLocalGit(raw: LocalGitDto | Record<string, unknown> | null | undefined): LocalGitInfo | null {
  if (!raw || !isRecord(raw)) {
    return null;
  }
  return {
    isGit: Boolean(raw.is_git),
    available: Boolean(raw.available),
    reason: str(raw.reason),
    branch: str(raw.branch),
    detached: Boolean(raw.detached),
    head: str(raw.head),
    shortHead: str(raw.short_head),
    headAuthor: str(raw.head_author),
    headTime: str(raw.head_time),
    headSubject: str(raw.head_subject),
    baseBranch: str(raw.base_branch),
    remoteUrl: str(raw.remote_url),
    latestTag: str(raw.latest_tag),
    ahead: num(raw.ahead, 0),
    behind: num(raw.behind, 0),
    dirty: Boolean(raw.dirty),
    stagedFiles: strList(raw.staged_files),
    unstagedFiles: strList(raw.unstaged_files),
    untrackedFiles: strList(raw.untracked_files),
    changedFiles: strList(raw.changed_files),
    diffStat: records(raw.diff_stat).map((row) => ({
      path: str(row.path),
      additions: num(row.additions, 0),
      deletions: num(row.deletions, 0),
      status: str(row.status),
    })),
    changedHunks: records(raw.changed_hunks).map((hunk) => ({
      path: str(hunk.path),
      start: num(hunk.start, 0),
      count: num(hunk.count, 0),
    })),
    recentCommits: records(raw.recent_commits).map((c) => ({
      sha: str(c.sha),
      shortSha: str(c.short_sha),
      author: str(c.author),
      date: str(c.date),
      subject: str(c.subject),
    })),
    hotspots: records(raw.hotspots).map((spot) => ({
      path: str(spot.path),
      commits: num(spot.commits, 0),
    })),
  };
}

export function mapCodeGraph(raw: CodeGraphDto | Record<string, unknown> | null | undefined): CodeGraph | null {
  if (!raw || !isRecord(raw)) {
    return null;
  }
  const statsRaw = isRecord(raw.stats) ? raw.stats : {};
  const nodes: CodeGraphNode[] = records(raw.nodes).map((n) => ({
    id: str(n.id),
    label: str(n.label),
    layer: asGraphLayer(n.layer),
    kind: str(n.kind),
    file: str(n.file),
    line: num(n.line, 0),
    module: str(n.module, "root"),
    meta: isRecord(n.meta) ? n.meta : {},
    hasConflict: Boolean(n.has_conflict),
    impact: asGraphImpact(n.impact),
  }));
  const edges: CodeGraphEdge[] = records(raw.edges).map((e) => ({
    id: str(e.id),
    source: str(e.source),
    target: str(e.target),
    kind: str(e.kind),
    label: str(e.label),
    conflict: Boolean(e.conflict),
  }));
  const conflicts: CodeGraphConflict[] = records(raw.conflicts).map((c) => ({
    kind: str(c.kind),
    severity: str(c.severity, "warning"),
    detail: str(c.detail),
    file: str(c.file),
    nodeIds: strList(c.node_ids),
  }));
  const moduleChunks: CodeGraphChunk[] = records(raw.module_chunks).map((chunk) => {
    const layersRaw = isRecord(chunk.layers) ? chunk.layers : {};
    return {
      chunkId: str(chunk.chunk_id),
      module: str(chunk.module),
      files: strList(chunk.files),
      nodeIds: strList(chunk.node_ids),
      layers: {
        frontend: num(layersRaw.frontend, 0),
        api: num(layersRaw.api, 0),
        backend: num(layersRaw.backend, 0),
        storage: num(layersRaw.storage, 0),
      },
      contentHash: str(chunk.content_hash),
      summary: str(chunk.summary),
    };
  });
  const externalSystems = records(raw.external_systems).map((ext) => ({
    id: str(ext.id),
    name: str(ext.name),
    category: str(ext.category),
    detail: str(ext.detail),
    source: str(ext.source),
  }));
  const impactRaw = isRecord(raw.impact) ? raw.impact : {};
  return {
    generatedAt: str(raw.generated_at),
    sourceHash: str(raw.source_hash),
    stats: {
      totalFiles: num(statsRaw.total_files, 0),
      frontend: num(statsRaw.frontend, 0),
      api: num(statsRaw.api, 0),
      backend: num(statsRaw.backend, 0),
      storage: num(statsRaw.storage, 0),
      edges: num(statsRaw.edges, edges.length),
      conflicts: num(statsRaw.conflicts, conflicts.length),
    },
    nodes,
    edges,
    conflicts,
    moduleChunks,
    externalSystems,
    impact: {
      changedFiles: strList(impactRaw.changed_files),
      directNodeIds: strList(impactRaw.direct_node_ids),
      affectedNodeIds: strList(impactRaw.affected_node_ids),
      affectedModules: strList(impactRaw.affected_modules),
      affectedRoutes: strList(impactRaw.affected_routes),
    },
  };
}

export function mapModule(dto: ModuleDto): Module {
  return {
    id: dto.module_id,
    name: dto.name,
    responsibility: dto.responsibility ?? "",
    owner: dto.owner ?? "",
    status: dto.status ?? "开发中",
    dependencies: (dto.deps ?? []).map((dep) => ({
      to: dep.to,
      type: dep.type,
      note: dep.note ?? "",
    })),
  };
}

export function mapConfig(raw: ProjectConfigDto): ProjectConfig {
  const l1 = isRecord(raw.l1) ? raw.l1 : {};
  const services = isRecord(raw.services) ? raw.services : {};
  const runners = isRecord(raw.runners) ? raw.runners : {};
  const evidence = isRecord(raw.evidence) ? raw.evidence : {};
  const link = isRecord(raw.link) ? raw.link : {};
  const data = isRecord(raw.data) ? raw.data : {};
  const model = isRecord(raw.model) ? raw.model : {};
  return {
    model: { ...model, name: str(model.name ?? model.model) },
    services: {
      ...services,
      healthUrl: str(services.healthUrl ?? services.health ?? services.health_url),
      baseUrl: str(services.baseUrl ?? services.base ?? services.base_url),
    },
    l1: {
      build: str(l1.build),
      entry: Array.isArray(l1.entry) ? l1.entry.filter((item) => typeof item === "string").join(", ") : str(l1.entry),
      smokeCommand: str(l1.smokeCommand ?? l1.smoke_command),
    },
    runners: { ...runners, test: str(runners.test), build: str(runners.build) },
    evidence: {
      ...evidence,
      singleFileMb: num(
        evidence.singleFileMb ?? evidence.file_cap_mb ?? evidence.max_file_mb,
        20,
      ),
      mediaMb: num(
        evidence.mediaMb ?? evidence.media_budget_mb ?? evidence.media_total_mb,
        500,
      ),
      video: bool(evidence.video),
    },
    link: {
      ...link,
      logPath: str(
        link.logPath ??
          link.log_path ??
          (Array.isArray(link.log_paths) ? link.log_paths.join(", ") : ""),
      ),
      query: str(link.query ?? link.db_query),
    },
    data: {
      ...data,
      cleanupCommand: str(data.cleanupCommand ?? data.cleanup ?? data.cleanup_command),
      dataPrefix: str(data.dataPrefix ?? data.data_prefix),
    },
    ...(raw.conventions !== undefined ? { conventions: mapConventions(raw.conventions) } : {}),
    ...(isRecord(raw.environments) ? { environments: { ...raw.environments } } : {}),
  };
}

export function toHostConfig(config: ProjectConfig): ProjectConfigDto {
  const l1 = config.l1 ?? {};
  const services = config.services ?? {};
  const evidence = config.evidence ?? {};
  const link = config.link ?? {};
  const data = config.data ?? {};
  const { healthUrl, baseUrl, ...serviceRest } = services;
  const { singleFileMb, mediaMb, video, ...evidenceRest } = evidence;
  const {
    logPath,
    log_path: legacyLogPath,
    query,
    db_query: legacyDbQuery,
    ...linkRest
  } = link;
  void legacyLogPath;
  const hasQuery = "query" in link || "db_query" in link;
  const queryValue = str(query ?? legacyDbQuery);
  const dataRest = { ...data };
  delete dataRest.cleanupCommand;
  delete dataRest.cleanup_command;
  delete dataRest.dataPrefix;
  delete dataRest.data_prefix;
  const cleanupCommand = str(data.cleanupCommand ?? data.cleanup ?? data.cleanup_command);
  const logPaths = (logPath ?? "")
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
  return {
    model: config.model ? { ...config.model } : {},
    services: {
      ...serviceRest,
      health: healthUrl ?? "",
      base_url: baseUrl ?? "",
    },
    l1: {
      build: l1.build ?? "",
      entry: (l1.entry ?? "")
        .split(/[,，\n]/)
        .map((item) => item.trim())
        .filter(Boolean),
      smoke_command: l1.smokeCommand ?? "",
    },
    runners: config.runners ? { ...config.runners } : {},
    evidence: {
      ...evidenceRest,
      file_cap_mb: singleFileMb ?? 20,
      media_budget_mb: mediaMb ?? 500,
      video: video ?? false,
    },
    link: {
      ...linkRest,
      log_paths: logPaths,
      ...(logPaths.length === 1
        ? { log_path: logPaths[0] }
        : logPaths.length === 0 && "logPath" in link
          ? { log_path: "" }
          : {}),
      ...(hasQuery ? { db_query: queryValue, query: queryValue } : {}),
    },
    data: {
      ...dataRest,
      cleanup: cleanupCommand,
      data_prefix: str(data.dataPrefix ?? data.data_prefix),
    },
    ...(config.conventions !== undefined ? { conventions: config.conventions as unknown as ProjectConfigDto["conventions"] } : {}),
    ...(config.environments !== undefined ? { environments: { ...config.environments } } : {}),
  };
}

export function mapRule(dto: RuleDto): RuleVersion {
  return {
    id: dto.rules_version_id,
    projectId: dto.project_id,
    name: dto.name,
    version: dto.version,
    stepsMd: dto.steps_md,
    aiRules: [...dto.ai_rules],
    evidenceRequirements: [...dto.evidence_requirements],
    revision: dto.revision,
    createdAt: isoNow(),
  };
}

export function mapDelivery(dto: DeliveryDto): Delivery {
  return {
    id: dto.delivery_id,
    taskId: dto.task_id,
    done: [...dto.done],
    notDone: [...dto.not_done],
    changedModules: [...dto.changed_modules],
    interfaceChanges: [...dto.interface_changes],
    runCommands: [...dto.run_commands],
    testData: dto.test_data,
    dependencies: [...dto.dependencies],
    mocks: [...dto.mocks],
    selfTestEvidence: [...dto.self_test_evidence],
    commit: dto.commit,
    complete: dto.complete,
    revision: dto.revision,
    updatedAt: isoNow(),
  };
}

export function mapEvidence(dto: EvidenceItem): EvidenceRef {
  const name = dto.name ?? dto.ref.split("/").pop() ?? dto.ref;
  return {
    id: dto.ref,
    name,
    mediaType: dto.media_type ?? name.split(".").pop()?.toUpperCase() ?? "FILE",
    bytes: dto.bytes,
    sha256: dto.sha256 ?? "",
    note: dto.note ?? "",
    missing: !dto.exists,
    truncated: Boolean(dto.truncated),
    addedAt: isoNow(),
    sourcePath: dto.path ?? dto.ref,
  };
}

function mapFeatures(raw: Record<string, unknown> | null, projectId: string): Feature[] {
  if (!raw) {
    return [];
  }
  return records(raw.business_functions).map((feature, index) => ({
    id: `feat_${projectId.slice(-6)}_${index}`,
    name: str(feature.name, `业务功能 ${index + 1}`),
    modules: strList(feature.modules),
    entryPoints: strList(feature.entry_points),
    testCommands: strList(feature.test_commands),
    testableItems: strList(feature.testable_items),
    evidence: strList(feature.evidence),
    confidence: asConfidence(feature.confidence),
  }));
}

function mapUnknowns(raw: Record<string, unknown> | null): UnknownArea[] {
  if (!raw) {
    return [];
  }
  return records(raw.unknowns).map((unknown, index) => ({
    id: `unk_${index}`,
    area: str(unknown.area, `未知项 ${index + 1}`),
    reason: str(unknown.reason),
  }));
}

export function buildCatalogFromFeatures(
  features: Feature[],
  tasks: Array<{ task: TaskDto }>,
  projectId: string,
  ignoredFeatures: string[] = [],
): AcceptanceItem[] {
  const ignoredSet = new Set(ignoredFeatures);
  const items: AcceptanceItem[] = [];
  features.forEach((feature) => {
    if (ignoredSet.has(feature.name)) {
      return;
    }
    feature.testableItems.forEach((text) => {
      items.push({
        id: `ai_catalog_${projectId.slice(-6)}_${items.length}`,
        no: String(items.length + 1).padStart(2, "0"),
        text,
        observable: true,
        status: "active",
        source: `项目地图 · ${feature.name}`,
        moduleId: null,
        moduleName: feature.modules[0] ?? "未归类",
        featureName: feature.name,
      });
    });
  });
  if (items.length === 0) {
    const seen = new Set<string>();
    tasks.forEach(({ task }) => {
      task.acceptance_items.forEach((item) => {
        if (seen.has(item.text)) {
          return;
        }
        seen.add(item.text);
        items.push({
          id: `ai_catalog_${projectId.slice(-6)}_${items.length}`,
          no: String(items.length + 1).padStart(2, "0"),
          text: item.text,
          observable: item.observable,
          status: item.status,
          source: item.source ?? "历史任务",
          moduleId: item.module_id ?? null,
          moduleName: "未归类",
          featureName: "历史任务",
        });
      });
    });
  }
  return items;
}

export function mapProjectMap(
  raw: Record<string, unknown> | null,
  projectId: string,
  sidecar: HostSidecar,
  stale = false,
  staleReason = "",
): ProjectMap | null {
  if (!raw) {
    return null;
  }
  const mergedIgnored = [
    ...new Set([...strList(raw.ignored_features), ...(sidecar.ignored[projectId] ?? [])]),
  ];
  return {
    revision: num(raw.project_revision ?? raw.revision, 0),
    sourceCommit: str(raw.source_commit, "unknown"),
    generatedAt: str(raw.generated_at, isoNow()),
    features: mapFeatures(raw, projectId),
    ignoredFeatures: mergedIgnored,
    unknowns: mapUnknowns(raw),
    stale,
    staleReason,
  };
}

function mapAcceptanceItem(
  item: TaskDto["acceptance_items"][number],
  index: number,
  modules: Module[],
  features: Feature[],
): AcceptanceItem {
  const module = modules.find((entry) => entry.id === item.module_id) ?? null;
  const feature = features.find((entry) => entry.testableItems.includes(item.text)) ?? null;
  return {
    id: item.item_id,
    no: String(index + 1).padStart(2, "0"),
    text: item.text,
    observable: item.observable,
    status: item.status,
    source: item.source ?? "",
    moduleId: item.module_id ?? null,
    moduleName: module?.name ?? "未归类",
    featureName: feature?.name ?? "未归类",
  };
}


export function mapPlanCase(dto: PlanCaseDto): PlanCase {
  return {
    id: dto.case_id,
    itemId: dto.acceptance_item_id ?? dto.acceptance_item_ids?.[0] ?? "",
    extraItemIds: dto.acceptance_item_ids ?? [],
    layer: dto.layer,
    title: dto.title || dto.expected,
    preconditions: dto.preconditions ?? [],
    steps: dto.steps ?? [],
    expected: dto.expected,
    expectationSource: dto.expectation_source ?? "",
    expectationVerified: dto.expectation_verified === true,
    verifyMethod: dto.verify_method ?? "",
    dependencies: dto.dependencies ?? [],
    mockAllowed: dto.mock_allowed !== false,
    importance: dto.importance ?? "medium",
    dependsOn: dto.depends_on ?? null,
    scriptName: dto.script_name ?? null,
    scriptContent: dto.script_content ?? null,
    manual: (() => {
      if (dto.layer !== "L3") {
        return false;
      }
      const method = (dto.verify_method ?? "").trim().toLowerCase();
      return method === "" || method === "manual" || method.startsWith("manual:");
    })(),
  };
}

export function mapTemplateResult(dto: TemplateResultDto): TemplateResult {
  return {
    templateId: dto.template_id,
    name: dto.name,
    version: dto.version,
    appliedAt: dto.applied_at,
    items: dto.items.map((item) => ({
      id: item.item_id,
      layer: asLayer(item.layer),
      objective: item.objective,
      disposition: item.disposition,
      reason: item.reason,
    })),
    summary: dto.summary,
  };
}

function mapExcludedItems(
  raw: ExcludedItemDto[] | null | undefined,
): ExcludedItemSnapshot[] | undefined {
  if (!Array.isArray(raw)) {
    return undefined;
  }
  return raw.map((entry, index) => ({
    id: str(entry.item_id, `excluded_${index}`),
    no: str(entry.no, String(index + 1).padStart(2, "0")),
    text: str(entry.text),
    moduleId: entry.module_id ?? null,
  }));
}

export function mapPlan(
  detail: PlanDetail,
  projectId: string,
  taskId: string,
  sidecar: HostSidecar,
): Plan {
  const dto = detail.plan;
  const selected = dto.scope.acceptance_item_ids ?? [];
  const applicable = detail.coverage?.applicable ?? dto.applicable_count ?? selected.length;
  const excludedItems = mapExcludedItems(dto.excluded_items);
  return {
    id: dto.plan_id,
    projectId,
    taskId,
    no: dto.plan_no ?? 1,
    revision: dto.revision,
    status: dto.status,
    taskRevision: dto.task_revision ?? 1,
    taskContract: dto.task_contract ?? "",
    createdAt: dto.created_at ?? isoNow(),
    scope: {
      preset: dto.scope.preset,
      moduleIds: dto.scope.module_ids ?? [],
      acceptanceItemIds: selected,
      coverage: { selected: selected.length, applicable },
    },
    applicableCount: applicable,
    rulesVersionId: dto.rules_version_id ?? null,
    cases: dto.cases.map(mapPlanCase),
    itemTexts: dto.item_texts,
    templates: (dto.templates ?? []).map(mapTemplateResult),
    unselectedByModule: (dto.unselected_by_module ?? []).map((entry) => ({
      moduleId: entry.module_id ?? "",
      moduleName: entry.module_name,
      count: entry.count,
    })),
    ...(excludedItems !== undefined ? { excludedItems } : {}),
    warnings: dto.warnings ?? [],
    sourceSignature: dto.source_signature ?? "",
    confirmation: sidecar.confirmations[dto.plan_id] ?? null,
    stale: Boolean(detail.stale),
    staleReason: detail.stale_reason ?? "",
  };
}

function mapCounts(raw: Record<string, number> | undefined, fallback: Counts): Counts {
  if (!raw) {
    return fallback;
  }
  return {
    selected: num(raw.selected, fallback.selected),
    applicable: num(raw.applicable, fallback.applicable),
    covered: num(raw.covered, fallback.covered),
    passed: num(raw.passed, fallback.passed),
    failed: num(raw.failed, fallback.failed),
    incomplete: num(raw.incomplete, fallback.incomplete),
  };
}

function mapProjectChecks(raw: unknown): ProjectCheck[] {
  if (Array.isArray(raw)) {
    return records(raw).map((check, index) => ({
      key: str(check.key ?? check.name, `check_${index}`),
      name: str(check.name ?? check.key, `检查 ${index + 1}`),
      method: str(check.method ?? check.command ?? check.detail),
      result: asCaseResult(check.result ?? check.status, "not_run"),
      detail: str(check.detail ?? check.message ?? ""),
      time: str(check.time ?? check.at ?? ""),
      reason: str(check.reason ?? check.message ?? ""),
      evidenceRef: str(check.evidence_ref ?? check.evidenceRef ?? ""),
    }));
  }
  if (!isRecord(raw)) {
    return [];
  }
  if (Array.isArray(raw.checks)) {
    return mapProjectChecks(raw.checks);
  }
  const blocking = strList(raw.blocking_reasons);
  const checks: ProjectCheck[] = [];
  if (isRecord(raw.build)) {
    const build = raw.build;
    const ok = build.ok === true;
    const exitCode = build.exit_code;
    checks.push({
      key: "build",
      name: "构建",
      method: "项目级构建",
      result: ok ? "passed" : "failed",
      detail: ok
        ? exitCode == null
          ? "未记录退出码"
          : `退出码 ${String(exitCode)}`
        : `退出码 ${String(exitCode ?? "未知")}`,
      time: "",
      reason: blocking.join("；"),
      evidenceRef: str(build.evidence_ref),
    });
  }
  if (Array.isArray(raw.entries)) {
    records(raw.entries).forEach((entry, index) => {
      checks.push({
        key: `entry_${index}`,
        name: `启动入口${entry.module ? ` · ${String(entry.module)}` : ""}`,
        method: entry.module ? `导入 ${String(entry.module)}` : "入口导入",
        result: entry.ok === true ? "passed" : "failed",
        detail: entry.ok === true ? "入口可加载" : "入口加载失败",
        time: "",
        evidenceRef: str(entry.evidence_ref),
      });
    });
  }
  if (Array.isArray(raw.cases)) {
    records(raw.cases).forEach((item, index) => {
      const attempts = records(item.attempts);
      const lastAttempt = attempts[attempts.length - 1];
      const refs = strList(item.evidence_refs);
      const caseId = str(item.case_id, `l1_case_${index}`);
      checks.push({
        key: caseId,
        name: str(item.title, caseId),
        method: str(item.verify_method ?? item.expected),
        result: asCaseResult(item.result, "not_run"),
        detail: str(lastAttempt?.message) || str(item.message),
        time: str(lastAttempt?.finished_at ?? lastAttempt?.started_at),
        reason: asCaseResult(item.result, "not_run") === "passed" ? "" : str(lastAttempt?.message),
        evidenceRef: refs[0] ?? "",
      });
    });
  }
  if (blocking.length && checks.length === 0) {
    checks.push({
      key: "blocking",
      name: "项目级检查",
      method: "",
      result: "blocked",
      detail: "",
      time: "",
      reason: blocking.join("；"),
    });
  }
  return checks;
}

function mapLink(raw: LinkLocation | null, runId: string): LinkData | null {
  if (!raw) {
    return null;
  }
  const rawAssertions = raw.assertions ?? [];
  const nodes: LinkNode[] = [
    ...(raw.upstream ?? []),
    ...(raw.first_anomaly ? [raw.first_anomaly] : []),
    ...(raw.downstream ?? []),
  ].map((node) => ({
    id: node.node_id,
    name: node.type ? `${node.type} ${node.node_id}` : node.node_id,
    state:
      node.status === "passed" ||
      node.status === "failed" ||
      node.status === "blocked" ||
      node.status === "not_run"
        ? node.status
        : "gap",
    detail: node.message ?? "",
    evidenceRef: node.evidence_ref ?? "",
  }));
  return {
    nodes,
    assertions: rawAssertions.map((entry) => ({
      nodeId: String(entry.node_id ?? entry.nodeId ?? ""),
      expected: String(entry.expected ?? ""),
      actual: String(entry.actual ?? ""),
      result: entry.passed === true || entry.result === "passed" ? "passed" : "failed",
      operator: String(entry.operator ?? "equals"),
    })),
    gaps: nodes
      .filter((node) => node.state === "gap")
      .map((node) => ({ nodeId: node.id, reason: node.detail || "链路缺口" })),
    location: raw.location.known
      ? { known: true, path: raw.location.path, line: raw.location.line, reason: "" }
      : { known: false, path: "", line: 0, reason: raw.location.reason },
    unknown: raw.first_anomaly
      ? `首个异常节点：${raw.first_anomaly.node_id}（运行 ${runId}）`
      : "",
  };
}

function mapJudgement(raw: unknown): CaseJudgement | null {
  if (!isRecord(raw)) {
    return null;
  }
  return {
    verdict: str(raw.verdict),
    source: str(raw.source),
    reason: str(raw.reason),
    evidencePoints: strList(raw.evidence_points),
    suggestedFix: str(raw.suggested_fix),
    ...(typeof raw.convention_id === "string" && raw.convention_id
      ? { conventionId: raw.convention_id }
      : {}),
  };
}

function mapRunCase(
  dto: RunCaseDto,
  planCase: PlanCase | null,
  evidence: EvidenceRef[],
): RunCase {
  const lastAttempt = dto.attempts[dto.attempts.length - 1];
  const refs = new Set([...(dto.evidence_refs ?? []), ...(lastAttempt?.evidence_refs ?? [])]);
  const caseItemIds = planCase
    ? [planCase.itemId, ...planCase.extraItemIds].filter(Boolean)
    : [];
  const loc = extractSourceLocation(`${dto.stack_key ?? ""}\n${dto.actual ?? ""}\n${lastAttempt?.message ?? ""}`);
  const confidence = asConfidenceTier(dto.confidence_tier);
  const judgement = mapJudgement(dto.judgement);
  const serviceLogIssues = strList(dto.service_log_issues);
  const phaseFailed = str(lastAttempt?.stage);
  return {
    id: dto.case_id,
    caseId: dto.case_id,
    itemId: planCase?.itemId ?? caseItemIds[0] ?? "",
    caseItemIds,
    title: planCase?.title ?? dto.case_id,
    method: planCase?.verifyMethod ?? "",
    layer: planCase?.layer ?? "L2",
    expected: planCase?.expected ?? "",
    // 没有计划快照就无法核对预期；不能默认成“已核实”。
    expectationVerified: planCase?.expectationVerified ?? false,
    dependsOn: planCase?.dependsOn ?? null,
    actual: dto.actual ?? lastAttempt?.message ?? "",
    result: dto.result,
    evidence: evidence.filter((entry) => refs.has(entry.id) || refs.has(entry.name)),
    message: lastAttempt?.message ?? "",
    errorType: dto.error_type ?? "",
    stackKey: dto.stack_key ?? "",
    attempts: dto.attempts.length,
    manualSteps: dto.attempts.map((attempt, index) => ({
      step: `第 ${index + 1} 次尝试`,
      result: asCaseResult(attempt.result, "not_run"),
      note: attempt.message ?? "",
      at: attempt.finished_at ?? attempt.started_at ?? "",
    })),
    ...(confidence ? { confidence } : {}),
    ...(dto.confidence_reason ? { confidenceReason: str(dto.confidence_reason) } : {}),
    ...(judgement ? { judgement } : {}),
    ...(serviceLogIssues.length ? { serviceLogIssues } : {}),
    ...(phaseFailed ? { phaseFailed } : {}),
    ...(loc ? { file: loc.file, line: loc.line } : {}),
  };
}

export function mapRun(
  detail: RunDetail,
  taskItems: AcceptanceItem[],
  evidence: EvidenceRef[],
): Run {
  const dto = detail.run;
  const plan = detail.plan ?? null;
  const runCasePlan = new Map<string, PlanCase>();
  (plan?.cases ?? []).forEach((testCase) => runCasePlan.set(testCase.case_id, mapPlanCase(testCase)));
  const cases = dto.cases.map((testCase) =>
    mapRunCase(testCase, runCasePlan.get(testCase.case_id) ?? null, evidence),
  );
  const outcomes = new Map<string, CaseResult>();
  (detail.item_outcomes ?? []).forEach((entry) => outcomes.set(entry.item_id, entry.result));
  const selectedIds = dto.scope.acceptance_item_ids ?? [];
  const counts = mapCounts(detail.counts ?? undefined, {
    selected: selectedIds.length,
    applicable: detail.counts?.applicable ?? selectedIds.length,
    covered: 0,
    passed: 0,
    failed: 0,
    incomplete: selectedIds.length,
  });
  const items: RunItem[] = selectedIds.map((itemId) => {
    const item = taskItems.find((entry) => entry.id === itemId);
    return {
      itemId,
      no: item?.no ?? "",
      text: item?.text ?? itemId,
      module: item?.moduleName ?? "",
      feature: item?.featureName ?? "",
      outcome: outcomes.get(itemId) ?? "not_run",
      caseIds: cases
        .filter((testCase) => (testCase.caseItemIds ?? [testCase.itemId]).includes(itemId))
        .map((testCase) => testCase.id),
    };
  });
  items.sort((first, second) => compareItemNo(first.no, second.no));
  const parsedStarted = new Date(dto.started_at).getTime();
  const started = Number.isFinite(parsedStarted) ? parsedStarted : Date.now();
  const startedAt = Number.isFinite(parsedStarted) ? dto.started_at : isoNow();
  const hasElapsed = typeof dto.elapsed_seconds === "number" && Number.isFinite(dto.elapsed_seconds);
  const endCandidate =
    dto.finished_at ?? (typeof dto.heartbeat_at === "string" ? dto.heartbeat_at : "");
  const parsedEnd = endCandidate ? new Date(endCandidate).getTime() : NaN;
  const elapsedMs =
    dto.status === "running"
      ? Math.max(0, Date.now() - started)
      : hasElapsed
        ? Math.max(0, (dto.elapsed_seconds as number) * 1000)
        : Number.isFinite(parsedEnd)
          ? Math.max(0, parsedEnd - started)
          : 0;
  return {
    id: dto.run_id,
    projectId: dto.project_id,
    taskId: detail.task?.task_id ?? plan?.task_id ?? "",
    taskTitle: detail.task?.title ?? "",
    planId: dto.plan_id,
    kind: dto.kind ?? "task",
    status: dto.status,
    postprocess: dto.postprocess ?? "done",
    startedAt,
    finishedAt:
      dto.status === "running"
        ? ""
        : (dto.finished_at ||
          (typeof dto.heartbeat_at === "string" && dto.heartbeat_at
            ? dto.heartbeat_at
            : new Date(started + elapsedMs).toISOString())),
    elapsedMs,
    confirmedBy: dto.confirmed_by ?? "",
    confirmationId: dto.confirmation_id ?? "",
    confirmationFingerprint: dto.confirmation_fingerprint ?? "",
    scope: {
      preset: dto.scope.preset,
      moduleIds: dto.scope.module_ids ?? [],
      acceptanceItemIds: selectedIds,
      coverage: { selected: counts.selected, applicable: counts.applicable },
    },
    cases,
    l1: mapProjectChecks(detail.l1),
    items,
    evidence,
    link: mapLink(detail.link ?? null, dto.run_id),
    verdict: detail.outcome ?? "incomplete",
    counts,
    conclusion: "",
    reportIds: dto.latest_report_id ? [dto.latest_report_id] : [],
    currentCaseId: dto.current_case_id ?? "",
    simulated: false,
    heartbeatAt: dto.heartbeat_at ?? dto.started_at,
    cancelRequested: dto.status === "cancelled",
    environment: dto.environment ?? {},
    dependencySuggestions: (dto.dependency_suggestions ?? []).map((entry) => ({
      module: entry.module,
      package: entry.package,
      source: entry.source ?? "",
      reason: entry.reason ?? "",
      caseId: entry.case_id ?? "",
      installed: entry.installed === "true",
    })),
    dependencyRepairs: (dto.dependency_repairs ?? []).map((entry) => ({
      at: entry.at,
      confirmedBy: entry.confirmed_by,
      packages: entry.packages ?? {},
      ok: entry.ok,
      evidenceRef: entry.evidence_ref ?? "",
    })),
    ...(mapExcludedItems(detail.excluded_items ?? dto.excluded_items) !== undefined
      ? { excludedItems: mapExcludedItems(detail.excluded_items ?? dto.excluded_items) }
      : {}),
    ...(dto.token_usage && (dto.token_usage.calls ?? 0) > 0
      ? {
          tokenUsage: {
            promptTokens: num(dto.token_usage.prompt_tokens, 0),
            completionTokens: num(dto.token_usage.completion_tokens, 0),
            totalTokens: num(dto.token_usage.total_tokens, 0),
            calls: num(dto.token_usage.calls, 0),
          },
        }
      : {}),
    ...(dto.preflight ? { preflight: mapPreflight(dto.preflight) } : {}),
    ...(dto.probe_results?.length ? { probeResults: mapProbes(dto.probe_results) } : {}),
    ...(dto.resumed_from_run_id ? { resumedFromRunId: dto.resumed_from_run_id } : {}),
  };
}

function layerCounts(cases: Array<{ layer: "L1" | "L2" | "L3"; result: CaseResult }>): Report["layers"] {
  const result = {
    L1: { total: 0, passed: 0, failed: 0, incomplete: 0 },
    L2: { total: 0, passed: 0, failed: 0, incomplete: 0 },
    L3: { total: 0, passed: 0, failed: 0, incomplete: 0 },
  };
  cases.forEach((testCase) => {
    const layer = testCase.layer;
    result[layer].total += 1;
    if (testCase.result === "passed") {
      result[layer].passed += 1;
    } else if (testCase.result === "failed") {
      result[layer].failed += 1;
    } else {
      result[layer].incomplete += 1;
    }
  });
  return result;
}

function mapAnalysis(raw: Record<string, unknown>[]): FailureAnalysis {
  const latest = raw[raw.length - 1];
  if (!latest) {
    return {
      status: "not_generated",
      reason: "no_failures",
      observed: "",
      evidenceRefs: [],
      candidateCauses: [],
      unverified: [],
      nextSteps: [],
    };
  }
  const status =
    latest.status === "generated" ||
    latest.status === "not_generated" ||
    latest.status === "invalid"
      ? latest.status
      : "invalid";
  return {
    status,
    reason: (latest.reason as FailureAnalysis["reason"]) ?? "",
    observed: str(latest.observed),
    evidenceRefs: strList(latest.evidence_refs),
    candidateCauses: records(latest.candidate_causes).map((cause) => ({
      cause: str(cause.cause),
      confidence: asConfidence(cause.confidence),
    })),
    unverified: strList(latest.unverified),
    nextSteps: strList(latest.next_steps),
  };
}

export function mapReport(
  dto: ReportDto,
  projectId: string,
  taskId: string,
  plan: Plan | null,
  run: Run | null,
  evidence: EvidenceRef[],
  rulesLabel: string,
): Report {
  const itemDetails: ReportItemDetail[] = records(dto.item_details).map((item) => {
    const itemId = str(item.item_id);
    const runItem = run?.items.find((entry) => entry.itemId === itemId);
    return {
      itemId,
      no: runItem?.no ?? str(item.no ?? item.item_id),
      text: str(item.text),
      outcome: asCaseResult(item.result ?? item.outcome),
      caseIds: strList(item.case_ids),
      selected: item.selected !== false,
    };
  });
  itemDetails.sort((first, second) => compareItemNo(first.no, second.no));
  const caseDetails: ReportCaseDetail[] = records(dto.case_details).map((testCase) => {
    const caseId = str(testCase.case_id);
    const planCase = plan?.cases.find((entry) => entry.id === caseId) ?? null;
    const matchedRunCase = run?.cases.find((entry) => entry.id === caseId) ?? null;
    const refs = strList(testCase.evidence_refs);
    const attRecords = records(testCase.attempts);
    const lastAttemptRec = attRecords[attRecords.length - 1];
    const confidence =
      asConfidenceTier(testCase.confidence_tier) || matchedRunCase?.confidence || "";
    const confidenceReason =
      str(testCase.confidence_reason) || matchedRunCase?.confidenceReason || "";
    const judgement = mapJudgement(testCase.judgement) ?? matchedRunCase?.judgement ?? null;
    const serviceLogIssues = strList(testCase.service_log_issues).length
      ? strList(testCase.service_log_issues)
      : (matchedRunCase?.serviceLogIssues ?? []);
    const phaseFailed =
      str(lastAttemptRec?.stage) || matchedRunCase?.phaseFailed || "";
    const errorType = str(testCase.error_type) || matchedRunCase?.errorType || "";
    const stackKey = str(testCase.stack_key) || matchedRunCase?.stackKey || "";
    const loc =
      extractSourceLocation(`${stackKey}\n${str(lastAttemptRec?.message)}`) ??
      (matchedRunCase?.file ? { file: matchedRunCase.file, line: matchedRunCase.line ?? 1 } : null);
    return {
      caseId,
      itemId: str(testCase.acceptance_item_id),
      itemNo:
        itemDetails.find((item) => item.itemId === str(testCase.acceptance_item_id))?.no ?? "",
      title: str(testCase.title, caseId),
      method: planCase?.verifyMethod ?? "",
      expected: str(testCase.expected),
      actual:
        str(lastAttemptRec?.message) ||
        (str(testCase.result) === "passed" ? "通过" : "未记录"),
      result: asCaseResult(testCase.result),
      evidence: evidence.filter((entry) => refs.includes(entry.id) || refs.includes(entry.name)),
      analysis: null,
      coverageKind: (str(testCase.coverage_kind) || null) as
        | "direct"
        | "file"
        | "shared"
        | "manual"
        | "unverified"
        | null,
      testNodes: strList(testCase.test_nodes),
      ...(confidence ? { confidence } : {}),
      ...(confidenceReason ? { confidenceReason } : {}),
      ...(judgement ? { judgement } : {}),
      ...(serviceLogIssues.length ? { serviceLogIssues } : {}),
      ...(phaseFailed ? { phaseFailed } : {}),
      ...(errorType ? { errorType } : {}),
      ...(stackKey ? { stackKey } : {}),
      ...(loc ? { file: loc.file, line: loc.line } : {}),
    };
  });
  const caseLayers = layerCounts(
    records(dto.case_details).map((testCase) => ({
      layer: asLayer(testCase.layer),
      result: asCaseResult(testCase.result),
    })),
  );
  const coverage = isRecord(dto.coverage) ? dto.coverage : {};
  const counts = mapCounts(dto.counts, {
    selected: num(coverage.selected),
    applicable: num(coverage.applicable),
    covered: 0,
    passed: 0,
    failed: 0,
    incomplete: 0,
  });
  const mockRecords = records(dto.mock_judgement);
  const mockKind = (entry: Record<string, unknown>): unknown => entry.kind ?? entry.result;
  const mockLimitReached = mockRecords.some((entry) => entry.limit_reached === true);
  const mockDetails = mockRecords
    .map((entry) => {
      const name = str(entry.name);
      const kind = str(mockKind(entry));
      const reason = str(entry.reason);
      if (!name) return reason;
      return `${name}(${kind || "unknown"})${reason ? `：${reason}` : ""}`;
    })
    .filter(Boolean);
  const mockRefs = [
    ...new Set(mockRecords.flatMap((entry) => strList(entry.evidence_refs))),
  ];
  const mock = {
    real: mockRecords.filter((entry) => mockKind(entry) === "real").length,
    mock: mockRecords.filter((entry) => mockKind(entry) === "mock").length,
    mixed: mockRecords.filter((entry) => mockKind(entry) === "mixed").length,
    unknown: mockRecords.filter((entry) => mockKind(entry) === "unknown").length,
    realChain: dto.real_chain === true,
    note: mockRecords.length
      ? `${mockLimitReached ? "部分依赖达到读取上限（unknown）；" : ""}${
          mockDetails.length
            ? `依据：${mockDetails.slice(0, 4).join("；")}${mockDetails.length > 4 ? ` 等 ${mockDetails.length} 项` : ""}`
            : "Mock 真实性由后端判定；含模拟或无法判断的用例不能获得真实链路通过。"
        }`
      : "本次没有 Mock 判定记录；未产生真实链路通过结论。",
    refs: mockRefs,
  };
  const l1 = mapProjectChecks(dto.l1);
  const backendLayers = isRecord(dto.layers) ? dto.layers : {};
  const layerTotal = (key: "L1" | "L2" | "L3"): number => {
    const value = backendLayers[key];
    return typeof value === "number" && Number.isFinite(value)
      ? value
      : caseLayers[key].total;
  };
  return {
    id: dto.report_id,
    projectId,
    taskId,
    runId: dto.run_id,
    version: dto.revision,
    generatedAt: dto.generated_at ?? isoNow(),
    verdict: dto.outcome,
    counts,
    conclusion: dto.conclusion,
    coverage: {
      selected: num(coverage.selected),
      applicable: num(coverage.applicable),
      covered: counts.covered,
      unselected: Math.max(0, num(coverage.applicable) - num(coverage.selected)),
    },
    itemDetails,
    caseDetails,
    l1,
    layers: {
      L1: { ...caseLayers.L1, total: layerTotal("L1") },
      L2: { ...caseLayers.L2, total: layerTotal("L2") },
      L3: { ...caseLayers.L3, total: layerTotal("L3") },
    },
    issues: records(dto.issues)
      .map((issue) => str(issue.issue_id))
      .filter(Boolean),
    mock,
    analysis: mapAnalysis(records(dto.analysis)),
    link: run?.link ?? null,
    templates: records(dto.templates).map((template) =>
      mapTemplateResult(template as unknown as TemplateResultDto),
    ),
    modules: strList(dto.changed_modules).map((name) => ({ name, count: 0 })),
    unmapped: strList(dto.unmapped_files),
    uncoveredItems: strList(dto.uncovered_items),
    unverifiedItems: strList(dto.unverified_items),
    planWarnings: strList(dto.plan_warnings ?? []),
    evidenceIndex: evidence,
    source: {
      runTime: run?.startedAt ?? "",
      planLabel: plan ? `计划 ${plan.no} · 第 ${plan.revision} 版` : "计划快照缺失",
      rulesLabel,
      commit: dto.source_commit ?? "未记录",
      model: str((dto.environment.model as unknown) ?? "未记录"),
      github: dto.github ? "已读取 GitHub 快照" : "未连接",
    },
    summary: dto.summary ?? "",
    summarySource: dto.summary_source ?? (dto.summary ? "fallback" : "none"),
    opinion: dto.opinion ?? null,
    status: "final",
    ...(dto.preflight ?? run?.preflight
      ? { preflight: mapPreflight(dto.preflight) ?? run?.preflight ?? null }
      : {}),
    ...(dto.probe_results?.length
      ? { probeResults: mapProbes(dto.probe_results) }
      : run?.probeResults?.length
        ? { probeResults: run.probeResults }
        : {}),
    ...(mapConfidenceSummary(dto.confidence_summary, caseDetails)
      ? { confidenceSummary: mapConfidenceSummary(dto.confidence_summary, caseDetails) }
      : {}),
    ...(mapTokenUsage(dto.token_usage) ?? run?.tokenUsage
      ? { tokenUsage: mapTokenUsage(dto.token_usage) ?? run?.tokenUsage ?? null }
      : {}),
  };
}

export function mapIssue(
  dto: IssueDto,
  projectId: string,
  runs: Map<string, RunDetail>,
): Issue {
  const latest = [...dto.occurrences].sort((first, second) => second.at.localeCompare(first.at))[0];
  const detail = latest ? runs.get(latest.run_id) : undefined;
  const planCase = detail?.plan?.cases.find((testCase) => testCase.case_id === latest?.case_id);
  const occurrences: IssueOccurrence[] = dto.occurrences.map((occurrence) => ({
    runId: occurrence.run_id,
    caseId: occurrence.case_id,
    time: occurrence.at,
    // 问题发生记录均对应一次真实失败；不把当前整体状态 last_result 覆写进历史发生。
    result: "failed",
    note: "",
    evidenceRefs: occurrence.evidence_refs ?? [],
  }));
  const evidenceRefs = [...new Set(dto.occurrences.flatMap((entry) => entry.evidence_refs ?? []))];
  const confidence = asConfidenceTier(dto.confidence_tier);
  const loc = extractSourceLocation(`${dto.signature}\n${dto.actual}`);
  return {
    id: dto.issue_id,
    projectId,
    taskId: detail?.task?.task_id ?? detail?.plan?.task_id ?? "",
    planId: detail?.plan?.plan_id ?? "",
    runId: latest?.run_id ?? "",
    itemId: planCase?.acceptance_item_id ?? "",
    itemNo: dto.item_no || "",
    itemText: dto.item_text || "",
    caseId: latest?.case_id ?? "",
    signature: dto.signature,
    title: dto.title,
    expected: dto.expected,
    actual: dto.actual,
    steps: dto.steps ?? [],
    severity: dto.severity,
    status: dto.status,
    disposition: dto.disposition ?? null,
    reason: dto.reason,
    fixVersion: dto.fix_version ?? "",
    source: dto.occurrences.length ? "auto" : "manual",
    occurrences,
    evidenceRefs,
    verificationRunId: dto.verification_run_id,
    unstable: dto.unstable,
    ...(confidence ? { confidence } : {}),
    ...(dto.confidence_reason ? { confidenceReason: str(dto.confidence_reason) } : {}),
    ...(loc ? { file: loc.file, line: loc.line } : {}),
    createdAt: dto.occurrences[0]?.at ?? isoNow(),
    updatedAt: latest?.at ?? isoNow(),
  };
}

export function mapTask(
  task: TaskDto,
  plans: Plan[],
  runs: Run[],
  delivery: Delivery | null,
  modules: Module[],
  features: Feature[],
  sidecar: HostSidecar,
): Task {
  const sortedPlans = [...plans].sort((first, second) => {
    const no = second.no - first.no;
    return no !== 0 ? no : second.revision - first.revision;
  });
  const sortedRuns = [...runs].sort((first, second) =>
    second.startedAt.localeCompare(first.startedAt),
  );
  const acceptanceItems = task.acceptance_items.map((item, index) =>
    mapAcceptanceItem(item, index, modules, features),
  );
  const applicable = acceptanceItems
    .filter((item) => item.observable && item.status === "active")
    .map((item) => item.id);
  const latestPlan = sortedPlans[0] ?? null;
  const latestRun = sortedRuns[0] ?? null;
  const override = sidecar.stage[task.task_id];
  let stage: Task["stage"] = "scope";
  if (task.stage) {
    stage = task.stage;
  } else if (sortedRuns.some((run) => run.status === "running")) {
    // 真实运行状态优先于 UI 的“调整范围”覆盖，避免运行中还能新建任务。
    stage = "running";
  } else if (override) {
    stage = "scope";
  } else if (!latestPlan) {
    stage = "scope";
  } else if (!latestRun) {
    stage = "planned";
  } else if (latestPlan.createdAt > latestRun.startedAt) {
    stage = "planned";
  } else {
    stage = "result";
  }
  const savedScope = sidecar.scopes[task.task_id];
  const selected =
    savedScope && savedScope.length > 0
      ? savedScope
      : (latestPlan?.scope.acceptanceItemIds ?? applicable);
  return {
    id: task.task_id,
    projectId: task.project_id,
    title: task.title,
    goal: task.goal ?? "",
    intent: task.intent ?? "business",
    origin: task.origin ?? "manual",
    sourceMapRevision: task.source_map_revision ?? 0,
    scope: task.scope ?? "",
    inputs: task.inputs ?? "",
    outputs: task.outputs ?? "",
    preconditions: task.preconditions ?? [],
    executor: task.executor ?? "",
    reviewer: task.reviewer ?? "",
    acceptanceItems,
    recommendedItemIds: sidecar.recommended[task.task_id] ?? selected,
    selectedItemIds: selected.filter((id) => applicable.includes(id)),
    stage,
    previousStage: task.previous_stage ?? override?.previousStage ?? null,
    revision: task.revision,
    createdAt: latestPlan?.createdAt ?? latestRun?.startedAt ?? isoNow(),
    updatedAt: latestRun?.startedAt ?? latestPlan?.createdAt ?? isoNow(),
    plans: sortedPlans,
    currentPlanId:
      task.current_plan_id && sortedPlans.some((plan) => plan.id === task.current_plan_id)
        ? task.current_plan_id
        : (latestPlan?.id ?? null),
    runs: sortedRuns,
    currentRunId:
      task.current_run_id && sortedRuns.some((run) => run.id === task.current_run_id)
        ? task.current_run_id
        : (latestRun?.id ?? null),
    delivery,
    templates: sidecar.templates[task.task_id] ?? latestPlan?.templates ?? [],
  };
}

export interface RawTaskBundle {
  task: TaskDto;
  plans: PlanDetail[];
  runs: RunDetail[];
  delivery: DeliveryDto | null;
}

export function mapProject(input: {
  project: ProjectDto;
  modules: ModuleDto[];
  map: Record<string, unknown> | null;
  mapStale: boolean;
  mapStaleReason: string;
  localGit?: LocalGitDto | null;
  codeGraph?: CodeGraphDto | null;
  config: ProjectConfigDto;
  rules: RuleDto[];
  tasks: RawTaskBundle[];
  reports: ReportDto[];
  issues: IssueDto[];
  runDetails: Map<string, RunDetail>;
  evidence: Map<string, EvidenceRef[]>;
  github?: Record<string, unknown> | null;
  sidecar: HostSidecar;
}): Project {
  const { project, sidecar } = input;
  const modules = input.modules.map(mapModule);
  const projectMap = mapProjectMap(
    input.map,
    project.project_id,
    sidecar,
    input.mapStale,
    input.mapStaleReason,
  );
  const features = projectMap?.features ?? [];
  const ignoredFeatures = projectMap?.ignoredFeatures ?? sidecar.ignored[project.project_id] ?? [];
  const catalog = buildCatalogFromFeatures(
    features,
    input.tasks,
    project.project_id,
    ignoredFeatures,
  );
  const tasks = input.tasks.map((bundle) => {
    const plans = bundle.plans.map((detail) =>
      mapPlan(detail, project.project_id, bundle.task.task_id, sidecar),
    );
    const taskItems = bundle.task.acceptance_items.map((item, index) =>
      mapAcceptanceItem(item, index, modules, features),
    );
    const runs = bundle.runs.map((detail) =>
      mapRun(detail, taskItems, input.evidence.get(detail.run.run_id) ?? []),
    );
    return mapTask(
      bundle.task,
      plans,
      runs,
      bundle.delivery ? mapDelivery(bundle.delivery) : null,
      modules,
      features,
      sidecar,
    );
  });
  const planByRun = new Map<string, Plan>();
  tasks.forEach((task) =>
    task.plans.forEach((plan) => planByRun.set(plan.id, plan)),
  );
  const runById = new Map<string, Run>();
  tasks.forEach((task) => task.runs.forEach((run) => runById.set(run.id, run)));
  const reports = input.reports.map((dto) => {
    const run = runById.get(dto.run_id) ?? null;
    const plan = run ? (planByRun.get(run.planId) ?? null) : null;
    const taskId = run?.taskId ?? "";
    const rule = input.rules.find((entry) => entry.rules_version_id === dto.rules_version_id);
    return mapReport(
      dto,
      project.project_id,
      taskId,
      plan,
      run,
      input.evidence.get(dto.run_id) ?? [],
      rule ? `${rule.name} v${rule.version}` : "未绑定规则",
    );
  });
  tasks.forEach((task) =>
    task.runs.forEach((run) => {
      const report = reports.find((entry) => entry.runId === run.id);
      if (report) {
        run.conclusion = report.conclusion;
      }
    }),
  );
  const issues = input.issues.map((dto) =>
    mapIssue(dto, project.project_id, input.runDetails),
  );
  const activeTaskId =
    sidecar.activeTask[project.project_id] &&
    tasks.some((task) => task.id === sidecar.activeTask[project.project_id])
      ? sidecar.activeTask[project.project_id]
      : null;
  const analysis = sidecar.analysis[project.project_id];
  return {
    id: project.project_id,
    name: project.name,
    root: project.root,
    goal: project.goal ?? "",
    archived: project.archived ?? false,
    createdAt: project.updated_at ?? isoNow(),
    updatedAt: project.updated_at ?? isoNow(),
    analysisStatus: analysis?.status ?? (projectMap || tasks.length ? "ready" : "idle"),
    analysisStartedAt: "",
    analysisError: analysis?.error ?? "",
    candidates: analysis?.candidates ?? [],
    modules,
    map: projectMap,
    ...(input.localGit ? { localGit: mapLocalGit(input.localGit) } : {}),
    ...(input.codeGraph ? { codeGraph: mapCodeGraph(input.codeGraph) } : {}),
    catalog,
    tasks,
    activeTaskId,
    rules: input.rules.map(mapRule),
    rulesVersionId: project.rules_version_id ?? null,
    config: mapConfig(input.config),
    github: sidecar.github[project.project_id] ?? (input.github ? mapGithub(input.github) : null),
    quickChecks: sidecar.quickChecks[project.project_id] ?? [],
    issues,
    reports,
  };
}

export function mapProjectSummary(
  dto: {
    project_id: string;
    name: string;
    root: string;
    updated_at?: string;
    revision?: number;
    archived?: boolean;
  },
  sidecar: HostSidecar,
): Project {
  return {
    id: dto.project_id,
    name: dto.name,
    root: dto.root,
    goal: "",
    createdAt: dto.updated_at ?? isoNow(),
    updatedAt: dto.updated_at ?? isoNow(),
    analysisStatus: sidecar.analysis[dto.project_id]?.status ?? "idle",
    analysisStartedAt: "",
    analysisError: "",
    candidates: sidecar.analysis[dto.project_id]?.candidates ?? [],
    modules: [],
    map: null,
    catalog: [],
    tasks: [],
    activeTaskId: null,
    rules: [],
    rulesVersionId: null,
    config: {},
    github: sidecar.github[dto.project_id] ?? null,
    quickChecks: sidecar.quickChecks[dto.project_id] ?? [],
    issues: [],
    reports: [],
    archived: dto.archived === true,
    summaryOnly: true,
  };
}

export function mapCandidate(
  dto: {
    title: string;
    reason?: string;
    source?: string;
    intent?: "smoke" | "business";
    scope_preview?: { business_functions?: string[]; acceptance_items_estimate?: number };
    uncertainties?: string[];
  },
  projectId: string,
  index: number,
): Candidate {
  const features = dto.scope_preview?.business_functions ?? [];
  const count = dto.scope_preview?.acceptance_items_estimate ?? 0;
  return {
    id: `cand_${projectId.slice(-6)}_${index}_${hashText(dto.title).slice(0, 6)}`,
    title: dto.title,
    reason: dto.reason ?? "模型未提供依据",
    source: dto.source ?? "AI",
    intent: dto.intent ?? "business",
    featureNames: features,
    itemCount: count,
    estimatedScope: `${features.join("、") || "待分析"} · 约 ${count} 项`,
    unknown: (dto.uncertainties ?? []).join("；") || "无",
  };
}

export function mapQuickCheck(
  run: RunDetail,
  projectId: string,
): QuickCheck {
  const dto = run.run;
  const checks = mapProjectChecks(run.l1);
  const status =
    dto.status === "running"
      ? "running"
      : dto.status === "cancelled" || dto.status === "interrupted"
        ? "cancelled"
        : dto.status === "execution_error"
          ? "execution_error"
          : "completed";
  return {
    id: dto.run_id,
    projectId,
    startedAt: dto.started_at,
    finishedAt: status === "running" ? "" : (dto.heartbeat_at ?? dto.started_at),
    status,
    confirmedBy: dto.confirmed_by ?? "本机用户",
    checks,
    simulated: false,
  };
}

/** run.start 回执里的确认记录；只映射 CLI 返回的事实，不补造环境或凭据。 */
export function mapConfirmation(
  dto: {
    confirmation_id?: string;
    confirmed_by?: string;
    confirmed_at?: string;
    fingerprint?: string;
    plan_revision?: number;
    side_effects?: string[];
    dependencies?: string[];
  },
  plan: Plan,
): Confirmation {
  return {
    id: dto.confirmation_id ?? `conf_${plan.id}`,
    planId: plan.id,
    planRevision: typeof dto.plan_revision === "number" ? dto.plan_revision : plan.revision,
    acceptanceItemIds: [...plan.scope.acceptanceItemIds],
    environment: {},
    credentials: {},
    sideEffects: [...(dto.side_effects ?? [])],
    dependencies: [...(dto.dependencies ?? [])],
    fingerprint: dto.fingerprint ?? "",
    confirmedBy: dto.confirmed_by ?? "",
    confirmedAt: dto.confirmed_at ?? "",
  };
}

export function mapGithub(dto: Record<string, unknown>): GithubSnapshot {
  const change = isRecord(dto.change) ? dto.change : dto;
  return {
    repo: str(change.repo),
    branch: str(change.branch, "main"),
    commit: str(change.commit),
    files: strList(change.files),
    prs: Array.isArray(change.prs)
      ? change.prs
          .map((pr) => {
            if (typeof pr === "string") {
              return pr;
            }
            if (isRecord(pr)) {
              const title = str(pr.title);
              if (typeof pr.number === "number") {
                return title ? `#${pr.number} ${title}` : `#${pr.number}`;
              }
              return str(pr.number ?? pr.title);
            }
            return "";
          })
          .filter(Boolean)
      : [],
    checks: Array.isArray(change.checks)
      ? change.checks
          .map((check) =>
            typeof check === "string"
              ? check
              : isRecord(check)
                ? str(check.name ?? check.conclusion)
                : "",
          )
          .filter(Boolean)
      : [],
    deployment: change.deployment ? JSON.stringify(change.deployment) : "",
    account: str(change.account),
    host: str(change.host, "github.com"),
    fetchedAt: str(change.fetched_at, isoNow()),
    cached: dto.cached === true || str(dto.cached) === "true",
    unavailableReason: "",
  };
}
