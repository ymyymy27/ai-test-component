<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import EmptyState from "@/components/EmptyState.vue";
import PageHeader from "@/components/PageHeader.vue";
import RunHistoryDialog from "@/components/RunHistoryDialog.vue";
import StatusMark from "@/components/StatusMark.vue";
import { latestReportForRun } from "@/domain/derive";
import { durationText } from "@/domain/ids";
import { RUN_STATUS_LABEL, VERDICT_LABEL } from "@/domain/labels";
import type { Run, Task } from "@/domain/types";
import { useWorkspaceStore } from "@/stores/workspace";

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const projectId = computed(() => String(route.params.projectId ?? ""));
const project = computed(() => workspace.currentProject);
const tasks = computed(() => project.value?.tasks.filter((task) => task.stage !== "fresh") ?? []);
const runs = computed(() => workspace.projectRuns);
const taskFilter = ref("all");
const resultFilter = ref("all");
const selected = ref<Run | null>(null);
const dialogOpen = ref(false);

const filtered = computed(() =>
  runs.value
    .filter((run) => taskFilter.value === "all" || run.taskId === taskFilter.value)
    .filter((run) => {
      if (resultFilter.value === "failed") return run.verdict === "failed";
      if (resultFilter.value === "passed") return run.verdict === "passed";
      if (resultFilter.value === "running") return run.status === "running";
      if (resultFilter.value === "interrupted") return run.status === "interrupted";
      if (resultFilter.value === "cancelled") return run.status === "cancelled";
      return true;
    }),
);

function groupOf(run: Run): "今天" | "昨天" | "更早" {
  const date = new Date(run.startedAt);
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const time = date.getTime();
  if (time >= start) return "今天";
  if (time >= start - 24 * 60 * 60 * 1000) return "昨天";
  return "更早";
}

const groups = computed(() =>
  (["今天", "昨天", "更早"] as const)
    .map((label) => ({
      label,
      runs: filtered.value.filter((run) => groupOf(run) === label),
    }))
    .filter((group) => group.runs.length > 0),
);

function taskFor(run: Run): Task | null {
  return tasks.value.find((task) => task.id === run.taskId) ?? null;
}

function planLabel(run: Run): string {
  const task = taskFor(run);
  const plan = task?.plans.find((entry) => entry.id === run.planId);
  return plan ? `计划 ${plan.no} · 第 ${plan.revision} 版` : "计划快照缺失";
}

function openRun(run: Run): void {
  selected.value = run;
  dialogOpen.value = true;
  void router.replace({ query: { ...route.query, run: run.id } });
}

function openReport(reportId: string): void {
  void router.push(`/projects/${projectId.value}/reports/${reportId}`);
}

watch(
  () => route.query.run,
  (runId) => {
    if (typeof runId !== "string") {
      return;
    }
    const target = runs.value.find((run) => run.id === runId);
    if (target) {
      selected.value = target;
      dialogOpen.value = true;
    }
  },
  { immediate: true },
);

watch(dialogOpen, (open) => {
  if (!open && route.query.run) {
    const query = { ...route.query };
    delete query.run;
    void router.replace({ query });
  }
});
</script>

<template>
  <div v-if="!project" class="page">
    <EmptyState title="项目不可用" text="回到项目概览重新选择项目。" />
  </div>
  <div v-else class="page">
    <PageHeader
      kicker="项目"
      title="运行记录"
      :subtitle="`${project.name} · ${runs.length} 次运行 · 记录只读`"
    />

    <div class="toolbar">
      <label class="scope-filter">
        <span>任务</span>
        <select v-model="taskFilter" aria-label="按任务筛选历史">
          <option value="all">全部任务（{{ runs.length }}）</option>
          <option v-for="task in tasks" :key="task.id" :value="task.id">
            {{ task.title }}（{{ runs.filter((run) => run.taskId === task.id).length }}）
          </option>
        </select>
      </label>
      <label class="scope-filter">
        <span>结果</span>
        <select v-model="resultFilter" aria-label="按结果筛选历史">
          <option value="all">全部结果</option>
          <option value="failed">失败</option>
          <option value="passed">通过</option>
          <option value="running">运行中</option>
          <option value="cancelled">已取消</option>
          <option value="interrupted">中断</option>
        </select>
      </label>
      <span class="section-note">{{ filtered.length }} 次运行</span>
    </div>

    <div v-if="groups.length" class="history-groups">
      <section v-for="group in groups" :key="group.label" class="history-group">
        <div class="section-head">
          <h2 class="section-title">{{ group.label }}</h2>
          <span class="section-note">{{ group.runs.length }} 次</span>
        </div>
        <div class="history-list">
          <button
            v-for="run in group.runs"
            :key="run.id"
            class="run-row"
            type="button"
            @click="openRun(run)"
          >
            <StatusMark :result="run.status === 'interrupted' ? 'blocked' : run.verdict" />
            <span>
              <span class="row-title">
                {{ taskFor(run)?.title ?? "任务已归档" }}
                <span
                  v-if="taskFor(run)?.currentRunId === run.id && taskFor(run)?.stage === 'result'"
                  class="badge is-accent"
                  >当前</span
                >
              </span>
              <span class="row-meta">
                <span>{{ planLabel(run) }}</span>
                <span>{{ new Date(run.startedAt).toLocaleString("zh-CN") }}</span>
                <span>{{ RUN_STATUS_LABEL[run.status] }} · {{ VERDICT_LABEL[run.verdict] }}</span>
              </span>
            </span>
            <span class="row-side">
              {{ run.counts.passed }} 通过 · {{ run.counts.failed }} 失败 ·
              {{ run.counts.incomplete }} 未完成
            </span>
            <span class="row-side">{{ durationText(run.elapsedMs / 1000) }}</span>
          </button>
        </div>
      </section>
    </div>
    <EmptyState
      v-else
      title="还没有运行记录"
      :text="
        runs.length === 0
          ? '完成一次测试后，运行记录、报告与证据都会保留在这里。'
          : '这个筛选下没有记录；切回全部任务或全部结果。'
      "
    >
      <button
        class="btn btn-sm"
        type="button"
        @click="router.push(`/projects/${projectId}/tasks/new`)"
      >
        开始测试
      </button>
    </EmptyState>

    <p class="doc-note">
      历史按任务归档；每次运行保留启动时的计划与任务快照，不混用当前任务文字。中断的运行没有报告。
    </p>

    <RunHistoryDialog
      v-model="dialogOpen"
      :run="selected"
      :task="selected ? taskFor(selected) : null"
      :report="selected && project ? latestReportForRun(project, selected.id) : null"
      @open-report="openReport"
    />
  </div>
</template>
