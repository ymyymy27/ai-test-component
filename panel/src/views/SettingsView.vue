<script setup lang="ts">
import { onMounted, ref } from "vue";

import { api } from "@/api/commands";
import { describeError } from "@/api/errors";
import type {
  BackupResult,
  GithubChange,
  ProjectConfig,
  Rule,
  RuleSummary,
} from "@/api/types";
import { THEMES, setThemeMode, themeMode } from "@/state/theme";
import { refreshDoctor, workspace } from "@/state/workspace";

const config = ref<ProjectConfig>({});
const configSection = ref<"services" | "l1" | "runners" | "evidence" | "link" | "model" | "data">("services");
const configText = ref("{}");
const configNote = ref("");
const configError = ref("");
const backupPath = ref("");
const backupEvidence = ref(false);
const backup = ref<BackupResult | null>(null);
const backupError = ref("");
const busy = ref(false);
const configOpen = ref(false);

const rules = ref<RuleSummary[]>([]);
const rulesError = ref("");
const rulesNote = ref("");
const ruleDetail = ref<Rule | null>(null);
const binding = ref("");
const ruleOpen = ref(false);
const ruleName = ref("");
const ruleVersion = ref("");
const ruleSteps = ref("");
const ruleAiRules = ref("");
const ruleEvidence = ref("");
const githubRepo = ref("");
const githubRef = ref("main");
const github = ref<GithubChange | null>(null);
const githubError = ref("");
const githubNote = ref("");
const githubBusy = ref(false);

const sections: { id: typeof configSection.value; label: string }[] = [
  { id: "services", label: "服务与健康检查" },
  { id: "l1", label: "项目级检查" },
  { id: "runners", label: "运行器" },
  { id: "evidence", label: "证据" },
  { id: "link", label: "调用链" },
  { id: "model", label: "模型" },
  { id: "data", label: "测试数据" },
];

function modelSource(value: string): string {
  const source = value.toLowerCase();
  if (source.includes("env")) {
    return "环境变量";
  }
  if (source.includes("user") || source.includes("config")) {
    return "用户配置";
  }
  return value ? "已配置" : "未配置";
}

function pickSection(): void {
  const value = config.value[configSection.value] ?? {};
  configText.value = JSON.stringify(value, null, 2);
}

async function loadConfig(): Promise<void> {
  if (!workspace.project) {
    return;
  }
  configError.value = "";
  try {
    const data = await api.getProjectConfig(workspace.project.project_id);
    config.value = data.config ?? {};
    pickSection();
  } catch (error) {
    const friendly = describeError(error);
    configError.value = `${friendly.title}。${friendly.next}`;
  }
}

async function saveConfig(): Promise<void> {
  if (!workspace.project) {
    return;
  }
  configNote.value = "";
  configError.value = "";
  let parsed: unknown;
  try {
    parsed = JSON.parse(configText.value || "{}");
  } catch {
    configError.value = "这段配置不是合法的对象。检查括号、引号和逗号后再保存。";
    return;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    configError.value = "这段配置必须是一个对象。";
    return;
  }
  busy.value = true;
  try {
    const data = await api.saveProjectConfig(workspace.project.project_id, {
      [configSection.value]: parsed as Record<string, unknown>,
    });
    config.value = data.config ?? {};
    pickSection();
    configNote.value = "项目配置已保存。";
  } catch (error) {
    const friendly = describeError(error);
    configError.value = `${friendly.title}。${friendly.next}`;
  } finally {
    busy.value = false;
  }
}

async function loadRules(): Promise<void> {
  if (!workspace.project) {
    rules.value = [];
    return;
  }
  rulesError.value = "";
  binding.value = workspace.project.rules_version_id ?? "";
  try {
    const data = await api.listRules(workspace.project.project_id);
    rules.value = data.rules;
    if (!binding.value && data.rules.length) {
      binding.value = data.rules[0]?.rules_version_id ?? "";
    }
  } catch (error) {
    const friendly = describeError(error);
    rulesError.value = `${friendly.title}。${friendly.next}`;
  }
}

function splitLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

