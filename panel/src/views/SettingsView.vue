<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { api } from "@/api/commands";
import { describeError, type FriendlyError } from "@/api/errors";
import type { ConfigSection, ProjectConfig } from "@/api/types";
import AppButton from "@/components/AppButton.vue";
import Badge from "@/components/Badge.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Icon from "@/components/Icon.vue";
import StatusDot from "@/components/StatusDot.vue";
import {
  flash,
  go,
  refreshDoctor,
  repairEnvironment,
  session,
} from "@/state/session";

const doctor = computed(() => session.doctor);

const rows = computed(() => {
  const facts = doctor.value;
  if (!facts) {
    return [];
  }
  return [
    {
      key: "python",
      label: "Python 运行环境",
      ok: facts.python.ok,
      value: facts.python.version ? `版本 ${facts.python.version}` : "未找到",
      hint: facts.python.ok ? "" : "需要 Python 3.11 及以上版本",
    },
    {
      key: "package",
      label: "测试服务包",
      ok: facts.package.ok,
      value: facts.package.version ? `版本 ${facts.package.version}` : "未安装",
      hint: facts.package.ok ? "" : "本地服务包缺失，面板无法调用测试能力",
    },
    {
      key: "workspace",
      label: "用户数据工作空间",
      ok: facts.workspace.ok,
      value: facts.workspace.path,
      hint: facts.workspace.ok ? "记录、证据与报告都保存在这里" : "目录不可写，需要换位置或修正权限",
    },
    {
      key: "model",
      label: "模型",
      ok: facts.model.ok,
      value: facts.model.ok ? "已配置" : "未配置",
      hint: facts.model.ok
        ? `来源：${facts.model.source}`
        : "AI 目标、验收标准与失败分析需要模型；本地执行与导出仍可用",
    },
    {
      key: "github",
      label: "GitHub",
      ok: facts.github.ok,
      value: facts.github.ok ? "可用" : "不可用",
      hint: facts.github.ok ? "只读读取提交与检查" : "安装并登录 gh 后可用；不影响本地测试",
    },
    {
      key: "proxy",
      label: "网络代理",
      ok: facts.proxy.ok,
      value: facts.proxy.ok ? "正常" : "异常",
      hint: "",
    },
  ];
});

const risks = computed(() => doctor.value?.repairs ?? []);

function riskLabel(risk: string): string {
  if (risk === "low") return "低风险";
  if (risk === "medium") return "中风险";
  return "高风险";
}

function riskTone(risk: string): "passed" | "incomplete" | "failed" {
  if (risk === "low") return "passed";
  if (risk === "medium") return "incomplete";
  return "failed";
}

interface ConfigGroup {
  key: Exclude<ConfigSection, "model">;
  title: string;
  hint: string;
}

const CONFIG_GROUPS: ConfigGroup[] = [
  {
    key: "services",
    title: "服务",
    hint: "单服务：{ start, health, stop, ... }；或 服务名 → 配置，取第一个。面板只保存这些非敏感字段。",
  },
  {
    key: "link",
    title: "链路",
    hint: "日志路径、格式、字段映射与只读查询；缺请求标识时会记录缺口，不补造。",
  },
  {
    key: "runners",
    title: "运行器",
    hint: "命令、接口或脚本运行器的默认参数；不会覆盖单条用例的验证方式。",
  },
  {
    key: "evidence",
    title: "证据预算",
    hint: "媒体总预算、单文件上限与视频开关；超限时按策略压缩或截断。",
  },
  {
    key: "l1",
    title: "源码可运行（L1）",
    hint: "项目级 build 命令、入口模块与超时；在用例执行前运行。",
  },
  {
    key: "data",
    title: "数据清理",
    hint: "dry-run 与清理命令；真实清理仍需要显式确认。",
  },
];

const config = ref<ProjectConfig | null>(null);
const drafts = reactive<Record<Exclude<ConfigSection, "model">, string>>({
  services: "{}",
  link: "{}",
  runners: "{}",
  evidence: "{}",
  l1: "{}",
  data: "{}",
});
const configLoading = ref(false);
const saving = ref(false);
const configLoadError = ref<FriendlyError | null>(null);
const validationError = ref("");
const saveError = ref<FriendlyError | null>(null);
const savedNote = ref("");

