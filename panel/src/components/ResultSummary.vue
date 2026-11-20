<script setup lang="ts">
import { computed } from "vue";

import { conclusionForRun, displayItemOutcome } from "@/domain/derive";
import { VERDICT_LABEL } from "@/domain/labels";
import type { CaseResult, Run, Task } from "@/domain/types";
import CoverageRuler from "./CoverageRuler.vue";

const props = defineProps<{
  run: Run;
  task: Task;
}>();

const emit = defineEmits<{ select: [itemId: string] }>();

const outcomes = computed<Record<string, CaseResult>>(() => {
  const map: Record<string, CaseResult> = {};
  props.run.items.forEach((item) => {
    map[item.itemId] = displayItemOutcome(props.run, item.itemId);
  });
  return map;
});
const conclusion = computed(() => props.run.conclusion || conclusionForRun(props.run));
const unselected = computed(() =>
  Math.max(0, props.run.counts.applicable - props.run.counts.selected),
);
const guidance = computed(() => {
  if (props.run.verdict === "failed") {
    return "先处理失败项；修复后可只重跑未通过项，或调整范围后重新生成计划。";
  }
  if (props.run.verdict === "passed") {
    return "";
  }
  return "补齐检查条件后重跑；报告会保留这次的分母。";
});
</script>

<template>
  <section class="result-head" aria-labelledby="result-verdict">
    <div>
      <p class="page-kicker">本次结论</p>
      <p id="result-verdict" class="result-verdict" :class="`is-${run.verdict}`">
        {{ VERDICT_LABEL[run.verdict] }}
      </p>
      <p class="page-subtitle">
        本次 <strong class="num">{{ run.counts.selected }}</strong> 项 / 共
        <strong class="num">{{ run.counts.applicable }}</strong> 项
        <span class="muted">· 未纳入 {{ unselected }} 项</span>
      </p>
    </div>
    <div class="result-counts" role="group" aria-label="本次结果计数">
      <div class="result-count is-passed">
        <strong>{{ run.counts.passed }}</strong>
        <span>通过</span>
      </div>
      <div class="result-count is-failed">
        <strong>{{ run.counts.failed }}</strong>
        <span>失败</span>
      </div>
      <div class="result-count is-incomplete">
        <strong>{{ run.counts.incomplete }}</strong>
        <span>未完成</span>
      </div>
    </div>
  </section>
  <CoverageRuler
    :items="run.items"
    :selected-ids="run.scope.acceptanceItemIds"
    :outcomes="outcomes"
    @select="emit('select', $event)"
  />
  <p class="guidance">{{ conclusion }}</p>
  <p v-if="guidance" class="guidance muted">{{ guidance }}</p>
  <div v-if="run.status === 'cancelled'" class="notice is-old">
    <span>这次运行已取消，已完成的结果仍然有效；停止时正在执行的检查按阻塞记录。</span>
  </div>
  <div v-if="run.status === 'interrupted'" class="notice is-old">
    <span>运行已中断，已完成结果保留；不提供恢复或续跑，也没有生成报告。</span>
  </div>
</template>
