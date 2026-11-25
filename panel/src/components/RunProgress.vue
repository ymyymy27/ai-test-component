<script setup lang="ts">
import { computed } from "vue";

import { durationText } from "@/domain/ids";
import type { Run } from "@/domain/types";

const props = defineProps<{
  run: Run;
  elapsedMs: number;
}>();

const total = computed(() => props.run.cases.length);
const done = computed(
  () =>
    props.run.cases.filter((testCase) => !["not_run", "manual_pending"].includes(testCase.result))
      .length,
);
const ratio = computed(() => (total.value > 0 ? done.value / total.value : 0));
const current = computed(() => {
  const byId = props.run.cases.find((testCase) => testCase.id === props.run.currentCaseId);
  return byId ?? props.run.cases.find((testCase) => testCase.result === "not_run") ?? null;
});
const currentItem = computed(() =>
  current.value
    ? (props.run.items.find((item) => item.itemId === current.value?.itemId) ?? null)
    : null,
);
</script>

<template>
  <section class="surface-strip run-progress" aria-label="运行进度">
    <div class="row-between">
      <div>
        <p class="page-kicker">运行中</p>
        <p class="page-subtitle">
          已完成 <strong class="num">{{ done }}</strong> /
          <strong class="num">{{ total }}</strong> 条用例检查 · {{ durationText(elapsedMs / 1000) }}
        </p>
      </div>
      <div class="run-progress-badges">
        <span v-if="run.resumedFromRunId" class="badge is-neutral">断点续跑</span>
        <span v-if="run.tokenUsage?.totalTokens" class="badge is-neutral">
          Token {{ run.tokenUsage.totalTokens }}
        </span>
        <span v-if="run.simulated" class="badge is-accent">本地模拟</span>
      </div>
    </div>
    <div
      v-if="total > 0"
      class="progress"
      role="progressbar"
      aria-label="检查完成进度"
      aria-valuemin="0"
      :aria-valuemax="total"
      :aria-valuenow="done"
      :aria-valuetext="`${done} / ${total} 条检查`"
    >
      <div class="progress-track">
        <div class="progress-fill" :style="{ transform: `scaleX(${ratio})` }"></div>
      </div>
      <span class="progress-text">{{ done }} / {{ total }}</span>
    </div>
    <p v-else class="field-hint">这次运行没有可执行的用例检查；项目级检查单独记录。</p>
    <div class="current-case">
      <span class="visually-hidden" aria-live="polite">
        {{ current ? `正在执行 ${current.title}` : "正在收尾" }}
      </span>
      <span class="current-case-label">正在执行</span>
      <span class="current-case-title">{{ current?.title || "正在收尾" }}</span>
      <span class="current-case-meta">
        {{
          current
            ? `${current.method}${currentItem ? ` · ${currentItem.no} ${currentItem.text}` : ""}`
            : "等待运行结束"
        }}
      </span>
    </div>
  </section>
</template>
