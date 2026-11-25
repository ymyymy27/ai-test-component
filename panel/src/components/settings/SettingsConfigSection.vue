<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";

import FormField from "@/components/FormField.vue";
import { defaultConfig } from "@/domain/factory";
import type { Project, ProjectConfig } from "@/domain/types";
import { formatJson, parseJsonObject, validateProjectConfig } from "@/domain/validation";
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

const props = defineProps<{
  project: Project;
  active: boolean;
}>();

const emit = defineEmits<{
  dirtyChange: [dirty: boolean];
}>();

const workspace = useWorkspaceStore();
const ui = useUiStore();

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
const savedStamp = ref("");
let applyingAdvanced = false;
const busyConfig = computed(() => workspace.busyKey(`config:${props.project.id}`));
const busyConvention = computed(() => workspace.busyKey(`convention:${props.project.id}`));
const conventions = computed(() => props.project.config.conventions ?? []);
const convPattern = ref("");
const convReason = ref("");
const convScope = ref<"all" | "probe" | "case">("all");
const detectingConfig = ref(false);

async function addConventionEntry(): Promise<void> {
  const pattern = convPattern.value.trim();
  if (!pattern || busyConvention.value) return;
  try {
    await workspace.addConvention(props.project.id, {
      pattern,
      reason: convReason.value.trim() || "项目正常约定",
      scope: convScope.value,
    });
    convPattern.value = "";
    convReason.value = "";
  } catch {
    // store 已提示。
  }
}

async function removeConventionEntry(id: string): Promise<void> {
  if (busyConvention.value) return;
  try {
    await workspace.removeConvention(props.project.id, id);
  } catch {
    // store 已提示。
  }
}

function pickPresent<T extends Record<string, unknown>>(obj: T | undefined): Partial<T> {
  if (!obj) {
    return {};
  }
  const result: Partial<T> = {};
  for (const [key, val] of Object.entries(obj)) {
    if (val !== undefined && val !== null && val !== "") {
      (result as Record<string, unknown>)[key] = val;
    }
  }
  return result;
}

async function detectConfig(): Promise<void> {
  if (detectingConfig.value) {
    return;
  }
  detectingConfig.value = true;
  try {
    const detected = await workspace.detectProjectConfig(props.project.id);
    if (!detected) {
      ui.showToast("未能探测到可自动识别的项目配置。", "info");
      return;
    }
    if (formDirty.value || advancedDirty.value) {
      const ok = await ui.askConfirm({
        title: "用探测结果更新当前草稿？",
        body: "当前表单中有尚未保存的更改，确认要用项目自动探测结果填补或更新配置草稿吗？（不会立即写入磁盘，点击“保存运行配置”后才生效）",
        confirmLabel: "填入草稿",
      });
      if (ok === null) {
        return;
      }
    }
    applyingAdvanced = true;
    const base = configDraft.value;
    configDraft.value = {
      model: { ...base.model, ...pickPresent(detected.model) },
      services: { ...base.services, ...pickPresent(detected.services) },
      l1: { ...base.l1, ...pickPresent(detected.l1) },
      runners: { ...base.runners, ...pickPresent(detected.runners) },
      evidence: { ...base.evidence, ...pickPresent(detected.evidence) },
      link: { ...base.link, ...pickPresent(detected.link) },
      data: { ...base.data, ...pickPresent(detected.data) },
    };
    advanced.value = formatJson(configDraft.value);
    advancedDirty.value = false;
    formDirty.value = true;
    await nextTick();
    ui.showToast("已按项目目录探测并填入建议配置，确认无误后请保存。", "success");
  } catch {
    // store 已提示。
  } finally {
    applyingAdvanced = false;
    detectingConfig.value = false;
  }
}

watch(
  [advancedDirty, formDirty],
  ([adv, form]) => {
    emit("dirtyChange", adv || form);
  },
  { immediate: true },
);

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
    formDirty.value = true;
  },
  { deep: true },
);

watch(
  () => props.project.id,
  () => {
    applyingAdvanced = true;
    configDraft.value = mergeConfig(defaultConfig(), props.project.config);
    advanced.value = formatJson(configDraft.value);
    advancedDirty.value = false;
    formDirty.value = false;
    configError.value = "";
    savedStamp.value = "";
    void nextTick(() => {
      applyingAdvanced = false;
    });
  },
  { immediate: true },
);

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
    await workspace.saveConfig(props.project.id, validated.config);
    advancedDirty.value = false;
    formDirty.value = false;
    advanced.value = formatJson(validated.config);
    savedStamp.value = new Date().toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    });
    ui.showToast("运行配置已保存；旧计划可能在启动时要求重新生成。", "success");
  } catch {
    // store 已提示。
  }
}
</script>

