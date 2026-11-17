<script setup lang="ts">
import { computed } from "vue";

import { CONFIDENCE_LABEL } from "@/api/labels";
import type { Analysis } from "@/api/types";
import AppButton from "./AppButton.vue";

const props = withDefaults(
  defineProps<{
    analysis?: Analysis;
    loading?: boolean;
    canGenerate?: boolean;
    /** No failed or stalled cases, so there is nothing to analyze. */
    notNeeded?: boolean;
  }>(),
  { analysis: undefined, loading: false, canGenerate: false, notNeeded: false },
);

defineEmits<{ generate: [] }>();

const acceptable = computed(
  () =>
    props.analysis?.status === "generated" &&
    Boolean(props.analysis.unverified && props.analysis.unverified.length > 0),
);

const REASON_TEXT: Record<string, string> = {
  no_failures: "本次没有失败，不需要生成分析。",
  model_missing: "模型未配置或不可用，分析未生成。",
  model_invalid: "模型输出不合格，已隐藏。",
  model_error: "模型调用失败，可稍后重试。",
};

const reasonText = computed(() => REASON_TEXT[props.analysis?.reason ?? ""] ?? "分析未生成。");

const canRetry = computed(
  () => props.canGenerate && props.analysis?.reason !== "no_failures",
);

const state = computed(() => {
  if (!props.analysis) {
    if (props.notNeeded) {
      return "not_needed";
    }
    return props.canGenerate ? "missing" : "unavailable";
  }
  if (props.analysis.status === "not_generated") {
    return "reason";
  }
  if (props.analysis.status === "invalid") {
    return "invalid";
  }
  return acceptable.value ? "ready" : "invalid";
});

const invalidText = computed(() =>
  props.analysis?.reason === "model_invalid"
    ? "模型输出不合格，已隐藏。"
    : "分析不合格，已隐藏。缺少“未验证范围”的分析不能作为结论依据。",
);
</script>

<template>
  <section class="analysis" aria-label="AI 失败分析">
    <div class="analysis-head">
      <h2>AI 失败分析</h2>
      <span class="hint">只解释事实，不改变结论</span>
    </div>

    <div v-if="state === 'missing'" class="state">
      <p class="state-text">还没有生成失败分析。</p>
      <AppButton size="sm" :loading="loading" @click="$emit('generate')">
        生成失败分析
      </AppButton>
    </div>

    <div v-else-if="state === 'reason'" class="state">
      <p class="state-text">{{ reasonText }}</p>
      <AppButton
        v-if="canRetry"
        size="sm"
        :loading="loading"
        @click="$emit('generate')"
      >
        生成失败分析
      </AppButton>
    </div>

    <div v-else-if="state === 'not_needed'" class="state">
      <p class="state-text">本次没有失败，不需要生成分析。</p>
    </div>

    <div v-else-if="state === 'unavailable'" class="state">
      <p class="state-text">分析未生成。</p>
    </div>

    <div v-else-if="state === 'invalid'" class="state">
      <p class="state-text">{{ invalidText }}</p>
    </div>

    <div v-else class="segments">
      <div class="segment">
        <h3>观察到的事实</h3>
        <p>{{ analysis?.observed || "未记录" }}</p>
      </div>
      <div class="segment">
        <h3>证据</h3>
        <ul v-if="analysis?.evidence_refs?.length" class="refs">
          <li v-for="ref in analysis.evidence_refs" :key="ref" class="mono">{{ ref }}</li>
        </ul>
        <p v-else class="hint">没有附证据引用。</p>
      </div>
      <div class="segment">
        <h3>候选原因</h3>
        <ul v-if="analysis?.candidate_causes?.length" class="causes">
          <li v-for="(item, index) in analysis.candidate_causes" :key="index">
            <span>{{ item.cause }}</span>
            <span v-if="item.confidence" class="confidence">
              {{ CONFIDENCE_LABEL[item.confidence] ?? item.confidence }}
            </span>
          </li>
        </ul>
        <p v-else class="hint">没有给出候选原因。</p>
      </div>
      <div class="segment">
        <h3>未验证与下一步</h3>
        <ul class="plain">
          <li v-for="item in analysis?.unverified ?? []" :key="item">
            <span class="tag">未验证</span>{{ item }}
          </li>
          <li v-for="item in analysis?.next_steps ?? []" :key="item">
            <span class="tag">下一步</span>{{ item }}
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>

<style scoped>
.analysis {
  background: none;
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  padding: var(--space-4) 0 0;
}

.analysis-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.analysis-head h2 {
  font-size: var(--fs-14);
}

.state {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3);
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
}

.state-text {
  font-size: var(--fs-12);
  color: var(--text-2);
}

.segments {
  display: grid;
  gap: var(--space-4);
}

.segment h3 {
  font-size: var(--fs-12);
  color: var(--text-3);
  font-weight: 500;
  margin-bottom: 5px;
}

.segment p {
  font-size: var(--fs-13);
  color: var(--text-2);
  line-height: 1.6;
  word-break: break-word;
}

.refs,
.causes,
.plain {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.refs li {
  color: var(--text-3);
  word-break: break-all;
}

.causes li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 5px 0;
  border-bottom: 1px dashed var(--line);
  font-size: var(--fs-13);
  color: var(--text-2);
}

.causes li:last-child {
  border-bottom: 0;
}

.confidence {
  flex: none;
  font-size: var(--fs-12);
  color: var(--text-3);
}

.plain li {
  font-size: var(--fs-12);
  color: var(--text-2);
  line-height: 1.6;
}

.tag {
  display: inline-block;
  margin-right: 6px;
  padding: 0 5px;
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  font-size: 11px;
  color: var(--text-3);
}
</style>
