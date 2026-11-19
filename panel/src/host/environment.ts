import type { EnvironmentCheck } from "@/domain/types";
import type { DoctorFacts } from "./dto";

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function repairFor(facts: DoctorFacts, key: string): string {
  const repairs = Array.isArray(facts.repairs) ? facts.repairs : [];
  if (repairs.length === 0) {
    return "";
  }
  const relevant = repairs.filter((repair) => {
    if (key === "python") return /python/i.test(repair.command);
    if (key === "github") return /gh\s/i.test(repair.command);
    if (key === "proxy") return /proxy/i.test(repair.command);
    return true;
  });
  const list = relevant.length > 0 ? relevant : repairs;
  return list.map((repair) => `${repair.risk === "high" ? "手动" : "可修复"}：${repair.command}`).join("；");
}

function riskFor(facts: DoctorFacts): EnvironmentCheck["risk"] {
  const repairs = Array.isArray(facts.repairs) ? facts.repairs : [];
  if (repairs.some((repair) => repair.risk === "high")) {
    return "high";
  }
  if (repairs.some((repair) => repair.risk === "medium")) {
    return "medium";
  }
  if (repairs.some((repair) => repair.risk === "low")) {
    return "low";
  }
  return "none";
}

export function mapDoctorFacts(facts: DoctorFacts): EnvironmentCheck[] {
  const workspace = facts.workspace;
  const python = facts.python;
  const pkg = facts.package;
  const model = facts.model;
  const github = facts.github;
  const proxy = facts.proxy;
  const config = facts.project_config ?? { ok: true, problems: [] };
  const references = facts.references ?? { ok: true, missing: [] };
  const risk = riskFor(facts);
  const proxyConfigured = isRecord(proxy.configured)
    ? Object.keys(proxy.configured).join("、")
    : "";
  return [
    {
      key: "python",
      name: "Python",
      value: String(python.version ?? "未检测"),
      ok: python.ok === true,
      detail: python.ok ? "宿主 CLI 运行时可用。" : "缺少 Python 3.11+。",
      repair: python.ok ? "" : repairFor(facts, "python"),
      risk: python.ok ? "none" : risk,
    },
    {
      key: "package",
      name: "测试插件",
      value: String(pkg.version ?? "未检测"),
      ok: pkg.ok === true,
      detail: pkg.ok ? "aitest 包可导入。" : "aitest 包不可用。",
      repair: pkg.ok ? "" : repairFor(facts, "package"),
      risk: pkg.ok ? "none" : risk,
    },
    {
      key: "workspace",
      name: "工作空间",
      value: String(workspace.path ?? "未配置"),
      ok: workspace.ok === true,
      detail: workspace.ok ? "宿主工作空间可写。" : "工作空间不可写或未配置。",
      repair: workspace.ok ? "" : repairFor(facts, "workspace"),
      risk: workspace.ok ? "none" : risk,
    },
    {
      key: "model",
      name: "模型连接",
      value: String(model.source ?? "未配置"),
      ok: model.ok === true,
      detail: model.ok ? "模型可用；用于候选、地图、计划和分析。" : "模型不可用；AI 步骤会标未生成。",
      repair: model.ok ? "" : repairFor(facts, "model"),
      risk: model.ok ? "none" : "medium",
    },
    {
      key: "github",
      name: "GitHub",
      value: String(github.account ?? github.host ?? github.path ?? "未连接"),
      ok: github.ok === true,
      detail: github.ok ? "gh 已登录，可读取只读变更。" : "gh 缺失或未登录。",
      repair: github.ok ? "" : repairFor(facts, "github"),
      risk: github.ok ? "none" : "low",
    },
    {
      key: "proxy",
      name: "代理",
      value: proxy.ok === true ? "已配置" : "未配置 · 直连",
      ok: true,
      detail: proxyConfigured ? `已配置项：${proxyConfigured}` : "没有代理配置。",
      repair: "",
      risk: "none",
    },
    {
      key: "config",
      name: "项目配置",
      value: config.ok === true ? "通过" : "有问题",
      ok: config.ok === true,
      detail:
        Array.isArray(config.problems) && config.problems.length > 0
          ? `问题：${config.problems.map((item) => String(item)).join("；")}`
          : "项目配置读取正常。",
      repair: config.ok === true ? "" : repairFor(facts, "config"),
      risk: config.ok === true ? "none" : risk,
    },
    {
      key: "references",
      name: "引用完整性",
      value: references.ok === true ? "通过" : "有缺失",
      ok: references.ok === true,
      detail:
        Array.isArray(references.missing) && references.missing.length > 0
          ? `缺失：${references.missing.map((item) => String(item)).join("；")}`
          : "记录引用完整。",
      repair: references.ok === true ? "" : repairFor(facts, "references"),
      risk: references.ok === true ? "none" : "medium",
    },
  ];
}
