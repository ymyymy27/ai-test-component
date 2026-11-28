<script setup lang="ts">
import { computed, ref } from "vue";

import { LAYER_LABEL, statusText } from "@/domain/labels";
import type { CaseResult, EvidenceRef, Issue, RunCase } from "@/domain/types";
import Icon from "./Icon.vue";
import StatusMark from "./StatusMark.vue";

const props = withDefaults(
  defineProps<{
    testCase: RunCase;
    result: CaseResult;
    allowManual?: boolean;
    manualBusy?: boolean;
    allowCopyFix?: boolean;
    allowScriptRepair?: boolean;
    repairBusy?: boolean;
    linkedOpenIssue?: Issue | null;
    closeIssueBusy?: boolean;
  }>(),
  {
    allowManual: false,
    manualBusy: false,
    allowCopyFix: false,
    allowScriptRepair: false,
    repairBusy: false,
    linkedOpenIssue: null,
    closeIssueBusy: false,
  },
);

const emit = defineEmits<{
  manual: [caseId: string];
  showEvidence: [evidence: EvidenceRef];
  copyFix: [testCase: RunCase];
  previewRepair: [testCase: RunCase];
  closeLinkedIssue: [payload: { issue: Issue; reason: string }];
}>();

const bad = computed(() => ["failed", "blocked", "error"].includes(props.result));
const canRecordManual = computed(
  () =>
    props.allowManual &&
    props.testCase.layer === "L3" &&
    ["manual_pending", "passed", "failed"].includes(props.result),
);
const canPreviewRepair = computed(
  () =>
    props.allowScriptRepair &&
    bad.value &&
    (props.testCase.method || "").trim().startsWith("script:"),
);
const canCloseLinked = computed(
  () =>
    props.result === "passed" &&
    Boolean(props.linkedOpenIssue) &&
    props.linkedOpenIssue?.status !== "closed",
);
const closeBoxOpen = ref(false);
const closeReason = ref("");

function openCloseBox(): void {
  closeReason.value = `本次复测用例「${props.testCase.title}」已通过，附运行证据关闭`;
  closeBoxOpen.value = true;
}

function submitCloseLinked(): void {
  if (!props.linkedOpenIssue || !closeReason.value.trim()) {
    return;
  }
  emit("closeLinkedIssue", {
    issue: props.linkedOpenIssue,
    reason: closeReason.value.trim(),
  });
  closeBoxOpen.value = false;
}
</script>

<template>
  <article class="case-block">
    <header class="case-head">
      <StatusMark :result="result" />
      <h3 class="case-title">{{ testCase.title }}</h3>
      <span class="badge">{{ LAYER_LABEL[testCase.layer] }}</span>
      <span v-if="!testCase.expectationVerified" class="badge is-warn">预期未核实</span>
      <span class="status-text" :class="`is-${result}`">{{ statusText(result) }}</span>
    </header>
    <p class="case-method">{{ testCase.method || "人工检查" }}</p>
    <div class="case-compare">
      <div class="case-col">
        <span class="case-label">预期</span>
        <span class="case-value">{{ testCase.expected || "未记录" }}</span>
      </div>
      <div class="case-col">
        <span class="case-label">实际</span>
        <span class="case-value" :class="{ 'is-bad': bad }">
          {{ testCase.actual || "未记录" }}
        </span>
      </div>
    </div>
    <p v-if="testCase.message" class="case-message">{{ testCase.message }}</p>
    <details v-if="testCase.scriptRepair?.unifiedDiff" class="details-tech">
      <summary>
        运行期脚本自动修复记录（{{
          testCase.scriptRepair.status === "applied"
            ? "已应用并重跑"
            : testCase.scriptRepair.status === "blocked"
              ? "已被安全守卫拦截"
              : "已跳过"
        }}）
      </summary>
      <p v-if="testCase.scriptRepair.reason" class="field-hint">
        触发原因：{{ testCase.scriptRepair.reason }}
      </p>
      <p v-if="testCase.scriptRepair.guardBlocked?.length" class="field-hint">
        拦截守卫：{{ testCase.scriptRepair.guardBlocked.join("、") }}
      </p>
      <pre class="mono" style="white-space: pre-wrap; font-size: 12px; margin-top: 6px;">{{ testCase.scriptRepair.unifiedDiff }}</pre>
    </details>
    <div v-if="(testCase.evidence?.length ?? 0) > 0" class="evidence-row">
      <button
        v-for="evidence in testCase.evidence ?? []"
        :key="evidence.id"
        class="evidence-chip"
        type="button"
        :class="{ 'is-missing': evidence.missing, 'is-truncated': evidence.truncated }"
        :title="`${evidence.mediaType} · ${evidence.bytes} 字节 · ${evidence.sha256} · ${evidence.sourcePath || '没有来源路径'}`"
        @click="emit('showEvidence', evidence)"
      >
        <Icon name="file" :size="12" />
        {{ evidence.name }}
        <template v-if="evidence.missing"> · 已缺失</template>
        <template v-else-if="evidence.truncated"> · 已截断</template>
      </button>
    </div>
    <ol v-if="(testCase.manualSteps?.length ?? 0) > 0" class="list-plain manual-steps">
      <li v-for="(step, index) in testCase.manualSteps ?? []" :key="index">
        {{ step.step }} · {{ statusText(step.result) }} ·
        {{ step.note || "无说明" }}
      </li>
    </ol>
    <div
      v-if="
        canRecordManual ||
        canPreviewRepair ||
        canCloseLinked ||
        (allowCopyFix && (result === 'failed' || result === 'error' || result === 'blocked'))
      "
      class="case-actions"
    >
      <button
        v-if="canRecordManual"
        class="btn btn-sm"
        type="button"
        :disabled="manualBusy"
        :aria-busy="manualBusy"
        @click="emit('manual', testCase.id)"
      >
        {{ result === "manual_pending" ? "记录人工结果" : "修改人工结果" }}
      </button>
      <button
        v-if="canPreviewRepair"
        class="btn btn-sm"
        type="button"
        :disabled="repairBusy"
        :aria-busy="repairBusy"
        @click="emit('previewRepair', testCase)"
      >
        {{ repairBusy ? "正在生成修复预览…" : "预览 AI 修复" }}
      </button>
      <button
        v-if="allowCopyFix && (result === 'failed' || result === 'error' || result === 'blocked')"
        class="btn btn-ghost btn-sm"
        type="button"
        @click="emit('copyFix', testCase)"
      >
        复制修复上下文
      </button>
      <button
        v-if="canCloseLinked && !closeBoxOpen"
        class="btn btn-sm"
        type="button"
        :disabled="closeIssueBusy"
        :aria-busy="closeIssueBusy"
        @click="openCloseBox"
      >
        关闭关联历史问题
      </button>
    </div>
    <div
      v-if="canCloseLinked && closeBoxOpen && linkedOpenIssue"
      class="row"
      style="gap: 8px; margin-top: 8px; flex-wrap: wrap;"
      aria-label="关闭关联历史问题确认"
    >
      <input
        v-model="closeReason"
        class="input"
        type="text"
        style="flex: 1; min-width: 220px;"
        placeholder="填写关闭理由（将合规流转至待复测后附本运行证据关闭）"
      />
      <button
        class="btn btn-primary btn-sm"
        type="button"
        :disabled="closeIssueBusy || !closeReason.trim()"
        @click="submitCloseLinked"
      >
        {{ closeIssueBusy ? "正在关闭…" : "确认关闭" }}
      </button>
      <button class="btn btn-ghost btn-sm" type="button" @click="closeBoxOpen = false">
        取消
      </button>
    </div>
  </article>
</template>