async function saveRule(): Promise<void> {
  if (!workspace.project) {
    return;
  }
  rulesError.value = "";
  rulesNote.value = "";
  const name = ruleName.value.trim();
  const version = ruleVersion.value.trim();
  if (!name || !version) {
    rulesError.value = "规则名称和版本都要填写。";
    return;
  }
  try {
    const saved = await api.saveRule(workspace.project.project_id, {
      name,
      version,
      steps_md: ruleSteps.value,
      ai_rules: splitLines(ruleAiRules.value),
      evidence_requirements: splitLines(ruleEvidence.value),
    });
    ruleName.value = "";
    ruleVersion.value = "";
    ruleSteps.value = "";
    ruleAiRules.value = "";
    ruleEvidence.value = "";
    ruleOpen.value = false;
    await loadRules();
    binding.value = saved.rules_version_id;
    rulesNote.value = "规则版本已保存；需要时再绑定到项目。";
  } catch (error) {
    const friendly = describeError(error);
    rulesError.value = `${friendly.title}。${friendly.next}`;
  }
}

async function bindRule(): Promise<void> {
  if (!workspace.project || !binding.value) {
    rulesNote.value = "先选一个规则版本。";
    return;
  }
  rulesNote.value = "";
  rulesError.value = "";
  try {
    await api.bindRule(workspace.project.project_id, binding.value);
    const project = await api.showProject(workspace.project.project_id);
    workspace.project = project.project;
    rulesNote.value = "已绑定到项目；新生成的计划会引用这个版本。";
  } catch (error) {
    const friendly = describeError(error);
    rulesError.value = `${friendly.title}。${friendly.next}`;
  }
}

async function showRule(id: string): Promise<void> {
  if (!workspace.project) {
    return;
  }
  if (ruleDetail.value?.rules_version_id === id) {
    ruleDetail.value = null;
    return;
  }
  try {
    const data = await api.showRule(workspace.project.project_id, id);
    ruleDetail.value = data.rule;
  } catch (error) {
    const friendly = describeError(error);
    rulesError.value = `${friendly.title}。${friendly.next}`;
  }
}

async function readGithub(): Promise<void> {
  if (!workspace.project) {
    return;
  }
  const repo = githubRepo.value.trim();
  if (!repo) {
    githubError.value = "先填写仓库，例如 owner/name。";
    return;
  }
  githubBusy.value = true;
  githubError.value = "";
  githubNote.value = "";
  github.value = null;
  try {
    github.value = await api.readGithub(workspace.project.project_id, repo, githubRef.value.trim() || "main");
    githubNote.value = "已读取并保存到项目工作空间。";
  } catch (error) {
    const friendly = describeError(error);
    githubError.value = `${friendly.title}。${friendly.next}`;
  } finally {
    githubBusy.value = false;
  }
}

async function runBackup(): Promise<void> {
  backupError.value = "";
  backup.value = null;
  if (!backupPath.value.trim()) {
    backupError.value = "先填写备份文件保存到哪里。";
    return;
  }
  busy.value = true;
  try {
    backup.value = await api.createBackup(backupPath.value.trim(), backupEvidence.value);
  } catch (error) {
    const friendly = describeError(error);
    backupError.value = `${friendly.title}。${friendly.next}`;
  } finally {
    busy.value = false;
  }
}

async function repair(): Promise<void> {
  if (!window.confirm("只执行低风险修复：创建目录、清理缓存、从项目配置移除密钥字段。继续？")) {
    return;
  }
  await refreshDoctor(true);
}

onMounted(() => {
  void loadConfig();
  void loadRules();
});
</script>

