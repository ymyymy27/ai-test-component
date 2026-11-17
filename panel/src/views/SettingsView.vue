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
  backToSession,
  flash,
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
      hint: facts.workspace.ok ? "" : "目录不可写，需要换位置或修正权限",
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
const activeGroup = ref<ConfigGroup["key"]>("services");
const advancedOpen = ref(false);

const activeGroupInfo = computed(
  () => CONFIG_GROUPS.find((group) => group.key === activeGroup.value) ?? CONFIG_GROUPS[0],
);

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
      value:
        model["timeout_seconds"] === undefined ? "未设置" : `${text(model["timeout_seconds"])} 秒`,
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

const githubState = computed(() => doctor.value?.github ?? { ok: false, path: null });

function sectionText(value: Record<string, unknown> | undefined): string {
  return JSON.stringify(value ?? {}, null, 2);
}

function fillDrafts(source: ProjectConfig): void {
  for (const group of CONFIG_GROUPS) {
    drafts[group.key] = sectionText(source[group.key]);
  }
}

function isDirty(key: ConfigGroup["key"]): boolean {
  const loaded = sectionText(config.value?.[key]);
  return drafts[key].trim() !== loaded.trim();
}

function selectGroup(key: ConfigGroup["key"]): void {
  activeGroup.value = key;
  validationError.value = "";
  saveError.value = null;
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
    activeGroup.value = group.key;
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
      <button type="button" class="back" @click="backToSession">
        <Icon name="arrow-left" :size="15" />
        返回测试单
      </button>
    </div>

    <header class="head-copy">
      <h1>设置</h1>
      <p class="muted">这里只显示当前环境事实与可执行的修复动作。密钥不会显示或保存到项目文件。</p>
    </header>

    <ErrorNotice
      v-if="session.bootError && !doctor"
      :error="session.bootError"
      retry-label="重新自检"
      :busy="session.doctorLoading"
      @retry="refreshDoctor"
    />

    <section class="card" data-testid="env-check">
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
          <span v-if="row.hint" class="fact-hint">{{ row.hint }}</span>
          <StatusDot :tone="row.ok ? 'ok' : 'warn'" :label="row.ok ? '正常' : '缺项'" />
        </li>
      </ul>
      <p v-else class="hint">还没有自检结果。</p>

      <div v-if="risks.length" class="sub-block">
        <h3>需要你处理</h3>
        <ul class="repairs">
          <li v-for="(item, index) in risks" :key="index" class="repair">
            <Badge :tone="riskTone(item.risk)">{{ riskLabel(item.risk) }}</Badge>
            <span class="repair-text mono">{{ item.command }}</span>
            <span v-if="item.auto" class="hint">可随执行确认处理</span>
          </li>
        </ul>
        <p class="hint">高风险项只给命令，不自动执行。</p>
      </div>

      <div v-if="session.doctor?.repairs_done?.length" class="sub-block">
        <h3>最近一次自动修复</h3>
        <ul class="done-list">
          <li v-for="item in session.doctor.repairs_done" :key="item" class="mono">{{ item }}</li>
        </ul>
      </div>
    </section>

    <section class="card" data-testid="model-status">
      <div class="section-title">
        <h2>模型</h2>
        <Badge :tone="modelKeyState.ok ? 'passed' : 'incomplete'">模型密钥：{{ modelKeyState.text }}</Badge>
      </div>
      <dl class="kv">
        <div v-for="row in modelRows" :key="row.label" class="kv-row">
          <dt>{{ row.label }}</dt>
          <dd class="mono">{{ row.value }}</dd>
        </div>
      </dl>
      <p class="hint">
        密钥只从用户配置或环境变量读取，不写进项目配置，也不在面板显示。模型不可用时，目标候选、验收标准提取、计划生成与失败分析会明确标为“未生成”；本地测试、证据与报告仍然可用。
      </p>
    </section>

    <section class="card" data-testid="workspace-status">
      <div class="section-title">
        <h2>工作空间</h2>
        <StatusDot
          :tone="doctor?.workspace.ok ? 'ok' : 'warn'"
          :label="doctor?.workspace.ok ? '可写' : '不可写'"
        />
      </div>
      <p v-if="doctor" class="path mono">{{ doctor.workspace.path }}</p>
      <p v-else class="hint">还没有自检结果。</p>
      <p class="hint">
        用户数据工作空间保存项目记录、证据与报告，不是被测源码目录。要更换位置，在编辑器设置中修改“AI 辅助测试 → 工作空间路径”，然后重新打开面板。
      </p>
    </section>

    <section class="card" data-testid="github-status">
      <div class="section-title">
        <h2>GitHub</h2>
        <StatusDot
          :tone="githubState.ok ? 'ok' : 'warn'"
          :label="githubState.ok ? '可用' : '不可用'"
        />
      </div>
      <p v-if="githubState.path" class="path mono">{{ githubState.path }}</p>
      <p class="hint">
        GitHub 只读读取一个仓库的提交、PR、检查与部署版本，复用 gh 的登录态；不可用时不影响本地测试。链路采集读取项目配置里的日志路径与只读查询命令，缺请求标识或日志时记录缺口，不补造。
      </p>
    </section>

    <section class="card advanced" :class="{ open: advancedOpen }" data-testid="advanced-config">
      <button
        type="button"
        class="advanced-head"
        :aria-expanded="advancedOpen"
        aria-controls="advanced-config-body"
        @click="advancedOpen = !advancedOpen"
      >
        <span class="advanced-title">高级配置</span>
        <span class="advanced-note">服务 · 链路 · 运行器 · 证据 · L1 · 数据清理</span>
      </button>

      <div v-if="advancedOpen" id="advanced-config-body" class="advanced-body">
        <p v-if="!session.project" class="hint">
          还没有选择项目。回到测试单选择项目后，这里会显示可编辑的服务、链路与运行器配置。
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

          <div class="tabs" role="tablist" aria-label="配置分组">
            <button
              v-for="group in CONFIG_GROUPS"
              :key="group.key"
              type="button"
              role="tab"
              class="tab"
              :class="{ on: activeGroup === group.key }"
              :aria-selected="activeGroup === group.key"
              @click="selectGroup(group.key)"
            >
              {{ group.title }}
              <span v-if="isDirty(group.key)" class="dirty-dot" aria-label="已修改"></span>
            </button>
          </div>

          <div class="group-body" role="tabpanel" :aria-label="activeGroupInfo.title">
            <p class="hint">{{ activeGroupInfo.hint }}</p>
            <label class="field">
              <span class="sr-only">{{ activeGroupInfo.title }} JSON</span>
              <textarea
                :id="`config-${activeGroup}`"
                v-model="drafts[activeGroup]"
                class="input textarea mono"
                rows="8"
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

          <div class="row advanced-actions">
            <AppButton variant="primary" :loading="saving" @click="saveConfig">
              保存项目配置
            </AppButton>
            <span class="hint">
              保存会合并全部已编辑分组；哪一组写错就跳到那一组并说明下一步。
            </span>
          </div>
        </template>
      </div>
    </section>

    <p class="footnote">
      设置里的模型与链路能力不改变已有的测试判定；任何未验证项都会在测试单的结果里标出。
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
  grid-template-columns: 132px 1fr auto;
  grid-template-areas:
    "label value status"
    "label hint status";
  gap: 2px var(--space-3);
  padding: 9px 0;
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

.sub-block {
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--line);
}

