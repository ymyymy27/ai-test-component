<script setup lang="ts">
import { computed } from "vue";

import { currentPlan, currentRun } from "@/domain/derive";
import { VERDICT_LABEL } from "@/domain/labels";
import { taskStageLabel } from "@/domain/stateMachine";
import type { Task } from "@/domain/types";

const props = defineProps<{
  task: Task;
  runningElsewhere: boolean;
}>();

const emit = defineEmits<{ open: [] }>();

const plan = computed(() => currentPlan(props.task));
const run = computed(() => currentRun(props.task));
const isDraft = computed(() => props.task.stage === "fresh");
const title = computed(() => (isDraft.value ? "新任务 · 待确认目标" : props.task.title));
const tone = computed(() => {
  if (isDraft.value) {
    return "warn";
  }
  if (props.task.stage === "running") {
    return "accent";
  }
  if (props.task.stage === "result" && run.value) {
    return run.value.verdict === "failed"
      ? "fail"
      : run.value.verdict === "passed"
        ? "pass"
        : "warn";
  }
  return "neutral";
});
const state = computed(() => {
  if (isDraft.value) {
    return "待定目标";
  }
  if (props.task.stage === "result" && run.value) {
    return VERDICT_LABEL[run.value.verdict];
  }
  return taskStageLabel(props.task);
});
const action = computed(() => {
  if (isDraft.value) {
    return "继续确认";
  }
  switch (props.task.stage) {
    case "scope":
      return "继续选范围";
    case "planned":
      return "确认计划";
    case "running":
      return "查看运行";
    case "result":
      return run.value?.verdict === "failed" ? "查看失败项" : "查看结果";
    default:
      return "继续";
  }
});
const meta = computed(() => {
  const parts: string[] = [];
  if (plan.value) {
    parts.push(`计划 ${plan.value.no} · 第 ${plan.value.revision} 版`);
  } else {
    parts.push("还没有计划");
  }
  if (props.runningElsewhere) {
    parts.push("其他任务运行中，暂时不能切换");
  }
  return parts.join(" · ");
});
</script>

<template>
  <div class="current-task-strip">
    <span class="side-status-dot" :class="`is-${tone}`" aria-hidden="true"></span>
    <span class="current-task-main">
      <span class="analysis-strip-title">{{ title }}</span>
      <span class="analysis-strip-text">{{ meta }}</span>
    </span>
    <span class="current-task-state">{{ state }}</span>
    <button class="btn btn-sm" type="button" @click="emit('open')">{{ action }}</button>
  </div>
</template>
