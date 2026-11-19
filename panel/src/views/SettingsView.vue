<script setup lang="ts">
import { computed, onMounted, ref, watch, type ComponentPublicInstance } from "vue";
import { useRoute, useRouter } from "vue-router";

import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import SettingsIndex from "@/components/SettingsIndex.vue";
import { defaultConfig } from "@/domain/factory";
import {
  formatJson,
  parseJsonObject,
  parseLineList,
  validateProjectConfig,
} from "@/domain/validation";
import type { ProjectConfig, RuleVersion } from "@/domain/types";
import { downloadText } from "@/services/exportService";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

type FullConfig = {
  model: NonNullable<ProjectConfig["model"]>;
  services: NonNullable<ProjectConfig["services"]>;
  l1: NonNullable<ProjectConfig["l1"]>;
  runners: NonNullable<ProjectConfig["runners"]>;
  evidence: NonNullable<ProjectConfig["evidence"]>;
  link: NonNullable<ProjectConfig["link"]>;
  data: NonNullable<ProjectConfig["data"]>;
};

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();
const projectId = computed(() => String(route.params.projectId ?? ""));
const project = computed(() => workspace.currentProject);

const sections = [
  { id: "settings-profile", label: "项目资料" },
  { id: "settings-config", label: "运行配置" },
  { id: "settings-environment", label: "环境" },
  { id: "settings-rules", label: "项目规则" },
  { id: "settings-github", label: "GitHub" },
  { id: "settings-boundary", label: "数据边界" },
  { id: "settings-appearance", label: "外观" },
  { id: "settings-backup", label: "备份" },
];
const activeSection = ref(sections[0]?.id ?? "");
const sectionRefs = new Map<string, HTMLElement>();

const profileName = ref("");
const profileGoal = ref("");
const profileError = ref("");
const busyProfile = computed(() => workspace.busyKey(`project:${projectId.value}`));

function mergeConfig(base: ReturnType<typeof defaultConfig>, current: ProjectConfig): FullConfig {
  return {
    model: { ...base.model, ...current.model },
    services: { ...base.services, ...current.services },
    l1: { ...base.l1, ...current.l1 },
    runners: { ...base.runners, ...current.runners },
    evidence: { ...base.evidence, ...current.evidence },
    link: { ...base.link, ...current.link },
    data: { ...base.data, ...current.data },
  };
}

const configDraft = ref<FullConfig>(mergeConfig(defaultConfig(), {}));
const advanced = ref("");
const configError = ref("");
const busyConfig = computed(() => workspace.busyKey(`config:${projectId.value}`));

const ruleForm = ref({
  name: "",
  version: "",
  steps: "",
  aiRules: "",
  evidence: "",
});
const ruleError = ref("");
const busyRule = computed(() => workspace.busyKey(`rule:${projectId.value}`));
const boundRule = computed(
  () => project.value?.rules.find((rule) => rule.id === project.value?.rulesVersionId) ?? null,
);

const backupBusy = ref(false);
const restoreBusy = ref(false);
const restoreInput = ref<HTMLInputElement | null>(null);
const ruleImportInput = ref<HTMLInputElement | null>(null);

watch(
  () => project.value?.id,
  () => {
    const current = project.value;
    if (!current) {
      return;
    }
    profileName.value = current.name;
    profileGoal.value = current.goal;
    configDraft.value = mergeConfig(defaultConfig(), current.config);
    advanced.value = formatJson(configDraft.value);
    ruleForm.value = { name: "", version: "", steps: "", aiRules: "", evidence: "" };
  },
  { immediate: true },
);

onMounted(async () => {
  if (workspace.environment.length === 0) {
    await workspace.refreshEnvironment();
  }
});

function setSectionRef(id: string, element: Element | ComponentPublicInstance | null): void {
  if (element instanceof HTMLElement) {
    sectionRefs.set(id, element);
  } else {
    sectionRefs.delete(id);
  }
}

function jump(id: string): void {
  activeSection.value = id;
  const element = sectionRefs.get(id);
  if (!element) {
    return;
  }
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  element.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
}