const modelSection = computed(() => config.value?.model ?? {});
const modelRows = computed(() => {
  const model = modelSection.value;
  const text = (value: unknown): string =>
    typeof value === "string" || typeof value === "number" ? String(value) : "";
  return [
    { label: "服务地址", value: text(model["base_url"]) || "未设置（使用用户配置）" },
    { label: "模型名称", value: text(model["name"]) || "未设置（使用默认模型）" },
    {
      label: "超时",
      value: model["timeout_seconds"] === undefined ? "未设置" : `${text(model["timeout_seconds"])} 秒`,
    },
  ];
});
const modelKeyState = computed(() => {
  const facts = doctor.value?.model;
  if (!facts) {
    return { ok: false, text: "还没有自检结果，密钥状态未知" };
  }
  return facts.ok
    ? { ok: true, text: `已配置（来源：${facts.source}）` }
    : { ok: false, text: "未配置；AI 能力不可用，本地测试仍可用" };
});

function sectionText(value: Record<string, unknown> | undefined): string {
  return JSON.stringify(value ?? {}, null, 2);
}

function fillDrafts(source: ProjectConfig): void {
  for (const group of CONFIG_GROUPS) {
    drafts[group.key] = sectionText(source[group.key]);
  }
}

async function loadConfig(): Promise<void> {
  if (!session.project) {
    config.value = null;
    return;
  }
  configLoading.value = true;
  configLoadError.value = null;
  try {
    const data = await api.getProjectConfig(session.project.project_id);
    config.value = data.config ?? {};
    fillDrafts(config.value);
  } catch (error) {
    configLoadError.value = describeError(error);
  } finally {
    configLoading.value = false;
  }
}

function parseSection(group: ConfigGroup): Record<string, unknown> | null {
  const text = drafts[group.key].trim();
  if (!text) {
    return {};
  }
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("必须是 JSON 对象");
    }
    return value as Record<string, unknown>;
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    validationError.value = `“${group.title}”的 JSON 无法解析：${detail}。下一步：检查逗号、引号和括号后重新保存。`;
    return null;
  }
}

async function saveConfig(): Promise<void> {
  if (!session.project) {
    return;
  }
  validationError.value = "";
  saveError.value = null;
  savedNote.value = "";
  const patch: ProjectConfig = {};
  for (const group of CONFIG_GROUPS) {
    const parsed = parseSection(group);
    if (parsed === null) {
      return;
    }
    patch[group.key] = parsed;
  }
  saving.value = true;
  try {
    const data = await api.saveProjectConfig(session.project.project_id, patch);
    config.value = data.config ?? {};
    fillDrafts(config.value);
    savedNote.value = "项目配置已保存。";
    flash("项目配置已保存");
  } catch (error) {
    saveError.value = describeError(error);
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  void loadConfig();
});
</script>

