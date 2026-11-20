import { isoNow, newId } from "./ids";
import type {
  AcceptanceItem,
  Candidate,
  FailureAnalysis,
  Feature,
  MockSummary,
  Module,
  Project,
  ProjectMap,
  Run,
  TemplateDisposition,
  TemplateItemResult,
  TemplateResult,
} from "./types";

export interface ProjectAnalysis {
  modules: Module[];
  map: ProjectMap;
  catalog: AcceptanceItem[];
  candidates: Candidate[];
}

const MODULE_SEEDS = [
  {
    key: "entry",
    name: "启动入口",
    responsibility: "启动脚本、入口文件与运行命令",
    owner: "",
    status: "已识别",
  },
  {
    key: "core",
    name: "主流程",
    responsibility: "核心调用链与主路径",
    owner: "",
    status: "已识别",
  },
  {
    key: "data",
    name: "数据",
    responsibility: "数据写入、读取与去重",
    owner: "",
    status: "已识别",
  },
  {
    key: "boundary",
    name: "接口边界",
    responsibility: "输入校验、错误返回与边界条件",
    owner: "",
    status: "待确认",
  },
];

const FEATURE_SEEDS = [
  {
    key: "startup",
    name: "启动与主流程",
    itemNos: ["01"],
    modules: ["启动入口", "主流程"],
    entryPoints: ["README 启动命令", "package scripts"],
    testCommands: ["最小测试命令"],
    evidence: ["README", "启动脚本"],
    confidence: "high" as const,
  },
  {
    key: "data",
    name: "数据写入与查询",
    itemNos: ["02", "04"],
    modules: ["数据"],
    entryPoints: ["数据写入与查询入口"],
    testCommands: ["已有测试（未确认）"],
    evidence: ["数据访问代码", "已有测试目录"],
    confidence: "medium" as const,
  },
  {
    key: "boundary",
    name: "异常与边界",
    itemNos: ["03"],
    modules: ["接口边界"],
    entryPoints: ["输入校验与错误返回"],
    testCommands: ["边界测试（未确认）"],
    evidence: ["路由与校验代码"],
    confidence: "medium" as const,
  },
];

const ITEM_TEXTS: Record<string, { text: string; module: string; feature: string }> = {
  "01": { text: "项目可以启动并完成一次完整操作", module: "主流程", feature: "启动与主流程" },
  "02": { text: "关键数据写入后可以在列表中查询", module: "数据", feature: "数据写入与查询" },
  "03": { text: "异常输入被拒绝并给出可读提示", module: "接口边界", feature: "异常与边界" },
  "04": { text: "重复执行同一操作不产生重复数据", module: "数据", feature: "数据写入与查询" },
};

function moduleId(projectId: string, key: string): string {
  return `mod_${projectId.replace(/[^a-zA-Z0-9]/g, "").slice(-8)}_${key}`;
}

function itemId(projectId: string, no: string): string {
  return `ai_${projectId.replace(/[^a-zA-Z0-9]/g, "").slice(-8)}_${no}`;
}

