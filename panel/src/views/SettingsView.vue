<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, useRoute, useRouter } from "vue-router";

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
import type { GithubSnapshot, ProjectConfig, RuleVersion } from "@/domain/types";
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
  { id: "settings-environment", label: "环境自检" },
  { id: "settings-rules", label: "项目规则" },
  { id: "settings-github", label: "GitHub 变更" },
  { id: "settings-boundary", label: "数据边界" },
  { id: "settings-appearance", label: "外观" },
  { id: "settings-backup", label: "备份" },
];
const activeSection = ref(
  sections.find((section) => section.id === route.query.section)?.id ?? sections[0]?.id ?? "",
);

watch(
  () => route.query.section,
  (value) => {
    const next = sections.find((section) => section.id === value);
    if (next) {
      activeSection.value = next.id;
    }
  },
);

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
const advancedDirty = ref(false);
const formDirty = ref(false);
const configError = ref("");
let applyingAdvanced = false;

watch(
  configDraft,
  () => {
    if (applyingAdvanced) {
      return;
    }
    const next = formatJson(configDraft.value);
    if (next === advanced.value) {
      return;
    }
    advancedDirty.value = false;
    advanced.value = next;
    // 只有常用项被人工修改才算“表单草稿有未保存修改”；项目加载时的回填不算。
    formDirty.value = true;
  },
  { deep: true },
);
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

const githubForm = ref({
  repo: "",
  branch: "main",
  commit: "",
  files: "",
  checks: "",
  account: "",
});
const githubError = ref("");
const busyGithub = computed(() => workspace.busyKey(`github:${projectId.value}`));
const githubReadRepo = ref("");
const githubReadRef = ref("main");
const githubReadError = ref("");
const busyGithubRead = computed(() => workspace.busyKey(`github-read:${projectId.value}`));

const backupBusy = ref(false);
const restoreBusy = ref(false);
const clearBusy = ref(false);
const envBusy = ref(false);
const bindingRuleId = ref("");
const savedAt = ref<Record<string, string>>({});

function markSaved(key: string): void {
  savedAt.value[key] = new Date().toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}
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
    advancedDirty.value = false;
    formDirty.value = false;
    ruleForm.value = { name: "", version: "", steps: "", aiRules: "", evidence: "" };
    githubForm.value = {
      repo: current.github?.repo ?? "",
      branch: current.github?.branch ?? "main",
      commit: current.github?.commit ?? "",
      files: current.github?.files.join("\n") ?? "",
      checks: current.github?.checks.join("\n") ?? "",
      account: current.github?.account ?? "",
    };
    githubReadRepo.value = current.github?.repo ?? "";
    githubReadRef.value = current.github?.branch ?? "main";
  },
  { immediate: true },
);

onMounted(async () => {
  if (workspace.environment.length === 0) {
    await workspace.refreshEnvironment();
  }
});

onBeforeRouteLeave(async () => {
  if (!advancedDirty.value && !formDirty.value) {
    return true;
  }
  const answer = await ui.askConfirm({
    title: "离开设置页？",
    body: "运行配置还有未保存的修改，离开后会丢失。",
    confirmLabel: "放弃修改并离开",
    tone: "danger",
  });
  return answer !== null;
});

async function selectSection(id: string): Promise<void> {
  activeSection.value = id;
  if (route.query.section !== id) {
    void router.replace({ query: { ...route.query, section: id } });
  }
  // 分区导航只切 tabpanel 不移动焦点；读屏会以为还停在导航上。
  await nextTick();
  const panel = document.getElementById(id);
  if (panel) {
    panel.setAttribute("tabindex", "-1");
    panel.focus();
  }
}

async function saveProfile(): Promise<void> {
  const name = profileName.value.trim();
  if (!name || name.length > 40) {
    profileError.value = "项目名不能为空，且最多 40 个字。";
    document.querySelector<HTMLInputElement>("#project-name")?.focus();
    return;
  }
  profileError.value = "";
  try {
    await workspace.updateProject(projectId.value, {
      name,
      goal: profileGoal.value,
    });
    markSaved("profile");
    ui.showToast("项目资料已保存。", "success");
  } catch {
    // store 已提示。
  }
}

