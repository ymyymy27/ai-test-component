<script setup lang="ts">
import { computed } from "vue";

import { LAYER_LABEL, statusText } from "@/domain/labels";
import type { CaseResult, EvidenceRef, RunCase } from "@/domain/types";
import Icon from "./Icon.vue";
import StatusMark from "./StatusMark.vue";

const props = withDefaults(
  defineProps<{
    testCase: RunCase;
    result: CaseResult;
    allowManual?: boolean;
    manualBusy?: boolean;
  }>(),
  { allowManual: false, manualBusy: false },
);

const emit = defineEmits<{
  manual: [caseId: string];
  showEvidence: [evidence: EvidenceRef];
}>();

const bad = computed(() => ["failed", "blocked", "error"].includes(props.result));
const canRecordManual = computed(
  () =>
    props.allowManual &&
    props.testCase.layer === "L3" &&
    ["manual_pending", "passed", "failed"].includes(props.result),
);
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
    <div v-if="testCase.evidence.length" class="evidence-row">
      <button
        v-for="evidence in testCase.evidence"
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
    <ol v-if="testCase.manualSteps.length" class="list-plain manual-steps">
      <li v-for="(step, index) in testCase.manualSteps" :key="index">
        {{ step.step }} · {{ statusText(step.result) }} ·
        {{ step.note || "无说明" }}
      </li>
    </ol>
    <div v-if="canRecordManual" class="case-actions">
      <button
        class="btn btn-sm"
        type="button"
        :disabled="manualBusy"
        :aria-busy="manualBusy"
        @click="emit('manual', testCase.id)"
      >
        {{ result === "manual_pending" ? "记录人工结果" : "修改人工结果" }}
      </button>
    </div>
  </article>
</template>