export function buildProjectAnalysis(
  project: Pick<Project, "id" | "name" | "root">,
): ProjectAnalysis {
  const modules: Module[] = MODULE_SEEDS.map((seed) => ({
    id: moduleId(project.id, seed.key),
    name: seed.name,
    responsibility: seed.responsibility,
    owner: seed.owner,
    status: seed.status,
    dependencies: [],
  }));

  const catalog: AcceptanceItem[] = FEATURE_SEEDS.flatMap((feature) =>
    feature.itemNos.map((no) => {
      const info = ITEM_TEXTS[no] as { text: string; module: string; feature: string };
      const module = modules.find((entry) => entry.name === info.module);
      return {
        id: itemId(project.id, no),
        no,
        text: info.text,
        observable: true,
        status: "active" as const,
        source: `本地演示分析 · ${feature.name}`,
        moduleId: module?.id ?? null,
        moduleName: info.module,
        featureName: info.feature,
      };
    }),
  );

  catalog.push({
    id: itemId(project.id, "05"),
    no: "05",
    text: "系统要稳定",
    observable: false,
    status: "pending",
    source: "本地演示分析 · 不可观察",
    moduleId: null,
    moduleName: "—",
    featureName: "待补充",
  });

  const features: Feature[] = FEATURE_SEEDS.map((seed) => ({
    id: `feat_${project.id.slice(-8)}_${seed.key}`,
    name: seed.name,
    modules: seed.modules,
    entryPoints: seed.entryPoints,
    testCommands: seed.testCommands,
    testableItems: seed.itemNos.map((no) => ITEM_TEXTS[no]?.text ?? "").filter(Boolean),
    evidence: seed.evidence,
    confidence: seed.confidence,
  }));

  const candidates: Candidate[] = [
    {
      id: `cand_${project.id.slice(-8)}_startup`,
      title: `验证 ${project.name} 可以启动并跑通主流程`,
      reason: "本地演示数据：从项目名、目录结构与启动入口推断",
      source: "来自目录",
      intent: "business",
      featureNames: ["启动与主流程"],
      itemCount: 1,
      estimatedScope: "启动与主流程 · 约 1 项",
      unknown: "启动脚本依赖的环境变量尚未确认",
    },
    {
      id: `cand_${project.id.slice(-8)}_data`,
      title: "验证关键数据写入后可以查询，并确认重复执行不产生重复数据",
      reason: "本地演示数据：按数据写入与查询路径整理",
      source: "来自模块",
      intent: "business",
      featureNames: ["数据写入与查询"],
      itemCount: 2,
      estimatedScope: "数据写入与查询 · 约 2 项",
      unknown: "重复执行的判定字段尚未确认",
    },
    {
      id: `cand_${project.id.slice(-8)}_boundary`,
      title: "验证异常输入被拒绝并给出可读提示",
      reason: "本地演示数据：按接口边界与错误处理路径整理",
      source: "来自模块",
      intent: "business",
      featureNames: ["异常与边界"],
      itemCount: 1,
      estimatedScope: "异常与边界 · 约 1 项",
      unknown: "错误码与提示文案的约定未在本地确认",
    },
  ];

  const map: ProjectMap = {
    revision: 1,
    sourceCommit: "local",
    generatedAt: isoNow(),
    features,
    ignoredFeatures: [],
    unknowns: [
      {
        id: newId("unk"),
        area: "外部依赖与运行环境",
        reason: "当前在浏览器内分析，未读取依赖清单、环境变量与服务配置",
      },
      {
        id: newId("unk"),
        area: "系统要稳定",
        reason: "不可观察，需要补充可验证的结果与判定方式",
      },
    ],
    stale: false,
    staleReason: "",
  };

  return { modules, map, catalog, candidates };
}

export interface TemplateDefinition {
  id: string;
  name: string;
  version: string;
  items: Array<{ id: string; layer: "L1" | "L2" | "L3"; objective: string }>;
}

export const BUILTIN_TEMPLATES: TemplateDefinition[] = [
  {
    id: "python-service",
    name: "Python 服务",
    version: "1.0.0",
    items: [
      { id: "startup", layer: "L1", objective: "构建、加载与最小启动" },
      { id: "health", layer: "L2", objective: "健康检查与关键入口" },
      { id: "state", layer: "L3", objective: "状态变化核验" },
    ],
  },
  {
    id: "python-library",
    name: "Python 库/纯函数",
    version: "1.0.0",
    items: [
      { id: "syntax", layer: "L1", objective: "语法与加载检查" },
      { id: "normal", layer: "L2", objective: "正常输入独立预期比对" },
      { id: "edge", layer: "L2", objective: "边界输入独立预期比对" },
    ],
  },
  {
    id: "http-workflow",
    name: "API 流程",
    version: "1.0.0",
    items: [
      { id: "chain", layer: "L2", objective: "同一业务对象请求串联与变量提取" },
      { id: "assertions", layer: "L2", objective: "字段与业务断言" },
      { id: "state", layer: "L3", objective: "持久化与状态核验" },
    ],
  },
  {
    id: "manual-web-workflow",
    name: "人工页面验收",
    version: "1.0.0",
    items: [
      { id: "navigation", layer: "L3", objective: "按钮、分页、跳转与滚动" },
      { id: "form", layer: "L3", objective: "表单正常、边界与异常提示" },
      { id: "visual", layer: "L3", objective: "页面状态与可读性" },
    ],
  },
  {
    id: "ticket-workflow",
    name: "工单流程",
    version: "1.0.0",
    items: [
      { id: "create", layer: "L2", objective: "工单创建与字段校验" },
      { id: "assign", layer: "L2", objective: "流转与指派" },
      { id: "close", layer: "L3", objective: "关闭条件与通知" },
    ],
  },
  {
    id: "agent-workflow",
    name: "智能体业务评价",
    version: "1.0.0",
    items: [
      { id: "input", layer: "L2", objective: "实际输入与发布评价规则" },
      { id: "calls", layer: "L2", objective: "真实工具调用与逐次结果" },
      { id: "business", layer: "L3", objective: "业务结果独立比对" },
    ],
  },
];