async function applyAdvanced(): Promise<void> {
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
  // 只有常用项存在未保存修改且 JSON 合法时才提示；避免错误输入也弹确认。
  if (formDirty.value) {
    const answer = await ui.askConfirm({
      title: "用 JSON 覆盖当前表单草稿？",
      body: "常用项里尚未保存的修改会被 JSON 内容替换。",
      confirmLabel: "覆盖并填入",
      tone: "danger",
    });
    if (answer === null) {
      return;
    }
  }
  applyingAdvanced = true;
  configDraft.value = mergeConfig(defaultConfig(), validated.config);
  advancedDirty.value = false;
  formDirty.value = false;
  await nextTick();
  applyingAdvanced = false;
  advanced.value = formatJson(configDraft.value);
  ui.showToast("JSON 已填入常用项；检查后保存。", "info");
}

async function saveConfig(): Promise<void> {
  configError.value = "";
  let source = configDraft.value;
  if (advancedDirty.value) {
    const parsed = parseJsonObject(advanced.value);
    if (!parsed.ok) {
      configError.value = parsed.message;
      return;
    }
    const validatedJson = validateProjectConfig(parsed.value);
    if (!validatedJson.ok) {
      configError.value = validatedJson.message;
      return;
    }
    applyingAdvanced = true;
    configDraft.value = mergeConfig(defaultConfig(), validatedJson.config);
    await nextTick();
    applyingAdvanced = false;
    source = configDraft.value;
  }
  const validated = validateProjectConfig(source);
  if (!validated.ok) {
    configError.value = validated.message;
    return;
  }
  try {
    await workspace.saveConfig(projectId.value, validated.config);
    advancedDirty.value = false;
    formDirty.value = false;
    advanced.value = formatJson(validated.config);
    markSaved("config");
    ui.showToast("运行配置已保存；旧计划可能在启动时要求重新生成。", "success");
  } catch {
    // store 已提示。
  }
}

async function saveRule(): Promise<void> {
  if (!ruleForm.value.name.trim() || !ruleForm.value.version.trim()) {
    ruleError.value = "规则名称和版本不能为空。";
    document.querySelector<HTMLInputElement>("#rule-name")?.focus();
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
    markSaved("rule");
    ui.showToast("规则版本已保存；绑定后新计划才使用它。", "success");
  } catch {
    // store 已提示。
  }
}

async function bindRule(ruleId: string): Promise<void> {
  bindingRuleId.value = ruleId;
  try {
    await workspace.bindRule(projectId.value, ruleId);
    ui.showToast("规则已绑定；旧计划可能在启动时要求重新生成。", "success");
  } catch {
    // store 已提示。
  } finally {
    bindingRuleId.value = "";
  }
}