<template>
  <div class="stack" data-testid="settings-view">
    <div class="head">
      <button type="button" class="back" @click="go('project')">
        <Icon name="arrow-left" :size="15" />
        返回
      </button>
    </div>

    <header class="head-copy">
      <h1>设置</h1>
      <p class="muted">
        这里只显示当前环境事实与可执行的修复动作。密钥不会显示或保存到项目文件。
      </p>
    </header>

    <ErrorNotice
      v-if="session.bootError && !doctor"
      :error="session.bootError"
      retry-label="重新自检"
      :busy="session.doctorLoading"
      @retry="refreshDoctor"
    />

    <section class="card">
      <div class="section-title">
        <h2>环境自检</h2>
        <div class="row">
          <AppButton size="sm" variant="ghost" :loading="session.doctorLoading" @click="refreshDoctor">
            <Icon name="refresh" :size="14" />
            重新自检
          </AppButton>
          <AppButton
            size="sm"
            variant="secondary"
            :loading="session.doctorLoading"
            @click="repairEnvironment"
          >
            修复可自动处理项
          </AppButton>
        </div>
      </div>

      <p class="hint repair-note">
        自动修复只创建缺失的数据目录并做一次写入探测，不安装软件、不改项目文件。
      </p>

      <ul v-if="rows.length" class="facts">
        <li v-for="row in rows" :key="row.key" class="fact">
          <span class="fact-label">{{ row.label }}</span>
          <span class="fact-value" :class="{ bad: !row.ok }">{{ row.value }}</span>
          <span class="fact-hint">{{ row.hint }}</span>
          <StatusDot :tone="row.ok ? 'ok' : 'warn'" :label="row.ok ? '正常' : '缺项'" />
        </li>
      </ul>
      <p v-else class="hint">还没有自检结果。</p>

      <div v-if="session.doctor?.repairs_done?.length" class="done">
        <h3>最近一次自动修复</h3>
        <ul>
          <li v-for="item in session.doctor.repairs_done" :key="item" class="mono">{{ item }}</li>
        </ul>
      </div>
    </section>

    <section v-if="risks.length" class="card">
      <div class="section-title">
        <h2>需要你处理</h2>
        <span class="section-note">高风险项只给命令，不自动执行</span>
      </div>
      <ul class="repairs">
        <li v-for="(item, index) in risks" :key="index" class="repair">
          <Badge :tone="riskTone(item.risk)">{{ riskLabel(item.risk) }}</Badge>
          <span class="repair-text mono">{{ item.command }}</span>
          <span v-if="item.auto" class="hint">可随执行确认处理</span>
        </li>
      </ul>
    </section>

    <section class="card">
      <div class="section-title">
        <h2>模型</h2>
      </div>
      <p class="note">
        模型密钥通过用户配置或环境变量提供，不写进项目配置，也不在面板显示。模型不可用时，目标候选、验收标准提取、计划生成与失败分析会明确标为“未生成”；已有的本地测试、证据与报告仍然可用。
      </p>
    </section>

    <section class="card" data-testid="project-config">
      <div class="section-title">
        <h2>项目配置</h2>
        <span class="section-note">读取 project.config.json；保存只合并下列分组</span>
      </div>

      <p v-if="!session.project" class="hint">
        还没有选择项目。回到项目页选择项目后，这里会显示可编辑的服务、链路与运行器配置。
      </p>

      <template v-else>
        <div v-if="configLoading" class="hint">正在读取项目配置…</div>
        <ErrorNotice
          v-if="configLoadError"
          :error="configLoadError"
          retry-label="重新读取"
          :busy="configLoading"
          @retry="loadConfig"
        />

        <div class="config-group">
          <h3>模型（只读）</h3>
          <p class="hint key-state">
            模型密钥：
            <Badge :tone="modelKeyState.ok ? 'passed' : 'incomplete'">{{ modelKeyState.text }}</Badge>
          </p>
          <p class="hint">
            密钥只从用户配置或环境变量读取；项目配置不保存密钥，这里也不提供编辑入口。
          </p>
          <dl class="kv">
            <div v-for="row in modelRows" :key="row.label" class="kv-row">
              <dt>{{ row.label }}</dt>
              <dd class="mono">{{ row.value }}</dd>
            </div>
          </dl>
        </div>

        <div v-for="group in CONFIG_GROUPS" :key="group.key" class="config-group">
          <h3>{{ group.title }}</h3>
          <p class="hint">{{ group.hint }}</p>
          <label class="field">
            <span class="sr-only">{{ group.title }} JSON</span>
            <textarea
              :id="`config-${group.key}`"
              v-model="drafts[group.key]"
              class="input textarea mono"
              rows="6"
              spellcheck="false"
            ></textarea>
          </label>
        </div>

        <ErrorNotice
          v-if="saveError"
          :error="saveError"
          :show-retry="false"
          @retry="() => {}"
        />
        <p v-if="validationError" class="action-error" role="alert">{{ validationError }}</p>
        <p v-if="savedNote" class="saved-note" role="status">{{ savedNote }}</p>

        <div class="row">
          <AppButton variant="primary" :loading="saving" @click="saveConfig">
            保存项目配置
          </AppButton>
          <span class="hint">保存失败时会指出哪个分组有问题以及下一步怎么做；密钥字段会被拒绝。</span>
        </div>
      </template>
    </section>

    <section class="card">
      <div class="section-title">
        <h2>GitHub 与链路</h2>
      </div>
      <p class="note">
        GitHub 只读读取一个仓库的提交、PR、检查与部署版本，复用 gh 的登录态。链路采集读取项目配置里的日志路径与只读查询命令；缺请求标识或日志时记录缺口，不补造。这两项都只影响变更关联与定位，不改变业务结论。
      </p>
      <p class="hint">
        当前自检不返回链路配置状态；服务与链路的详细配置保存在项目配置中，面板不提供修改入口。
      </p>
    </section>

    <section class="card">
      <div class="section-title">
        <h2>工作空间</h2>
      </div>
      <p class="note">
        用户数据工作空间保存项目记录、证据与报告，不是被测源码目录。要更换位置，在编辑器设置中修改“AI 辅助测试 → 工作空间路径”，然后重新打开面板。
      </p>
      <p v-if="doctor" class="path mono">{{ doctor.workspace.path }}</p>
    </section>

    <p class="footnote">
      设置里的模型与链路能力不改变已有的测试判定；任何未验证项都会在结果页标出。
    </p>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 4px 2px;
  background: none;
  border: 0;
  color: var(--text-2);
  font-size: var(--fs-12);
  border-radius: var(--r-control);
}

