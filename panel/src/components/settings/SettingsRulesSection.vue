<script setup lang="ts">
import { computed, ref, watch } from "vue";

import FormField from "@/components/FormField.vue";
import type { Project, RuleVersion } from "@/domain/types";
import { parseLineList } from "@/domain/validation";
import { downloadText } from "@/services/exportService";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const props = defineProps<{
  project: Project;
  active: boolean;
}>();

const workspace = useWorkspaceStore();
const ui = useUiStore();
const ruleForm = ref({
  name: "",
  version: "",
  steps: "",
  aiRules: "",
  evidence: "",
});
const ruleError = ref("");
const bindingRuleId = ref("");
const savedStamp = ref("");
const ruleImportInput = ref<HTMLInputElement | null>(null);
const busyRule = computed(() => workspace.busyKey(`rule:${props.project.id}`));
const boundRule = computed(
  () => props.project.rules.find((rule) => rule.id === props.project.rulesVersionId) ?? null,
);

watch(
  () => props.project.id,
  () => {
    ruleForm.value = { name: "", version: "", steps: "", aiRules: "", evidence: "" };
    ruleError.value = "";
    savedStamp.value = "";
  },
);

async function saveRule(): Promise<void> {
  if (!ruleForm.value.name.trim() || !ruleForm.value.version.trim()) {
    ruleError.value = "规则名称和版本不能为空。";
    document.querySelector<HTMLInputElement>("#rule-name")?.focus();
    return;
  }
  ruleError.value = "";
  try {
    await workspace.saveRule(props.project.id, {
      name: ruleForm.value.name,
      version: ruleForm.value.version,
      stepsMd: ruleForm.value.steps,
      aiRules: parseLineList(ruleForm.value.aiRules),
      evidenceRequirements: parseLineList(ruleForm.value.evidence),
    });
    ruleForm.value = { name: "", version: "", steps: "", aiRules: "", evidence: "" };
    savedStamp.value = new Date().toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    });
    ui.showToast("规则版本已保存；绑定后新计划才使用它。", "success");
  } catch {
    // store 已提示。
  }
}

async function bindRule(ruleId: string): Promise<void> {
  bindingRuleId.value = ruleId;
  try {
    await workspace.bindRule(props.project.id, ruleId);
    ui.showToast("规则已绑定；旧计划可能在启动时要求重新生成。", "success");
  } catch {
    // store 已提示。
  } finally {
    bindingRuleId.value = "";
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
</script>

<template>
  <section
    v-show="active"
    class="settings-section"
    id="settings-rules"
    role="tabpanel"
    aria-labelledby="settings-tab-settings-rules"
  >
    <div class="settings-head">
      <div>
        <h2 class="settings-title">项目规则</h2>
        <p class="settings-desc">新计划用绑定版本；旧运行保留旧版本。</p>
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
            <li v-for="item in rule.evidenceRequirements" :key="item">证据要求：{{ item }}</li>
          </ul>
        </details>
      </div>
    </div>
    <p v-if="boundRule" class="doc-note">
      当前绑定：{{ boundRule.name }} v{{ boundRule.version }}。绑定变化可能在启动时要求重新生成计划。
    </p>
    <div class="divider"></div>
    <h3 class="settings-title">新建规则版本</h3>
    <form class="settings-form" novalidate @submit.prevent="saveRule">
      <div class="form-grid">
        <FormField
          id="rule-name"
          label="名称"
          required
          :error="!ruleForm.name.trim() && ruleError ? ruleError : ''"
          v-slot="{ inputId, describedBy, invalid }"
        >
          <input
            :id="inputId"
            v-model="ruleForm.name"
            type="text"
            maxlength="40"
            :aria-invalid="invalid"
            :aria-describedby="describedBy"
            @input="ruleError = ''"
          />
        </FormField>
        <FormField
          id="rule-version"
          label="版本"
          required
          :error="ruleForm.name.trim() && !ruleForm.version.trim() && ruleError ? ruleError : ''"
          v-slot="{ inputId, describedBy, invalid }"
        >
          <input
            :id="inputId"
            v-model="ruleForm.version"
            type="text"
            maxlength="20"
            :aria-invalid="invalid"
            :aria-describedby="describedBy"
            @input="ruleError = ''"
          />
        </FormField>
      </div>
      <FormField id="rule-steps" label="步骤">
        <textarea
          id="rule-steps"
          v-model="ruleForm.steps"
          rows="4"
          placeholder="按项目 README 与启动脚本执行主流程检查。"
        />
      </FormField>
      <div class="form-grid">
        <FormField id="rule-ai" label="AI 规则" optional="一行一条">
          <textarea id="rule-ai" v-model="ruleForm.aiRules" rows="3" />
        </FormField>
        <FormField id="rule-evidence" label="证据要求" optional="一行一条">
          <textarea id="rule-evidence" v-model="ruleForm.evidence" rows="3" />
        </FormField>
      </div>
      <div class="form-actions">
        <button class="btn btn-primary" type="submit" :disabled="busyRule" :aria-busy="busyRule">
          {{ busyRule ? "正在保存…" : "保存规则版本" }}
        </button>
        <span v-if="savedStamp" class="save-stamp">已于 {{ savedStamp }} 保存</span>
      </div>
    </form>
  </section>
</template>