function dispositionFor(
  project: Project,
  templateId: string,
  layer: "L1" | "L2" | "L3",
  itemIdValue: string,
): { disposition: TemplateDisposition; reason: string } {
  if (
    layer === "L3" &&
    templateId === "manual-web-workflow" &&
    !/web|页面|前端|ui/i.test(project.name)
  ) {
    return {
      disposition: "not_applicable",
      reason: "未识别到可操作页面；如实际存在页面，请改选模板或补充页面入口。",
    };
  }
  if (itemIdValue === "visual" && !/web|页面|前端|ui/i.test(project.name)) {
    return { disposition: "unknown", reason: "没有页面材料，无法判断是否适用。" };
  }
  return { disposition: "applicable", reason: "按模板条目纳入本次检查范围。" };
}

export function applyTemplate(project: Project, templateId: string): TemplateResult {
  const definition = BUILTIN_TEMPLATES.find((template) => template.id === templateId);
  if (!definition) {
    throw new Error(`模板不存在：${templateId}`);
  }
  const items: TemplateItemResult[] = definition.items.map((item) => {
    const { disposition, reason } = dispositionFor(project, definition.id, item.layer, item.id);
    return {
      id: item.id,
      layer: item.layer,
      objective: item.objective,
      disposition,
      reason,
    };
  });
  const summary: Record<TemplateDisposition, number> = {
    applicable: 0,
    not_applicable: 0,
    unsupported: 0,
    unknown: 0,
  };
  items.forEach((item) => {
    summary[item.disposition] += 1;
  });
  return {
    templateId: definition.id,
    name: definition.name,
    version: definition.version,
    appliedAt: isoNow(),
    items,
    summary,
  };
}

export function buildMockSummary(run: Run): MockSummary {
  const total = run.cases.length;
  return {
    real: 0,
    mock: 0,
    mixed: 0,
    unknown: total,
    realChain: false,
    note: "本次是本地模拟运行，未连接真实依赖，无法判断 Mock 真实性；本次不产生“真实链路通过”。",
    refs: [],
  };
}

export function buildFailureAnalysis(run: Run): FailureAnalysis {
  const failed = run.cases.filter((testCase) => testCase.result === "failed");
  if (failed.length === 0) {
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
  const first = failed[0] as Run["cases"][number];
  return {
    status: "generated",
    reason: "",
    observed: `${failed.length} 条用例失败；首条为「${first.title}」，实际结果：${first.actual || "未记录"}。`,
    evidenceRefs: first.evidence.map((evidence) => evidence.name),
    candidateCauses: [
      { cause: "断言结果与冻结预期不一致，业务实现或预期需要复核。", confidence: "medium" },
      { cause: "测试数据或依赖状态与本次范围不一致。", confidence: "low" },
    ],
    unverified: [
      "本地模拟运行未读取真实日志与环境，候选原因未验证。",
      "依赖真实性无法判断，不能视为真实链路通过。",
    ],
    nextSteps: ["查看失败用例的预期、实际与证据。", "修复后重跑未通过项，再决定是否关闭问题。"],
  };
}