async function saveProfile(): Promise<void> {
  const name = profileName.value.trim();
  if (!name || name.length > 40) {
    profileError.value = "项目名不能为空，且最多 40 个字。";
    return;
  }
  profileError.value = "";
  try {
    await workspace.updateProject(projectId.value, {
      name,
      goal: profileGoal.value,
    });
    ui.showToast("项目资料已保存。", "success");
  } catch {
    // store 已提示。
  }
}

function applyAdvanced(): void {
  configError.value = "";
  const parsed = parseJsonObject(advanced.value);
  if (!parsed.ok) {
    configError.value = parsed.message;
    return;
  }
  const validated = validateProjectConfig(parsed.value);
  if (!validated.ok) {
    configError.value = validated.message;
    return;
  }
  configDraft.value = mergeConfig(defaultConfig(), validated.config);
  advanced.value = formatJson(configDraft.value);
  ui.showToast("JSON 已填入常用项；检查后保存。", "info");
}

async function saveConfig(): Promise<void> {
  const validated = validateProjectConfig(configDraft.value);
  if (!validated.ok) {
    configError.value = validated.message;
    return;
  }
  configError.value = "";
  try {
    await workspace.saveConfig(projectId.value, validated.config);
    advanced.value = formatJson(validated.config);
    ui.showToast("运行配置已保存；旧计划可能在启动时要求重新生成。", "success");
  } catch {
    // store 已提示。
  }
}

async function saveRule(): Promise<void> {
  if (!ruleForm.value.name.trim() || !ruleForm.value.version.trim()) {
    ruleError.value = "规则名称和版本不能为空。";
    return;
  }
  ruleError.value = "";
  try {
    await workspace.saveRule(projectId.value, {
      name: ruleForm.value.name,
      version: ruleForm.value.version,
      stepsMd: ruleForm.value.steps,
      aiRules: parseLineList(ruleForm.value.aiRules),
      evidenceRequirements: parseLineList(ruleForm.value.evidence),
    });
    ruleForm.value = { name: "", version: "", steps: "", aiRules: "", evidence: "" };
    ui.showToast("规则版本已保存；绑定后新计划才使用它。", "success");
  } catch {
    // store 已提示。
  }
}

async function bindRule(ruleId: string): Promise<void> {
  try {
    await workspace.bindRule(projectId.value, ruleId);
    ui.showToast("规则已绑定；旧计划可能在启动时要求重新生成。", "success");
  } catch {
    // store 已提示。
  }
}

function exportRule(rule: RuleVersion): void {
  const lines = [
    `# ${rule.name} v${rule.version}`,
    "",
    rule.stepsMd || "未填写步骤。",
    "",
    "## AI 规则",
    ...(rule.aiRules.length ? rule.aiRules.map((item) => `- ${item}`) : ["- 未填写"]),
    "",
    "## 证据要求",
    ...(rule.evidenceRequirements.length
      ? rule.evidenceRequirements.map((item) => `- ${item}`)
      : ["- 未填写"]),
    "",
  ];
  downloadText(`${rule.name}-v${rule.version}.md`, lines.join("\n"), "text/markdown");
  ui.showToast("规则已导出。", "success");
}

function openRuleImport(): void {
  ruleImportInput.value?.click();
}

async function onRuleFile(event: Event): Promise<void> {
  const target = event.target;
  if (!(target instanceof HTMLInputElement) || !target.files?.[0]) {
    return;
  }
  const file = target.files[0];
  try {
    const text = await file.text();
    ruleForm.value.name = file.name.replace(/\.[^.]+$/, "");
    ruleForm.value.version = "1";
    ruleForm.value.steps = text;
    ui.showToast("规则文件已读入；检查名称与版本后保存。", "info");
  } catch {
    ui.showToast("读取规则文件失败。", "error");
  } finally {
    target.value = "";
  }
}

async function createBackup(): Promise<void> {
  backupBusy.value = true;
  try {
    await workspace.backup();
  } catch {
    // store 已提示。
  } finally {
    backupBusy.value = false;
  }
}