async function refreshEnvironment(): Promise<void> {
  envBusy.value = true;
  try {
    await workspace.refreshEnvironment();
  } finally {
    envBusy.value = false;
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
  const downloaded = downloadText(
    `${rule.name}-v${rule.version}.md`,
    lines.join("\n"),
    "text/markdown",
  );
  ui.showToast(
    downloaded ? "规则已导出。" : "当前环境不能下载文件；请用桌面浏览器重试。",
    downloaded ? "success" : "error",
  );
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

async function readGithub(): Promise<void> {
  if (!githubReadRepo.value.trim()) {
    githubReadError.value = "填写仓库后再读取。";
    return;
  }
  githubReadError.value = "";
  try {
    await workspace.readGithub(
      projectId.value,
      githubReadRepo.value.trim(),
      githubReadRef.value.trim() || "main",
    );
    ui.showToast("GitHub 变更已读取。", "success");
  } catch {
    // store 已提示。
  }
}

async function saveGithubSnapshot(): Promise<void> {
  if (!githubForm.value.repo.trim() || !githubForm.value.commit.trim()) {
    githubError.value = "填写仓库与提交号后再保存缓存快照。";
    return;
  }
  githubError.value = "";
  const snapshot: GithubSnapshot = {
    repo: githubForm.value.repo.trim(),
    branch: githubForm.value.branch.trim() || "main",
    commit: githubForm.value.commit.trim(),
    files: parseLineList(githubForm.value.files),
    prs: [],
    checks: parseLineList(githubForm.value.checks),
    deployment: "",
    account: githubForm.value.account.trim(),
    host: "github.com",
    fetchedAt: new Date().toISOString(),
    cached: true,
    unavailableReason: "",
  };
  try {
    await workspace.saveGithubSnapshot(projectId.value, snapshot);
    ui.showToast("GitHub 缓存快照已保存；报告会标“未确认最新”。", "success");
  } catch {
    // store 已提示。
  }
}

async function clearGithubSnapshot(): Promise<void> {
  const answer = await ui.askConfirm({
    title: "清除 GitHub 缓存快照？",
    body: "清除后设置页显示未连接状态；不会影响本地测试、证据与报告。",
    confirmLabel: "清除快照",
    tone: "danger",
  });
  if (answer === null) {
    return;
  }
  try {
    await workspace.saveGithubSnapshot(projectId.value, null);
    ui.showToast("GitHub 缓存快照已清除。", "success");
  } catch {
    // store 已提示。
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
    const data = await file.arrayBuffer();
    await workspace.restoreBackup(data);
    if (workspace.currentProjectId) {
      await router.push(`/projects/${workspace.currentProjectId}/overview`);
    }
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
    requireText: true,
    textLabel: "输入“清空”",
    placeholder: "清空",
  });
  if (answer === null) {
    return;
  }
  if (answer !== "清空") {
    ui.showToast("确认文字不匹配，未执行清空。", "error");
    return;
  }
  clearBusy.value = true;
  try {
    await workspace.clearData();
    await router.push("/setup");
  } finally {
    clearBusy.value = false;
  }
}
</script>

<template>
  <div v-if="!project" class="page">
    <ErrorState
      title="项目不可用"
      text="回到项目概览重新选择项目。"
      retry-label="回到概览"
      @retry="router.push(projectId ? `/projects/${projectId}/overview` : '/')"
    />
  </div>
  <div v-else class="page page-data">
    <PageHeader
      title="设置"
      :subtitle="`${project.name} · 环境、规则与运行配置`"
    >
      <template #actions>
        <button
          class="btn btn-sm settings-back"
          type="button"
          @click="router.push(`/projects/${projectId}/overview`)"
        >
          回到项目概览
        </button>
      </template>
    </PageHeader>

    <p class="doc-note">
      {{
        workspace.mode === "host"
          ? "工作空间由宿主 CLI 管理；面板不直接写业务文件。凭据不写入配置、日志、报告或导出。"
          : "工作空间位于浏览器本地存储，项目仓库内不留插件数据。凭据不写入配置、日志、报告或导出。"
      }}
    </p>

    <div class="settings-layout">
      <SettingsIndex :sections="sections" :active="activeSection" @jump="selectSection" />
      <div class="settings-content">
        <section
          v-show="activeSection === 'settings-profile'"
          class="settings-section"
          id="settings-profile"
          role="tabpanel"
          aria-labelledby="settings-tab-settings-profile"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">项目资料</h2>
              <p class="settings-desc">
                名称与目标可改；目录登记后固定。
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
              <span v-if="savedAt.profile" class="save-stamp">已于 {{ savedAt.profile }} 保存</span>
            </div>
          </form>
        </section>

        <section
          v-show="activeSection === 'settings-config'"
          class="settings-section"
          id="settings-config"
          role="tabpanel"
          aria-labelledby="settings-tab-settings-config"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">运行配置</h2>
              <p class="settings-desc">
                服务、项目级检查、运行器、证据与链路；不接受凭据字段。
              </p>
            </div>
          </div>
          <div class="form-grid">
            <h3 class="settings-subtitle">构建与项目级检查</h3>
            <div class="field">
              <label for="config-build">构建命令</label>
              <input
                id="config-build"
                v-model="configDraft.l1.build"
                type="text"
                placeholder="例如 pnpm build"
              />
            </div>
            <div class="field">
              <label for="config-entry">入口模块 <span class="optional">逗号分隔</span></label>
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
            <h3 class="settings-subtitle">运行器</h3>
            <div class="field">
              <label for="config-test">测试命令</label>
              <input
                id="config-test"
                v-model="configDraft.runners.test"
                type="text"
                placeholder="例如 pytest"
              />
            </div>
            <h3 class="settings-subtitle">服务</h3>
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
            <h3 class="settings-subtitle">证据</h3>
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
            <h3 class="settings-subtitle">链路</h3>
            <div class="field">
              <label for="config-log">链路日志路径</label>
              <input
                id="config-log"
                v-model="configDraft.link.logPath"
                type="text"
                placeholder="留空表示未配置"
              />
            </div>
            <h3 class="settings-subtitle">模型</h3>
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
            <textarea
              id="config-json"
              v-model="advanced"
              class="code-input"
              rows="8"
              @input="advancedDirty = true"
            />
            <p v-if="configError" class="field-error" role="alert">{{ configError }}</p>
          </div>
          <div class="form-actions settings-savebar">
            <button class="btn" type="button" @click="applyAdvanced">解析并填入表单</button>
            <button
              class="btn btn-primary"
              type="button"
              :disabled="busyConfig"
              :aria-busy="busyConfig"
              @click="saveConfig"
            >
              {{ busyConfig ? "正在保存…" : "保存运行配置" }}
            </button>
            <span v-if="savedAt.config" class="save-stamp">已于 {{ savedAt.config }} 保存</span>
          </div>
        </section>

        <section
          v-show="activeSection === 'settings-environment'"
          class="settings-section"
          id="settings-environment"
          role="tabpanel"
          aria-labelledby="settings-tab-settings-environment"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">环境自检</h2>
              <p class="settings-desc">缺失只影响对应能力，本地记录与报告仍可用。</p>
            </div>
            <button
              class="btn btn-sm"
              type="button"
              :disabled="envBusy"
              :aria-busy="envBusy"
              @click="refreshEnvironment"
            >
              {{ envBusy ? "正在检查…" : "重新检查" }}
            </button>
          </div>
          <div v-if="workspace.environment.length" class="env-list">
            <div v-for="check in workspace.environment" :key="check.key" class="env-row">
              <span class="env-name">{{ check.name }}</span>
              <span class="env-value">{{ check.value }}</span>
              <span class="env-state" :class="{ 'is-missing': !check.ok }">
                {{ check.ok ? "通过" : "缺失" }}
              </span>
              <span v-if="!check.ok && (check.detail || check.repair)" class="env-detail">
                {{ check.detail }}<template v-if="check.repair"> · {{ check.repair }}</template>
              </span>
            </div>
          </div>
          <p v-else class="doc-note">还没有检查结果；点“重新检查”读取当前环境。</p>
        </section>

        <section
          v-show="activeSection === 'settings-rules'"
          class="settings-section"
          id="settings-rules"
          role="tabpanel"
          aria-labelledby="settings-tab-settings-rules"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">项目规则</h2>
              <p class="settings-desc">
                新计划用绑定版本；旧运行保留旧版本。
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
              <span class="rule-meta">
                <span class="badge" :class="rule.id === project.rulesVersionId ? 'is-accent' : ''">
                  {{ rule.id === project.rulesVersionId ? "已绑定" : "历史版本" }}
                </span>
                v{{ rule.version }}
              </span>
              <span class="rule-actions">
                <button class="btn btn-sm" type="button" @click="exportRule(rule)">导出</button>
                <button
                  v-if="rule.id !== project.rulesVersionId"
                  class="btn btn-sm"
                  type="button"
                  :disabled="Boolean(bindingRuleId)"
                  :aria-busy="bindingRuleId === rule.id"
                  @click="bindRule(rule.id)"
                >
                  {{ bindingRuleId === rule.id ? "绑定中…" : "绑定" }}
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
                <input
                  id="rule-version"
                  v-model="ruleForm.version"
                  type="text"
                  maxlength="20"
                  :aria-invalid="ruleError ? 'true' : undefined"
                  :aria-describedby="ruleError ? 'rule-error' : undefined"
                />
              </div>
            </div>
            <p v-if="ruleError" id="rule-error" class="field-error" role="alert">
              {{ ruleError }}
            </p>
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
            <div class="form-actions">
              <button
                class="btn btn-primary"
                type="submit"
                :disabled="busyRule"
                :aria-busy="busyRule"
              >
                {{ busyRule ? "正在保存…" : "保存规则版本" }}
              </button>
              <span v-if="savedAt.rule" class="save-stamp">已于 {{ savedAt.rule }} 保存</span>
            </div>
          </form>
        </section>

        <section
          v-show="activeSection === 'settings-github'"
          class="settings-section"
          id="settings-github"
          role="tabpanel"
          aria-labelledby="settings-tab-settings-github"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">GitHub 变更</h2>
              <p class="settings-desc">只读提交与检查；缓存不冒充最新。</p>
            </div>
          </div>
          <div v-if="project.github" class="surface-strip">
            <div class="row-between">
              <p class="doc-p">
                <strong>{{ project.github.repo }}</strong> · {{ project.github.commit }} ·
                {{ project.github.branch }}
              </p>
              <span class="badge" :class="project.github.cached ? 'is-warn' : 'is-pass'">
                {{ project.github.cached ? "缓存快照，未确认最新" : "最新读取" }}
              </span>
            </div>
            <p class="doc-note">
              读取于 {{ new Date(project.github.fetchedAt).toLocaleString("zh-CN") }}
              <template v-if="project.github.account"> · {{ project.github.account }}</template>
            </p>
            <p class="doc-note mono">{{ project.github.files.join(" · ") || "没有文件列表" }}</p>
            <p v-if="project.github.checks.length" class="doc-note">
              检查：{{ project.github.checks.join(" · ") }}
            </p>
            <button
              class="btn btn-sm btn-danger"
              type="button"
              :disabled="busyGithub"
              @click="clearGithubSnapshot"
            >
              清除缓存快照
            </button>
          </div>
          <EmptyState
            v-else
            title="未连接 GitHub"
            :text="
              workspace.mode === 'host'
                ? '输入仓库与分支后读取；读取失败不会用缓存冒充最新。'
                : '只读提交、PR、检查与部署不可用；本地测试、证据与报告不受影响。可以手动登记缓存快照，或接入宿主后读取。'
            "
          />
          <form
            v-if="workspace.mode === 'host'"
            class="settings-form github-read-form"
            novalidate
            @submit.prevent="readGithub"
          >
            <div class="form-grid">
              <div class="field">
                <label for="github-read-repo">仓库</label>
                <input id="github-read-repo" v-model="githubReadRepo" type="text" placeholder="owner/name" />
              </div>
              <div class="field">
                <label for="github-read-ref">分支或引用</label>
                <input id="github-read-ref" v-model="githubReadRef" type="text" placeholder="main" />
              </div>
            </div>
            <p v-if="githubReadError" class="field-error" role="alert">{{ githubReadError }}</p>
            <div class="form-actions">
              <button class="btn btn-primary" type="submit" :disabled="busyGithubRead" :aria-busy="busyGithubRead">
                {{ busyGithubRead ? "正在读取…" : "读取 GitHub" }}
              </button>
            </div>
          </form>
          <details v-if="workspace.mode !== 'host'" class="link-tools" :open="!project.github">
            <summary>{{ project.github ? "更新缓存快照" : "登记缓存快照" }}</summary>
            <form class="settings-form" novalidate @submit.prevent="saveGithubSnapshot">
              <div class="form-grid">
                <div class="field">
                  <label for="github-repo">仓库</label>
                  <input
                    id="github-repo"
                    v-model="githubForm.repo"
                    type="text"
                    placeholder="owner/name"
                  />
                </div>
                <div class="field">
                  <label for="github-branch">分支</label>
                  <input
                    id="github-branch"
                    v-model="githubForm.branch"
                    type="text"
                    placeholder="main"
                  />
                </div>
                <div class="field">
                  <label for="github-commit">提交</label>
                  <input
                    id="github-commit"
                    v-model="githubForm.commit"
                    type="text"
                    placeholder="abc123"
                  />
                </div>
                <div class="field">
                  <label for="github-account">账号 <span class="optional">选填</span></label>
                  <input id="github-account" v-model="githubForm.account" type="text" />
                </div>
              </div>
              <div class="form-grid">
                <div class="field">
                  <label for="github-files">文件 <span class="optional">一行一条</span></label>
                  <textarea id="github-files" v-model="githubForm.files" rows="3" />
                </div>
                <div class="field">
                  <label for="github-checks">检查 <span class="optional">一行一条</span></label>
                  <textarea id="github-checks" v-model="githubForm.checks" rows="3" />
                </div>
              </div>
              <p v-if="githubError" class="field-error" role="alert">{{ githubError }}</p>
              <p class="field-hint">
                这是手动登记的缓存快照，不代表 GitHub 最新状态；报告会明确标注“未确认最新”。
              </p>
              <div class="form-actions">
                <button
                  class="btn btn-primary"
                  type="submit"
                  :disabled="busyGithub"
                  :aria-busy="busyGithub"
                >
                  {{ busyGithub ? "正在保存…" : "保存缓存快照" }}
                </button>
              </div>
            </form>
          </details>
        </section>

        <section
          v-show="activeSection === 'settings-boundary'"
          class="settings-section"
          id="settings-boundary"
          role="tabpanel"
          aria-labelledby="settings-tab-settings-boundary"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">数据边界</h2>
              <p class="settings-desc">约束本机数据，不改被测仓库。</p>
            </div>
          </div>
          <div class="boundary-list">
            <div class="boundary-row">
              <span>项目隔离</span>
              <p>测试、问题、历史、报告只属于当前项目；项目列表只汇总，不混合明细。</p>
            </div>
            <div class="boundary-row">
              <span>本机存储</span>
              <p>
                {{
                  workspace.mode === "host"
                    ? "项目记录、证据元数据与报告由宿主 CLI 写入本机工作空间，不写进被测仓库。"
                    : "项目记录、证据元数据与报告留在浏览器本地存储，不写进被测仓库。"
                }}
              </p>
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
          v-show="activeSection === 'settings-appearance'"
          class="settings-section"
          id="settings-appearance"
          role="tabpanel"
          aria-labelledby="settings-tab-settings-appearance"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">外观</h2>
              <p class="settings-desc">明暗共用同一结构，默认跟随系统。</p>
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
          v-show="activeSection === 'settings-backup'"
          class="settings-section"
          id="settings-backup"
          role="tabpanel"
          aria-labelledby="settings-tab-settings-backup"
        >
          <div class="settings-head">
            <div>
              <h2 class="settings-title">备份</h2>
              <p class="settings-desc">
                {{
                  workspace.capabilities.browserBackup
                    ? "ZIP 备份包含业务记录，不包含证据正文；恢复会替换当前本地数据。"
                    : "创建备份会写入宿主工作空间；恢复与清理由 CLI 完成。"
                }}
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
            <template v-if="workspace.capabilities.browserBackup">
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
                accept=".zip,application/zip,application/json,.json"
                @change="onRestoreFile"
              />
            </template>
            <span class="backup-meta">
              {{ workspace.capabilities.browserBackup ? "本地 ZIP" : "宿主工作空间" }}
            </span>
          </div>
          <h3 class="settings-subtitle">危险操作</h3>
          <div v-if="workspace.capabilities.browserBackup" class="backup-row">
            <button
              class="btn btn-sm btn-danger"
              type="button"
              :disabled="clearBusy"
              :aria-busy="clearBusy"
              @click="clearData"
            >
              {{ clearBusy ? "正在清空…" : "清空本地数据" }}
            </button>
            <span class="backup-meta">删除本机保存的项目、任务、问题与报告；不影响被测仓库。</span>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>
