<script setup lang="ts">
import { computed } from "vue";

import { currentPlan, currentRun } from "@/domain/derive";
import { stageLabel } from "@/domain/stateMachine";
import type { Task } from "@/domain/types";

const props = defineProps<{
  task: Task;
  openIssueCount: number;
  runningElsewhere: boolean;
}>();

const emit = defineEmits<{ open: [] }>();

const plan = computed(() => currentPlan(props.task));
const run = computed(() => currentRun(props.task));
const isDraft = computed(() => props.task.stage === "fresh");
const title = computed(() => (isDraft.value ? "新任务 · 待确认目标" : props.task.title));
const state = computed(() => {
  if (isDraft.value) {
    return "待定目标";
  }
  if (props.task.stage === "result" && run.value) {
    return run.value.verdict === "passed"
      ? "通过"
      : run.value.verdict === "failed"
        ? "失败"
        : "未完成";
  }
  return stageLabel(props.task.stage);
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
  parts.push(`${props.openIssueCount} 个未关闭问题`);
  if (run.value) {
    parts.push(`最近运行 ${new Date(run.value.startedAt).toLocaleString("zh-CN")}`);
  }
  if (props.runningElsewhere) {
    parts.push("其他任务运行中，暂时不能切换");
  }
  return parts.join(" · ");
});
</script>

<template>
  <div class="current-task-strip">
    <span class="side-status-dot" :class="isDraft ? 'is-warn' : 'is-ok'" aria-hidden="true"></span>
    <span class="current-task-main">
      <span class="analysis-strip-title">{{ title }}</span>
      <span class="analysis-strip-text">{{ meta }}</span>
    </span>
    <span class="current-task-state">{{ state }}</span>
    <button class="btn btn-sm" type="button" @click="emit('open')">{{ action }}</button>
  </div>
</template>