<template>
  <div class="settings">
    <header class="page-head">
      <div>
        <h1>设置</h1>
        <p>环境、模型、GitHub 和项目配置；密钥正文不会显示，也不会写进工作空间。</p>
      </div>
      <button type="button" class="ui-btn" :disabled="Boolean(workspace.loading.doctor)" @click="refreshDoctor(false)">
        重新自检
      </button>
    </header>

    <section class="section">
      <div class="section-head">
        <h2>环境自检</h2>
        <span class="ui-help">{{ workspace.doctor ? "事实来自当前机器" : "还没有结果" }}</span>
      </div>
      <div v-if="workspace.doctor" class="facts">
        <div class="fact">
          <span>工作空间</span>
          <b :class="{ bad: !workspace.doctor.workspace.ok }">{{ workspace.doctor.workspace.ok ? "可用" : "不可用" }}</b>
          <span class="detail">{{ workspace.doctor.workspace.path }}</span>
        </div>
        <div class="fact">
          <span>Python</span>
          <b :class="{ bad: !workspace.doctor.python.ok }">{{ workspace.doctor.python.ok ? workspace.doctor.python.version : "不可用" }}</b>
          <span class="detail"></span>
        </div>
        <div class="fact">
          <span>插件包</span>
          <b :class="{ bad: !workspace.doctor.package.ok }">{{ workspace.doctor.package.ok ? workspace.doctor.package.version : "不可用" }}</b>
          <span class="detail"></span>
        </div>
        <div class="fact">
          <span>模型</span>
          <b :class="{ bad: !workspace.doctor.model.ok }">{{ workspace.doctor.model.ok ? "已连通" : "不可用" }}</b>
          <span class="detail">{{ modelSource(workspace.doctor.model.source) }}</span>
        </div>
        <div class="fact">
          <span>GitHub</span>
          <b :class="{ bad: !workspace.doctor.github.ok }">{{ workspace.doctor.github.ok ? "已登录" : "未配置" }}</b>
          <span class="detail">{{ workspace.doctor.github.path || "" }}</span>
        </div>
        <div class="fact">
          <span>代理</span>
          <b :class="{ bad: !workspace.doctor.proxy.ok }">{{ workspace.doctor.proxy.ok ? "正常" : "未通过" }}</b>
          <span class="detail"></span>
        </div>
      </div>
      <p v-else class="ui-help">还没有执行自检。</p>
      <div class="actions">
        <button type="button" class="ui-btn" :disabled="Boolean(workspace.loading.doctor)" @click="repair">
          低风险修复
        </button>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>项目规则</h2>
        <span class="ui-help">计划里的步骤与证据要求引用规则版本</span>
      </div>
      <p v-if="!workspace.project" class="ui-help">先登记项目。</p>
      <template v-else>
        <p v-if="rulesError" class="ui-error">{{ rulesError }}</p>
        <p v-if="!rules.length && !rulesError" class="ui-help">
          还没有规则版本。可以在这里新建，或先用 CLI 导入 Markdown。
        </p>
        <div v-if="ruleOpen" class="rule-editor">
          <label class="field">
            <span class="ui-label">规则名称</span>
            <input v-model="ruleName" class="ui-input" placeholder="例如：下单回归规则" />
          </label>
          <label class="field">
            <span class="ui-label">版本</span>
            <input v-model="ruleVersion" class="ui-input" placeholder="例如：1.0.0" />
          </label>
          <label class="field">
            <span class="ui-label">步骤与说明</span>
            <textarea v-model="ruleSteps" class="ui-textarea config-text" spellcheck="false" />
          </label>
          <label class="field">
            <span class="ui-label">AI 规则（每行一条）</span>
            <textarea v-model="ruleAiRules" class="ui-textarea" spellcheck="false" />
          </label>
          <label class="field">
            <span class="ui-label">证据要求（每行一条）</span>
            <textarea v-model="ruleEvidence" class="ui-textarea" spellcheck="false" />
          </label>
          <div class="actions">
            <button type="button" class="ui-btn" @click="saveRule">保存规则版本</button>
            <button type="button" class="ui-link" @click="ruleOpen = false">取消</button>
          </div>
        </div>
        <button v-else type="button" class="ui-link rule-new" @click="ruleOpen = true">新建规则版本</button>
        <div v-if="!ruleOpen" class="rule-list">
          <div v-for="rule in rules" :key="rule.rules_version_id" class="rule-row">
            <label class="rule-pick">
              <input v-model="binding" type="radio" :value="rule.rules_version_id" />
              <span>{{ rule.name || rule.rules_version_id }}</span>
              <small>{{ rule.version }}</small>
              <small v-if="rule.rules_version_id === workspace.project?.rules_version_id" class="bound">
                当前绑定
              </small>
            </label>
            <button type="button" class="ui-link" @click="showRule(rule.rules_version_id)">
              {{ ruleDetail?.rules_version_id === rule.rules_version_id ? "收起" : "查看" }}
            </button>
          </div>
        </div>
        <div v-if="ruleDetail" class="rule-detail">
          <p v-if="ruleDetail.steps_md" class="ui-help">{{ ruleDetail.steps_md }}</p>
          <p v-for="(line, index) in ruleDetail.ai_rules" :key="`ai-${index}`" class="ui-help">
            · {{ line }}
          </p>
          <p v-for="(line, index) in ruleDetail.evidence_requirements" :key="`ev-${index}`" class="ui-help">
            证据：{{ line }}
          </p>
        </div>
        <div v-if="rules.length" class="actions">
          <button type="button" class="ui-btn" :disabled="!binding" @click="bindRule">
            绑定所选规则
          </button>
          <span v-if="rulesNote" class="ui-help">{{ rulesNote }}</span>
        </div>
      </template>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>GitHub 变更</h2>
        <span class="ui-help">可选；只读提交、PR 与检查结果</span>
      </div>
      <p v-if="!workspace.project" class="ui-help">先登记项目。</p>
      <template v-else>
        <div class="gh-row">
          <label class="field gh-field">
            <span class="ui-label">仓库</span>
            <input v-model="githubRepo" class="ui-input" placeholder="owner/name" />
          </label>
          <label class="field gh-field">
            <span class="ui-label">分支或提交</span>
            <input v-model="githubRef" class="ui-input" placeholder="main" />
          </label>
          <button type="button" class="ui-btn" :disabled="githubBusy" @click="readGithub">
            {{ githubBusy ? "读取中…" : "读取变更" }}
          </button>
        </div>
        <p v-if="githubError" class="ui-error">{{ githubError }}</p>
        <p v-if="githubNote" class="ui-help">{{ githubNote }}</p>
        <template v-if="github">
          <p class="ui-help">
            {{ github.repo }} · {{ github.branch }} · {{ github.commit.slice(0, 12) }} ·
            {{ github.files.length }} 个文件
          </p>
          <p v-for="file in github.files.slice(0, 12)" :key="file" class="ui-mono file-line">{{ file }}</p>
          <p v-if="github.files.length > 12" class="ui-help">还有 {{ github.files.length - 12 }} 个文件。</p>
          <p class="ui-help">PR {{ github.prs.length }} 条 · 检查 {{ github.checks.length }} 条</p>
        </template>
      </template>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>项目配置</h2>
        <span class="ui-help">{{ workspace.project?.name || "还没有项目" }}</span>
      </div>
      <p v-if="!workspace.project" class="ui-help">先登记项目。</p>
      <template v-else>
        <div v-if="!configOpen" class="config-collapsed">
          <p class="ui-help">
            服务健康检查、运行器、调用链日志来源等按段保存；凭据字段会被拒绝。
          </p>
          <button type="button" class="ui-btn" @click="configOpen = true">编辑项目配置</button>
        </div>
        <template v-else>
          <div class="section-tabs">
            <button
              v-for="section in sections"
              :key="section.id"
              type="button"
              class="tab"
              :class="{ on: configSection === section.id }"
              @click="configSection = section.id; pickSection()"
            >
              {{ section.label }}
            </button>
          </div>
          <textarea v-model="configText" class="ui-textarea config-text" spellcheck="false" />
          <p class="ui-help">这里直接编辑该段项目配置；凭据字段会被拒绝，密钥不要写进来。</p>
          <p v-if="configError" class="ui-error">{{ configError }}</p>
          <p v-if="configNote" class="ui-help">{{ configNote }}</p>
          <div class="actions">
            <button type="button" class="ui-btn" :disabled="busy" @click="saveConfig">
              保存这段配置
            </button>
            <button type="button" class="ui-link" @click="configOpen = false">收起</button>
          </div>
        </template>
      </template>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>外观</h2>
        <span class="ui-help">跟随系统时随编辑器明暗切换</span>
      </div>
      <div class="themes">
        <label v-for="theme in THEMES" :key="theme.id" class="theme">
          <input
            type="radio"
            name="theme"
            :value="theme.id"
            :checked="themeMode === theme.id"
            @change="setThemeMode(theme.id)"
          />
          <span>{{ theme.name }}</span>
          <small>{{ theme.summary }}</small>
        </label>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>备份</h2>
        <span class="ui-help">默认不包含证据文件</span>
      </div>
      <label class="field">
        <span class="ui-label">保存到</span>
        <input v-model="backupPath" class="ui-input" placeholder="例如 E:\backup\aitest.zip" />
      </label>
      <label class="check">
        <input v-model="backupEvidence" type="checkbox" />
        同时备份证据
      </label>
      <button type="button" class="ui-btn" :disabled="busy" @click="runBackup">创建备份</button>
      <p v-if="backupError" class="ui-error">{{ backupError }}</p>
      <div v-if="backup" class="backup-result">
        <p class="ui-help">已保存：{{ backup.path }}</p>
        <p class="ui-help">文件 {{ backup.files }} 个 · {{ backup.sha256.slice(0, 12) }}</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.settings {
  max-width: var(--layout-max);
  margin: 0 auto;
  padding: var(--space-4);
  padding-bottom: var(--space-6);
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line-strong);
}

