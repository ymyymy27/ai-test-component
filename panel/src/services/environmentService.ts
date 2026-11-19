import type { EnvironmentCheck } from "@/domain/types";

export function buildEnvironmentChecks(): EnvironmentCheck[] {
  let storageOk = true;
  let storageDetail = "浏览器本地存储可用；数据只留在这台机器。";
  try {
    const probe = "__aitest_probe__";
    localStorage.setItem(probe, "1");
    localStorage.removeItem(probe);
  } catch {
    storageOk = false;
    storageDetail = "浏览器本地存储不可用；当前修改只在本次会话保留。";
  }
  return [
    {
      key: "panel",
      name: "面板",
      value: "已加载",
      ok: true,
      detail: "Vue 3 + Vite；当前是独立面板运行环境。",
      repair: "",
      risk: "none",
    },
    {
      key: "storage",
      name: "本地存储",
      value: storageOk ? "可用" : "不可用",
      ok: storageOk,
      detail: storageDetail,
      repair: storageOk ? "" : "允许浏览器本地存储，或接入宿主工作空间。",
      risk: storageOk ? "none" : "high",
    },
    {
      key: "cli",
      name: "测试运行时",
      value: "未接入",
      ok: false,
      detail: "当前没有连接 CLI；运行和快速检查是本地模拟。",
      repair: "在宿主面板中打开，或配置 AITEST 运行时。",
      risk: "high",
    },
    {
      key: "model",
      name: "模型连接",
      value: "未接入",
      ok: false,
      detail: "目标候选和失败分析使用本地规则，不调用模型。",
      repair: "接入宿主后配置模型密钥。",
      risk: "medium",
    },
    {
      key: "github",
      name: "GitHub",
      value: "未连接",
      ok: false,
      detail: "只读提交与检查不可用；本地测试与报告不受影响。",
      repair: "接入宿主后执行 gh auth login。",
      risk: "low",
    },
    {
      key: "workspace",
      name: "工作空间",
      value: "浏览器本地",
      ok: storageOk,
      detail: "不写入被测仓库；证据与报告只保留元数据。",
      repair: "",
      risk: "none",
    },
  ];
}

export function missingEnvironmentCount(checks: EnvironmentCheck[]): number {
  return checks.filter((check) => !check.ok).length;
}
