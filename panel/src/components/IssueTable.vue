<script setup lang="ts">
import { computed } from "vue";

import { ISSUE_STATUS_LABEL } from "@/domain/labels";
import type { Issue, Task } from "@/domain/types";
import Icon from "./Icon.vue";

const props = defineProps<{
  issues: Issue[];
  tasks: Task[];
}>();

const emit = defineEmits<{ open: [issueId: string] }>();

const rows = computed(() =>
  props.issues.map((issue) => {
    const task = props.tasks.find((entry) => entry.id === issue.taskId);
    const plan = task?.plans.find((entry) => entry.id === issue.planId);
    const run = task?.runs.find((entry) => entry.id === issue.runId);
    return {
      issue,
      taskTitle: task?.title ?? "任务已归档",
      planLabel: plan ? `计划 ${plan.no} · 第 ${plan.revision} 版` : "计划已归档",
      runLabel: issue.runId
        ? run
          ? new Date(run.startedAt).toLocaleString("zh-CN")
          : "运行已归档"
        : "未绑定运行",
      lastAt: issue.occurrences.length
        ? new Date(issue.occurrences[issue.occurrences.length - 1]?.time ?? "").toLocaleString(
            "zh-CN",
          )
        : "人工登记 · 未发生",
    };
  }),
);

function statusTone(status: Issue["status"]): string {
  if (status === "closed") return "is-neutral";
  if (status === "in_progress") return "is-accent";
  if (status === "retest") return "is-neutral";
  return "is-warn";
}
</script>

<template>
  <div v-if="rows.length" class="issue-table">
    <button
      v-for="row in rows"
      :key="row.issue.id"
      class="issue-row"
      type="button"
      @click="emit('open', row.issue.id)"
    >
      <span
        class="severity"
        :class="{ 'is-critical': row.issue.severity === 'P0', 'is-high': row.issue.severity === 'P1' }"
      >
        {{ row.issue.severity }}
      </span>
      <span>
        <span class="row-title" :title="row.issue.title">{{ row.issue.title }}</span>
        <span class="row-meta">
          <span>{{ row.taskTitle }}</span>
          <span>{{ row.planLabel }}</span>
          <span>{{ row.runLabel }}</span>
          <span>{{ row.lastAt }}</span>
          <span>发生 {{ row.issue.occurrences.length }} 次</span>
          <span v-if="row.issue.unstable" class="badge is-warn">疑似不稳定</span>
        </span>
      </span>
      <span class="badge" :class="statusTone(row.issue.status)">
        {{ ISSUE_STATUS_LABEL[row.issue.status] }}
      </span>
      <Icon class="issue-chevron" name="chevron-right" :size="13" />
    </button>
  </div>
  <p v-else class="doc-note">没有符合筛选条件的问题。</p>
</template>
