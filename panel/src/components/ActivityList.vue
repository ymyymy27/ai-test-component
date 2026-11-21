<script setup lang="ts">
import { computed } from "vue";

import { durationText } from "@/domain/ids";
import { VERDICT_LABEL } from "@/domain/labels";
import type { Run, Task } from "@/domain/types";
import StatusMark from "./StatusMark.vue";

const props = defineProps<{
  runs: Run[];
  tasks: Task[];
}>();

const emit = defineEmits<{ open: [runId: string] }>();

const rows = computed(() =>
  props.runs.slice(0, 4).map((run) => {
    const task = props.tasks.find((entry) => entry.id === run.taskId) ?? null;
    const plan = task?.plans.find((entry) => entry.id === run.planId) ?? null;
    return {
      run,
      taskTitle: task?.title ?? "任务已归档",
      planLabel: plan ? `计划 ${plan.no} · 第 ${plan.revision} 版` : "计划快照缺失",
    };
  }),
);
</script>

<template>
  <div v-if="rows.length" class="activity-list">
    <button
      v-for="row in rows"
      :key="row.run.id"
      class="activity-row"
      type="button"
      @click="emit('open', row.run.id)"
    >
      <StatusMark
        :result="row.run.status === 'interrupted' ? 'blocked' : row.run.verdict"
        :label="row.run.status === 'interrupted' ? '中断' : VERDICT_LABEL[row.run.verdict]"
      />
      <span>
        <span class="row-title">{{ row.taskTitle }}</span>
        <span class="row-meta">
          <span>{{ row.planLabel }}</span>
          <span>{{ new Date(row.run.startedAt).toLocaleString("zh-CN") }}</span>
        </span>
      </span>
      <span class="row-side">
        {{ row.run.counts.passed }} 通过 · {{ row.run.counts.failed }} 失败
      </span>
      <span class="row-side">{{ durationText(row.run.elapsedMs / 1000) }}</span>
    </button>
  </div>
  <p v-else class="doc-note">还没有运行记录。完成一次测试后，结果与报告会留在这里。</p>
</template>