.page-head h1 {
  font-size: var(--fs-20);
}

.page-head p {
  margin-top: 3px;
  color: var(--text-3);
  font-size: var(--fs-11);
  line-height: 1.5;
}

.section {
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}

.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: 7px;
}

.section-head h2 {
  margin: 0;
  font-size: var(--fs-13);
}

.facts {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.fact {
  display: grid;
  grid-template-columns: 88px 90px minmax(0, 1fr);
  gap: 8px;
  align-items: baseline;
  padding: 4px 0;
  border-bottom: 1px solid var(--line);
  font-size: var(--fs-11);
}

.fact > span:first-child {
  color: var(--text-3);
}

.fact b {
  font-weight: 500;
  color: var(--passed);
}

.fact b.bad {
  color: var(--failed);
}

.detail {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  overflow-wrap: anywhere;
}

.actions {
  margin-top: 8px;
}

.themes {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.theme {
  display: grid;
  grid-template-columns: auto auto;
  gap: 2px 5px;
  align-items: center;
  color: var(--text-2);
  font-size: var(--fs-11);
}

.theme small {
  grid-column: 2;
  color: var(--text-3);
  font-size: var(--fs-10);
}

.section-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 6px;
}

.tab {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-3);
  font-size: var(--fs-10);
}

.tab.on {
  color: var(--text);
  text-decoration: underline;
  text-underline-offset: 3px;
}

