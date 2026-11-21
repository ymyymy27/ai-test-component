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
const queryValue = (key: string): string =>
  typeof route.query[key] === "string" ? String(route.query[key]) : "all";
const taskFilter = ref(queryValue("task"));
const resultFilter = ref(queryValue("result"));
const selected = ref<Run | null>(null);
const dialogOpen = ref(false);
const limit = ref(40);
let openedByPush = false;

function matchesResult(run: Run): boolean {
  const value = resultFilter.value;
  if (["passed", "failed", "incomplete", "not_applicable"].includes(value)) {
    return run.verdict === value;
  }
  if (value === "execution_error") return run.status === "execution_error";
  if (["running", "interrupted", "cancelled"].includes(value)) {
    return run.status === value;
  }
  return true;
}

const resultScoped = computed(() => runs.value.filter(matchesResult));
const taskRunCounts = computed(() => {
  const counts = new Map<string, number>();
  resultScoped.value.forEach((run) => {
    counts.set(run.taskId, (counts.get(run.taskId) ?? 0) + 1);
  });
  return counts;
});
const filtered = computed(() =>
  resultScoped.value.filter((run) => taskFilter.value === "all" || run.taskId === taskFilter.value),
);
const visibleRuns = computed(() => filtered.value.slice(0, limit.value));

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
      runs: visibleRuns.value.filter((run) => groupOf(run) === label),
    }))
    .filter((group) => group.runs.length > 0),
);

watch(
  [taskFilter, resultFilter],
  () => {
    limit.value = 40;
    const query = { ...route.query };
    if (taskFilter.value === "all") {
      delete query.task;
    } else {
      query.task = taskFilter.value;
    }
    if (resultFilter.value === "all") {
      delete query.result;
    } else {
      query.result = resultFilter.value;
    }
    void router.replace({ query });
  },
);

function statusLabel(run: Run): string {
  return run.status === "completed" ? VERDICT_LABEL[run.verdict] : RUN_STATUS_LABEL[run.status];
}

function statusTone(run: Run): "blocked" | "error" | "not_run" | Run["verdict"] {
  if (run.status === "interrupted") return "blocked";
  if (run.status === "execution_error") return "error";
  if (run.status === "cancelled") return "not_run";
  return run.verdict;
}

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
  openedByPush = true;
  void router.push({ query: { ...route.query, run: run.id } });
}

function openRunResult(): void {
  if (!selected.value) {
    return;
  }
  void router.push(
    `/projects/${projectId.value}/tasks/${selected.value.taskId}/result?run=${selected.value.id}`,
  );
}

function clearFilters(): void {
  taskFilter.value = "all";
  resultFilter.value = "all";
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
  if (open || !route.query.run) {
    return;
  }
  if (openedByPush) {
    openedByPush = false;
    router.back();
    return;
  }
  const query = { ...route.query };
  delete query.run;
  void router.replace({ query });
});
</script>

<template>
  <div v-if="!project" class="page">
    <EmptyState title="项目不可用" text="回到项目概览重新选择项目。" />
  </div>
  <div v-else class="page">
    <PageHeader
      title="运行记录"
      :subtitle="`${project.name} · ${runs.length} 次运行 · 记录只读`"
    />

    <div class="toolbar">
      <label class="scope-filter">
        <span>任务</span>
        <select v-model="taskFilter" aria-label="按任务筛选历史">
          <option value="all">全部任务（{{ tasks.length }} 个）</option>
          <option v-for="task in tasks" :key="task.id" :value="task.id">
            {{ task.title }}（{{ taskRunCounts.get(task.id) ?? 0 }} 次运行）
          </option>
        </select>
      </label>
      <label class="scope-filter">
        <span>结果</span>
        <select v-model="resultFilter" aria-label="按结果筛选历史">
          <option value="all">全部结果</option>
          <optgroup label="结论">
            <option value="passed">通过</option>
            <option value="failed">失败</option>
            <option value="incomplete">未完成</option>
            <option value="not_applicable">不适用</option>
          </optgroup>
          <optgroup label="状态">
            <option value="running">运行中</option>
            <option value="cancelled">已取消</option>
            <option value="interrupted">中断</option>
            <option value="execution_error">执行出错</option>
          </optgroup>
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
            <StatusMark :result="statusTone(run)" :label="statusLabel(run)" />
            <span>
              <span class="row-title">
                {{ taskFor(run)?.title ?? "任务已归档" }}
                <span
                  v-if="taskFor(run)?.currentRunId === run.id"
                  class="badge is-accent"
                  >当前</span
                >
              </span>
              <span class="row-meta">
                <span>{{ planLabel(run) }}</span>
                <span>{{ new Date(run.startedAt).toLocaleString("zh-CN") }}</span>
              </span>
            </span>
            <span class="row-side">
              <span class="status-text" :class="`is-${statusTone(run)}`">
                {{ statusLabel(run) }}
              </span>
              {{ run.counts.selected }}/{{ run.counts.applicable }} 项 · {{ run.counts.passed }} 通过 ·
              {{ run.counts.failed }} 失败 · {{ run.counts.incomplete }} 未完成
            </span>
            <span class="row-side">{{ durationText(run.elapsedMs / 1000) }}</span>
          </button>
        </div>
      </section>
      <div v-if="filtered.length > visibleRuns.length" class="form-actions">
        <button class="btn btn-sm" type="button" @click="limit += 40">
          再显示 {{ Math.min(40, filtered.length - visibleRuns.length) }} 次
        </button>
      </div>
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
        v-if="runs.length === 0"
        class="btn btn-sm"
        type="button"
        @click="router.push(`/projects/${projectId}/tasks/new`)"
      >
        开始测试
      </button>
      <button v-else class="btn btn-sm" type="button" @click="clearFilters">清空筛选</button>
    </EmptyState>

    <RunHistoryDialog
      v-model="dialogOpen"
      :run="selected"
      :task="selected ? taskFor(selected) : null"
      :report="selected && project ? latestReportForRun(project, selected.id) : null"
      @open-report="openReport"
      @open-result="openRunResult"
    />
  </div>
</template>