<template>
  <section
    v-show="active"
    class="settings-section"
    id="settings-config"
    role="tabpanel"
    aria-labelledby="settings-tab-settings-config"
  >
    <div class="settings-head">
      <div>
        <h2 class="settings-title">运行配置</h2>
        <p class="settings-desc">服务、项目级检查、运行器、证据与链路；不接受凭据字段。</p>
      </div>
    </div>
    <div class="form-grid">
      <h3 class="settings-subtitle">构建与项目级检查</h3>
      <FormField id="config-build" label="构建命令">
        <input id="config-build" v-model="configDraft.l1.build" type="text" placeholder="例如 pnpm build" />
      </FormField>
      <FormField id="config-entry" label="入口模块" optional="逗号分隔">
        <input id="config-entry" v-model="configDraft.l1.entry" type="text" placeholder="留空时自动检测" />
      </FormField>
      <FormField id="config-smoke" label="最小测试命令">
        <input
          id="config-smoke"
          v-model="configDraft.l1.smokeCommand"
          type="text"
          placeholder="留空时自动检测"
        />
      </FormField>
      <h3 class="settings-subtitle">运行器</h3>
      <FormField id="config-test" label="测试命令">
        <input id="config-test" v-model="configDraft.runners.test" type="text" placeholder="例如 pytest" />
      </FormField>
      <h3 class="settings-subtitle">服务</h3>
      <FormField id="config-health" label="服务健康地址">
        <input
          id="config-health"
          v-model="configDraft.services.healthUrl"
          type="text"
          placeholder="http://127.0.0.1:8000/health"
        />
      </FormField>
      <FormField id="config-base" label="服务地址">
        <input
          id="config-base"
          v-model="configDraft.services.baseUrl"
          type="text"
          placeholder="http://127.0.0.1:8000"
        />
      </FormField>
      <h3 class="settings-subtitle">证据</h3>
      <FormField id="config-evidence-file" label="单文件证据上限（MB）">
        <input
          id="config-evidence-file"
          v-model.number="configDraft.evidence.singleFileMb"
          type="number"
          min="1"
        />
      </FormField>
      <FormField id="config-evidence-media" label="单次媒体上限（MB）">
        <input
          id="config-evidence-media"
          v-model.number="configDraft.evidence.mediaMb"
          type="number"
          min="1"
        />
      </FormField>
      <h3 class="settings-subtitle">链路</h3>
      <FormField id="config-log" label="链路日志路径">
        <input id="config-log" v-model="configDraft.link.logPath" type="text" placeholder="留空表示未配置" />
      </FormField>
      <h3 class="settings-subtitle">模型</h3>
      <FormField id="config-model" label="模型名">
        <input id="config-model" v-model="configDraft.model.name" type="text" placeholder="留空表示未接入" />
      </FormField>
    </div>
    <FormField
      id="config-json"
      label="高级 JSON"
      optional="只允许白名单段"
      :error="configError"
      v-slot="{ inputId, describedBy, invalid }"
    >
      <textarea
        :id="inputId"
        v-model="advanced"
        class="code-input"
        rows="8"
        :aria-invalid="invalid"
        :aria-describedby="describedBy"
        @input="advancedDirty = true; configError = ''"
      />
    </FormField>
    <div class="form-actions settings-savebar">
      <button
        v-if="workspace.mode === 'host'"
        class="btn btn-ghost"
        type="button"
        :disabled="detectingConfig || busyConfig"
        :aria-busy="detectingConfig"
        @click="detectConfig"
      >
        {{ detectingConfig ? "正在探测…" : "自动探测项目配置" }}
      </button>
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
      <span v-if="savedStamp" class="save-stamp">已于 {{ savedStamp }} 保存</span>
    </div>

    <div class="conventions-manager">
      <div class="section-head">
        <h3 class="settings-subtitle">项目错题本（正常约定）</h3>
        <span class="section-note">{{ conventions.length }} 条 · 自动合并仓库 .aitest.json</span>
      </div>
      <p class="doc-note">
        登记属于项目预期行为的规则，硬探针与失败裁判在命中时会自动降噪豁免，避免反复误报。
      </p>
      <div v-if="conventions.length" class="conventions-list">
        <div v-for="entry in conventions" :key="entry.id" class="convention-row">
          <div class="convention-main">
            <code class="mono">{{ entry.pattern }}</code>
            <span class="badge is-neutral">{{ entry.scope }}</span>
            <span class="muted">{{ entry.reason }}</span>
          </div>
          <button
            class="link-btn"
            type="button"
            :disabled="busyConvention"
            @click="removeConventionEntry(entry.id)"
          >
            移除
          </button>
        </div>
      </div>
      <p v-else class="doc-note">还没有登记项目正常约定。</p>
      <div class="convention-add-row">
        <input
          v-model="convPattern"
          type="text"
          placeholder="匹配关键文本，如 /api/legacy 或 429"
          aria-label="约定匹配关键文本"
        />
        <input
          v-model="convReason"
          type="text"
          placeholder="说明理由（如：外部网关托管路由）"
          aria-label="约定正常理由"
        />
        <select v-model="convScope" aria-label="约定适用范围">
          <option value="all">全部 (all)</option>
          <option value="probe">仅探针 (probe)</option>
          <option value="case">仅裁判 (case)</option>
        </select>
        <button
          class="btn btn-sm"
          type="button"
          :disabled="!convPattern.trim() || busyConvention"
          @click="addConventionEntry"
        >
          新增约定
        </button>
      </div>
    </div>
  </section>
</template>