.config-collapsed .ui-help {
  margin-bottom: 7px;
}

.config-text {
  min-height: 130px;
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  line-height: 1.5;
}

.field {
  display: block;
  margin-bottom: 7px;
}

.check {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 8px;
  color: var(--text-3);
  font-size: var(--fs-10);
}

.backup-result {
  margin-top: 6px;
}

.rule-editor {
  margin: 8px 0 10px;
  padding: 10px;
  border: 1px solid var(--line);
  background: var(--surface-sunken);
}

.rule-editor .ui-textarea {
  min-height: 64px;
  font-family: var(--font-mono);
  font-size: var(--fs-10);
}

.rule-new {
  margin: 6px 0;
}

.rule-list {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--line);
}

.rule-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 5px 0;
  border-bottom: 1px solid var(--line);
}

.rule-pick {
  display: flex;
  align-items: baseline;
  gap: 6px;
  color: var(--text-2);
  font-size: var(--fs-11);
  min-width: 0;
}

.rule-pick small {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
}

.rule-pick .bound {
  color: var(--accent);
}

.rule-detail {
  padding: 6px 0;
}

.gh-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.gh-field {
  min-width: 120px;
  flex: 1;
  margin-bottom: 0;
}

.file-line {
  font-size: var(--fs-10);
  color: var(--text-3);
  overflow-wrap: anywhere;
}

@media (max-width: 520px) {
  .settings {
    padding: var(--space-3);
  }

  .fact {
    grid-template-columns: 70px 76px minmax(0, 1fr);
  }
}
</style>