.sub-block h3 {
  font-size: var(--fs-12);
  color: var(--text-3);
  font-weight: 500;
  margin-bottom: 6px;
}

.repairs,
.done-list {
  list-style: none;
  margin: 0 0 var(--space-2);
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.repair {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.repair-text {
  flex: 1 1 auto;
  min-width: 0;
  color: var(--text-2);
  overflow-wrap: break-word;
}

.done-list {
  color: var(--text-2);
}

.kv {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0 0 var(--space-2);
  padding: 8px 10px;
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
}

.kv-row {
  display: grid;
  grid-template-columns: 96px 1fr;
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

.path {
  margin-bottom: var(--space-2);
  padding: 7px 10px;
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

/* 高级配置：折叠 + 分段，一次只显示一个 JSON 分组 */

.advanced {
  padding: 0;
}

.advanced-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  width: 100%;
  padding: var(--space-4);
  background: none;
  border: 0;
  text-align: left;
  cursor: pointer;
  border-radius: var(--r-card);
}

.advanced-head:hover {
  background: var(--surface-raised);
}

.advanced-title {
  font-size: var(--fs-14);
  font-weight: 600;
  letter-spacing: 0.01em;
}

.advanced-title::before {
  content: "";
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 8px;
  border-right: 1px solid var(--text-3);
  border-bottom: 1px solid var(--text-3);
  transform: rotate(-45deg) translateY(-1px);
}

.advanced.open .advanced-title::before {
  transform: rotate(45deg) translateY(-1px);
}

.advanced-note {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.advanced-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: 0 var(--space-4) var(--space-4);
  border-top: 1px solid var(--line);
  padding-top: var(--space-3);
}

.tabs {
  display: flex;
  gap: 2px;
  flex-wrap: wrap;
  border-bottom: 1px solid var(--line);
}

.tab {
  position: relative;
  padding: 7px 10px;
  background: none;
  border: 0;
  border-bottom: 2px solid transparent;
  color: var(--text-3);
  font-size: var(--fs-12);
  margin-bottom: -1px;
}

.tab:hover {
  color: var(--text-2);
}

.tab.on {
  color: var(--text);
  border-bottom-color: var(--accent-line);
}

.dirty-dot {
  display: inline-block;
  width: 5px;
  height: 5px;
  margin-left: 5px;
  border-radius: 50%;
  background: var(--incomplete);
  vertical-align: middle;
}

.group-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
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

.advanced-actions {
  flex-wrap: wrap;
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