.back:hover {
  color: var(--text);
  background: var(--surface-raised);
}

.head-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.head-copy p {
  font-size: var(--fs-12);
  max-width: 64ch;
  line-height: 1.6;
}

.repair-note {
  margin-bottom: var(--space-3);
}

.facts {
  list-style: none;
  margin: 0;
  padding: 0;
}

.fact {
  display: grid;
  grid-template-columns: 150px 1fr auto;
  grid-template-areas:
    "label value status"
    "label hint status";
  gap: 2px var(--space-3);
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
  align-items: start;
}

.fact:last-child {
  border-bottom: 0;
}

.fact-label {
  grid-area: label;
  font-size: var(--fs-12);
  color: var(--text-3);
  padding-top: 1px;
}

.fact-value {
  grid-area: value;
  font-size: var(--fs-13);
  color: var(--text);
  word-break: break-word;
}

.fact-value.bad {
  color: var(--incomplete);
}

.fact-hint {
  grid-area: hint;
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.5;
}

.fact :deep(.status) {
  grid-area: status;
}

.done {
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--line);
}

.done h3 {
  font-size: var(--fs-12);
  color: var(--text-3);
  font-weight: 500;
  margin-bottom: 4px;
}

.done ul {
  list-style: none;
  margin: 0;
  padding: 0;
  color: var(--text-2);
}

.repairs {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.repair {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding: var(--space-3);
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
}

.repair-text {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--text-2);
  word-break: break-all;
}

.note {
  font-size: var(--fs-13);
  color: var(--text-2);
  line-height: 1.65;
  max-width: 68ch;
}

.path {
  margin-top: var(--space-2);
  padding: 8px 10px;
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  color: var(--text-2);
  word-break: break-all;
}

.footnote {
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.6;
}

.config-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: var(--space-3) 0;
  border-top: 1px solid var(--line);
}

.config-group h3 {
  font-size: var(--fs-13);
}

.key-state {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.kv {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 4px 0 0;
  padding: 8px 10px;
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
}

.kv-row {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: var(--space-3);
  font-size: var(--fs-12);
}

.kv-row dt {
  color: var(--text-3);
}

.kv-row dd {
  margin: 0;
  color: var(--text-2);
  word-break: break-all;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.input {
  width: 100%;
  padding: 7px 10px;
  background: var(--surface-sunken);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-12);
}

.input:focus {
  border-color: var(--accent-line);
  outline: none;
  box-shadow: 0 0 0 2px rgba(130, 175, 205, 0.16);
}

.textarea {
  resize: vertical;
  line-height: 1.55;
}

.action-error {
  padding: 8px 10px;
  border: 1px solid rgba(207, 122, 122, 0.35);
  background: var(--failed-fill);
  border-radius: var(--r-control);
  color: var(--text-2);
  font-size: var(--fs-12);
  line-height: 1.6;
}

.saved-note {
  padding: 8px 10px;
  border: 1px solid rgba(134, 179, 148, 0.3);
  background: var(--passed-fill);
  border-radius: var(--r-control);
  font-size: var(--fs-12);
}

@media (max-width: 520px) {
  .fact {
    grid-template-columns: 1fr auto;
    grid-template-areas:
      "label status"
      "value value"
      "hint hint";
    row-gap: 3px;
  }
}
</style>