function openRestore(): void {
  restoreInput.value?.click();
}

async function onRestoreFile(event: Event): Promise<void> {
  const target = event.target;
  if (!(target instanceof HTMLInputElement) || !target.files?.[0]) {
    return;
  }
  const file = target.files[0];
  const answer = await ui.askConfirm({
    title: "从备份恢复？",
    body: "恢复会替换当前本机保存的项目、任务、问题与报告。不会影响被测仓库。",
    confirmLabel: "恢复备份",
    tone: "danger",
  });
  if (answer === null) {
    target.value = "";
    return;
  }
  restoreBusy.value = true;
  try {
    const text = await file.text();
    await workspace.restoreBackup(text);
  } catch {
    // store 已提示。
  } finally {
    restoreBusy.value = false;
    target.value = "";
  }
}

async function clearData(): Promise<void> {
  const answer = await ui.askConfirm({
    title: "清空本地面板数据？",
    body: "会删除本机保存的项目、任务、问题与报告；这个动作不能撤销，不影响被测仓库。",
    confirmLabel: "清空本地数据",
    tone: "danger",
  });
  if (answer === null) {
    return;
  }
  await workspace.clearData();
  await router.push("/setup");
}
</script>

<template>
  <div v-if="!project" class="page">
    <ErrorState
      title="项目不可用"
      text="回到项目概览重新选择项目。"
      retry-label="回到概览"
      @retry="router.push('/')"
    />
  </div>
  <div v-else class="page">
    <PageHeader
      kicker="设置"
      title="设置"
      :subtitle="`${project.name} · 环境、规则、配置与本机工作空间`"
    >
      <template #actions>
        <button
          class="btn btn-sm"
          type="button"
          @click="router.push(`/projects/${projectId}/overview`)"
        >
          返回项目
        </button>
      </template>
    </PageHeader>

    <div class="settings-layout">
      <SettingsIndex :sections="sections" :active="activeSection" @jump="jump" />
      <div class="settings-content">
        <section
          :ref="(el) => setSectionRef('settings-profile', el)"
          class="settings-section"
          id="settings-profile"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">项目资料</h2>
              <p class="settings-desc">
                只改显示名称与项目目标；目录是登记时确定的，不在这里改写。
              </p>
            </div>
          </div>
          <form class="settings-form" novalidate @submit.prevent="saveProfile">
            <div class="form-grid">
              <div class="field" :class="{ 'has-error': profileError }">
                <label for="project-name">项目名</label>
                <input
                  id="project-name"
                  v-model="profileName"
                  type="text"
                  maxlength="40"
                  :aria-describedby="profileError ? 'project-name-error' : undefined"
                  @input="profileError = ''"
                />
                <p v-if="profileError" id="project-name-error" class="field-error" role="alert">
                  {{ profileError }}
                </p>
              </div>
              <div class="field">
                <label for="project-root">项目目录</label>
                <input id="project-root" :value="project.root" type="text" disabled />
              </div>
            </div>
            <div class="field">
              <label for="project-goal">项目目标</label>
              <textarea
                id="project-goal"
                v-model="profileGoal"
                rows="3"
                placeholder="这个项目要解决什么"
              />
            </div>
            <div class="form-actions">
              <button
                class="btn btn-primary"
                type="submit"
                :disabled="busyProfile"
                :aria-busy="busyProfile"
              >
                {{ busyProfile ? "正在保存…" : "保存项目资料" }}
              </button>
            </div>
          </form>
        </section>

        <section
          :ref="(el) => setSectionRef('settings-config', el)"
          class="settings-section"
          id="settings-config"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">运行配置</h2>
              <p class="settings-desc">
                保存服务、项目级检查、运行器、证据、链路与数据边界配置；拒绝凭据字段。
              </p>
            </div>
          </div>
          <div class="form-grid">
            <div class="field">
              <label for="config-build">构建命令</label>
              <input
                id="config-build"
                v-model="configDraft.l1.build"
                type="text"
                placeholder="留空时自动检测"
              />
            </div>
            <div class="field">
              <label for="config-entry">入口命令</label>
              <input
                id="config-entry"
                v-model="configDraft.l1.entry"
                type="text"
                placeholder="留空时自动检测"
              />
            </div>
            <div class="field">
              <label for="config-smoke">最小测试命令</label>
              <input
                id="config-smoke"
                v-model="configDraft.l1.smokeCommand"
                type="text"
                placeholder="留空时自动检测"
              />
            </div>
            <div class="field">
              <label for="config-test">测试命令</label>
              <input
                id="config-test"
                v-model="configDraft.runners.test"
                type="text"
                placeholder="例如 pytest"
              />
            </div>
            <div class="field">
              <label for="config-health">服务健康地址</label>
              <input
                id="config-health"
                v-model="configDraft.services.healthUrl"
                type="text"
                placeholder="http://127.0.0.1:8000/health"
              />
            </div>
            <div class="field">
              <label for="config-base">服务地址</label>
              <input
                id="config-base"
                v-model="configDraft.services.baseUrl"
                type="text"
                placeholder="http://127.0.0.1:8000"
              />
            </div>
            <div class="field">
              <label for="config-evidence-file">单文件证据上限（MB）</label>
              <input
                id="config-evidence-file"
                v-model.number="configDraft.evidence.singleFileMb"
                type="number"
                min="1"
              />
            </div>
            <div class="field">
              <label for="config-evidence-media">单次媒体上限（MB）</label>
              <input
                id="config-evidence-media"
                v-model.number="configDraft.evidence.mediaMb"
                type="number"
                min="1"
              />
            </div>
            <div class="field">
              <label for="config-log">链路日志路径</label>
              <input
                id="config-log"
                v-model="configDraft.link.logPath"
                type="text"
                placeholder="留空表示未配置"
              />
            </div>
            <div class="field">
              <label for="config-model">模型名</label>
              <input
                id="config-model"
                v-model="configDraft.model.name"
                type="text"
                placeholder="留空表示未接入"
              />
            </div>
          </div>
          <div class="field">
            <label for="config-json">高级 JSON <span class="optional">只允许白名单段</span></label>
            <textarea id="config-json" v-model="advanced" class="code-input" rows="8" />
            <p v-if="configError" class="field-error" role="alert">{{ configError }}</p>
          </div>
          <div class="form-actions">
            <button class="btn btn-sm" type="button" @click="applyAdvanced">解析并填入表单</button>
            <button
              class="btn btn-primary"
              type="button"
              :disabled="busyConfig"
              :aria-busy="busyConfig"
              @click="saveConfig"
            >
              {{ busyConfig ? "正在保存…" : "保存运行配置" }}
            </button>
          </div>
        </section>

        <section
          :ref="(el) => setSectionRef('settings-environment', el)"
          class="settings-section"
          id="settings-environment"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">环境自检</h2>
              <p class="settings-desc">缺失项不扩散到全部能力；本地记录、证据与报告仍可用。</p>
            </div>
            <button class="btn btn-sm" type="button" @click="workspace.refreshEnvironment()">
              重新检查
            </button>
          </div>
          <div class="env-list">
            <div v-for="check in workspace.environment" :key="check.key" class="env-row">
              <span class="env-name">{{ check.name }}</span>
              <span class="env-value">{{ check.value }}</span>
              <span class="env-state" :class="{ 'is-missing': !check.ok }">
                {{ check.ok ? "通过" : "缺失" }}
              </span>
            </div>
          </div>
          <div
            v-for="check in workspace.environment.filter((item) => !item.ok)"
            :key="`${check.key}-repair`"
            class="env-repair"
          >
            <strong>{{ check.name }}</strong>：{{ check.detail }}
            <template v-if="check.repair"> · {{ check.repair }}</template>
          </div>
        </section>

        <section
          :ref="(el) => setSectionRef('settings-rules', el)"
          class="settings-section"
          id="settings-rules"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">项目规则</h2>
              <p class="settings-desc">
                规则有版本；新计划引用当前绑定版本，旧运行保留自己的规则版本。
              </p>
            </div>
            <button class="btn btn-sm" type="button" @click="openRuleImport">导入规则文件</button>
            <input
              ref="ruleImportInput"
              class="visually-hidden"
              type="file"
              accept=".md,.txt"
              @change="onRuleFile"
            />
          </div>
          <div class="rule-list">
            <div v-for="rule in project.rules" :key="rule.id" class="rule-row">
              <span class="rule-name">{{ rule.name }}</span>
              <span class="rule-meta"
                >v{{ rule.version }} ·
                {{ rule.id === project.rulesVersionId ? "已绑定" : "历史版本" }}</span
              >
              <span class="rule-actions">
                <button class="link-btn" type="button" @click="exportRule(rule)">导出</button>
                <button
                  v-if="rule.id !== project.rulesVersionId"
                  class="link-btn"
                  type="button"
                  :disabled="workspace.busyKey(`rule-bind:${projectId}`)"
                  @click="bindRule(rule.id)"
                >
                  绑定
                </button>
              </span>
              <details class="rule-detail">
                <summary>查看</summary>
                <p class="doc-p">{{ rule.stepsMd || "未填写步骤。" }}</p>
                <ul class="list-plain">
                  <li v-for="item in rule.aiRules" :key="item">AI 规则：{{ item }}</li>
                  <li v-for="item in rule.evidenceRequirements" :key="item">
                    证据要求：{{ item }}
                  </li>
                </ul>
              </details>
            </div>
          </div>
          <p v-if="boundRule" class="doc-note">
            当前绑定：{{ boundRule.name }} v{{
              boundRule.version
            }}。绑定变化可能在启动时要求重新生成计划。
          </p>
          <div class="divider"></div>
          <h3 class="settings-title">新建规则版本</h3>
          <form class="settings-form" novalidate @submit.prevent="saveRule">
            <div class="form-grid">
              <div class="field">
                <label for="rule-name">名称</label>
                <input id="rule-name" v-model="ruleForm.name" type="text" maxlength="40" />
              </div>
              <div class="field">
                <label for="rule-version">版本</label>
                <input id="rule-version" v-model="ruleForm.version" type="text" maxlength="20" />
              </div>
            </div>
            <div class="field">
              <label for="rule-steps">步骤</label>
              <textarea
                id="rule-steps"
                v-model="ruleForm.steps"
                rows="4"
                placeholder="按项目 README 与启动脚本执行主流程检查。"
              />
            </div>
            <div class="form-grid">
              <div class="field">
                <label for="rule-ai">AI 规则 <span class="optional">一行一条</span></label>
                <textarea id="rule-ai" v-model="ruleForm.aiRules" rows="3" />
              </div>
              <div class="field">
                <label for="rule-evidence">证据要求 <span class="optional">一行一条</span></label>
                <textarea id="rule-evidence" v-model="ruleForm.evidence" rows="3" />
              </div>
            </div>
            <p v-if="ruleError" class="field-error" role="alert">{{ ruleError }}</p>
            <div class="form-actions">
              <button
                class="btn btn-primary"
                type="submit"
                :disabled="busyRule"
                :aria-busy="busyRule"
              >
                {{ busyRule ? "正在保存…" : "保存规则版本" }}
              </button>
            </div>
          </form>
        </section>

        <section
          :ref="(el) => setSectionRef('settings-github', el)"
          class="settings-section"
          id="settings-github"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">GitHub 变更</h2>
              <p class="settings-desc">只读提交与检查；读取失败时不会用缓存冒充最新。</p>
            </div>
          </div>
          <div v-if="project.github" class="surface-strip">
            <p class="doc-p">
              <strong>{{ project.github.repo }}</strong> · {{ project.github.commit }} ·
              {{ project.github.branch }}
            </p>
            <p class="doc-note">
              读取于 {{ project.github.fetchedAt }} ·
              {{ project.github.cached ? "缓存快照，未确认最新" : "最新读取" }}
            </p>
            <p class="doc-note mono">{{ project.github.files.join(" · ") || "没有文件列表" }}</p>
          </div>
          <EmptyState
            v-else
            title="未连接 GitHub"
            text="只读提交、PR、检查与部署不可用；本地测试、证据与报告不受影响。接入宿主后再读取，不使用演示快照。"
          />
        </section>

        <section
          :ref="(el) => setSectionRef('settings-boundary', el)"
          class="settings-section"
          id="settings-boundary"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">数据边界</h2>
              <p class="settings-desc">这些是本机数据的边界规则，不会改动被测仓库。</p>
            </div>
          </div>
          <div class="boundary-list">
            <div class="boundary-row">
              <span>项目隔离</span>
              <p>测试、问题、历史、报告只属于当前项目；项目列表只汇总，不混合明细。</p>
            </div>
            <div class="boundary-row">
              <span>本机存储</span>
              <p>项目记录、证据元数据与报告留在浏览器本地存储，不写进被测仓库。</p>
            </div>
            <div class="boundary-row">
              <span>运行锁</span>
              <p>同一项目同时只运行一个任务；运行中不能切换任务、新建任务或做快速检查。</p>
            </div>
            <div class="boundary-row">
              <span>凭据</span>
              <p>配置拒绝 password/token/secret 等字段；凭据不进入报告或导出。</p>
            </div>
            <div class="boundary-row">
              <span>清理</span>
              <p>面板只提供清空本地数据；按运行清理测试数据仍由 CLI 执行。</p>
            </div>
          </div>
        </section>

        <section
          :ref="(el) => setSectionRef('settings-appearance', el)"
          class="settings-section"
          id="settings-appearance"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">外观</h2>
              <p class="settings-desc">明暗两套主题共用同一结构；默认跟随系统。</p>
            </div>
          </div>
          <div class="theme-group" role="group" aria-label="外观主题">
            <button
              class="filter-btn"
              :class="{ 'is-active': ui.theme === 'auto' }"
              type="button"
              :aria-pressed="ui.theme === 'auto'"
              @click="ui.setTheme('auto')"
            >
              跟随系统
            </button>
            <button
              class="filter-btn"
              :class="{ 'is-active': ui.theme === 'light' }"
              type="button"
              :aria-pressed="ui.theme === 'light'"
              @click="ui.setTheme('light')"
            >
              浅色
            </button>
            <button
              class="filter-btn"
              :class="{ 'is-active': ui.theme === 'dark' }"
              type="button"
              :aria-pressed="ui.theme === 'dark'"
              @click="ui.setTheme('dark')"
            >
              深色
            </button>
          </div>
        </section>

        <section
          :ref="(el) => setSectionRef('settings-backup', el)"
          class="settings-section"
          id="settings-backup"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">备份</h2>
              <p class="settings-desc">
                本地 JSON 备份包含业务记录，不包含证据正文；恢复会替换当前本地数据。
              </p>
            </div>
          </div>
          <div class="backup-row">
            <button
              class="btn btn-sm"
              type="button"
              :disabled="backupBusy"
              :aria-busy="backupBusy"
              @click="createBackup"
            >
              {{ backupBusy ? "正在创建…" : "创建备份" }}
            </button>
            <button
              class="btn btn-sm"
              type="button"
              :disabled="restoreBusy"
              :aria-busy="restoreBusy"
              @click="openRestore"
            >
              {{ restoreBusy ? "正在恢复…" : "从备份恢复" }}
            </button>
            <input
              ref="restoreInput"
              class="visually-hidden"
              type="file"
              accept="application/json,.json"
              @change="onRestoreFile"
            />
            <span class="backup-meta">本地 JSON · 不打包证据 · 真实 ZIP 备份仍由 CLI 完成</span>
          </div>
          <div class="backup-row">
            <button class="btn btn-sm btn-danger" type="button" @click="clearData">
              清空本地数据
            </button>
            <span class="backup-meta">删除本机保存的项目、任务、问题与报告；不影响被测仓库。</span>
          </div>
        </section>

        <p class="doc-note">
          工作空间位于浏览器本地存储，项目仓库内不留插件数据。凭据不写入配置、日志、报告或导出。
        </p>
      </div>
    </div>
  </div>
</template>
