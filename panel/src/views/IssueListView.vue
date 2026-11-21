<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import EmptyState from "@/components/EmptyState.vue";
import IssueCreateDialog from "@/components/IssueCreateDialog.vue";
import IssueTable from "@/components/IssueTable.vue";
import PageHeader from "@/components/PageHeader.vue";
import { lastIssueOccurrence } from "@/domain/derive";
import { ISSUE_STATUS_LABEL } from "@/domain/labels";
import type { Issue, IssueStatus } from "@/domain/types";
import type { IssueCreateInput } from "@/repositories/ProjectRepository";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();
const projectId = computed(() => String(route.params.projectId ?? ""));
const project = computed(() => workspace.currentProject);
const tasks = computed(() => project.value?.tasks.filter((task) => task.stage !== "fresh") ?? []);
const createGate = computed(() => {
  if (workspace.mode !== "host") {
    return "";
  }
  const hasRunCase = tasks.value.some((task) =>
    task.runs.some((run) => run.cases.length > 0),
  );
  return hasRunCase ? "" : "还没有可绑定的运行用例；先完成一次运行再登记问题。";
});
const issues = computed(() => workspace.projectIssues);
const queryText = (key: string): string =>
  typeof route.query[key] === "string" ? String(route.query[key]) : "";
const status = ref<"all" | IssueStatus>(
  (["pending", "in_progress", "retest", "closed"] as const).includes(
    queryText("status") as IssueStatus,
  )
    ? (queryText("status") as IssueStatus)
    : "all",
);
const taskFilter = ref(queryText("task") || "all");
const query = ref(queryText("q"));
const severityFilter = ref<"all" | Issue["severity"]>(
  (["P0", "P1", "P2", "P3"] as const).includes(queryText("severity") as Issue["severity"])
    ? (queryText("severity") as Issue["severity"])
    : "all",
);
const unstableOnly = ref(queryText("unstable") === "1");
const sort = ref<"severity" | "recent">(queryText("sort") === "recent" ? "recent" : "severity");
const limit = ref(40);
const createOpen = ref(false);

const statuses: Array<"all" | IssueStatus> = ["all", "pending", "in_progress", "retest", "closed"];

const scoped = computed(() => {
  const text = query.value.trim().toLowerCase();
  return issues.value
    .filter((issue) => taskFilter.value === "all" || issue.taskId === taskFilter.value)
    .filter((issue) => severityFilter.value === "all" || issue.severity === severityFilter.value)
    .filter((issue) => !unstableOnly.value || issue.unstable)
    .filter(
      (issue) =>
        !text ||
        issue.title.toLowerCase().includes(text) ||
        issue.actual.toLowerCase().includes(text) ||
        issue.expected.toLowerCase().includes(text),
    );
});

const counts = computed(() => {
  const map: Record<string, number> = { all: scoped.value.length };
  scoped.value.forEach((issue) => {
    map[issue.status] = (map[issue.status] ?? 0) + 1;
  });
  return map;
});

const filtered = computed(() => {
  const list = scoped.value.filter(
    (issue) => status.value === "all" || issue.status === status.value,
  );
  const severityRank: Record<Issue["severity"], number> = { P0: 0, P1: 1, P2: 2, P3: 3 };
  return [...list].sort((first, second) => {
    if (sort.value === "recent") {
      return lastIssueOccurrence(second).localeCompare(lastIssueOccurrence(first));
    }
    const rank = severityRank[first.severity] - severityRank[second.severity];
    return rank !== 0
      ? rank
      : lastIssueOccurrence(second).localeCompare(lastIssueOccurrence(first));
  });
});

const visibleIssues = computed(() => filtered.value.slice(0, limit.value));

watch(
  [status, taskFilter, severityFilter, unstableOnly, sort, query],
  () => {
    limit.value = 40;
    const next = { ...route.query };
    const set = (key: string, value: string): void => {
      if (!value || value === "all") {
        delete next[key];
      } else {
        next[key] = value;
      }
    };
    set("status", status.value);
    set("task", taskFilter.value);
    set("severity", severityFilter.value);
    set("q", query.value.trim());
    set("unstable", unstableOnly.value ? "1" : "");
    set("sort", sort.value === "recent" ? "recent" : "");
    void router.replace({ query: next });
  },
);

const initialTaskId = computed(() =>
  taskFilter.value !== "all" ? taskFilter.value : (workspace.currentTask?.id ?? ""),
);

function openCreate(): void {
  if (tasks.value.length === 0) {
    ui.showToast("先建立任务，问题才有归属。", "info");
    return;
  }
  createOpen.value = true;
}

async function saveIssue(input: IssueCreateInput): Promise<void> {
  try {
    const issue = await workspace.createIssue(projectId.value, input);
    createOpen.value = false;
    ui.showToast("问题已登记；相同失败签名会合并到现有问题。", "success");
    await router.push(`/projects/${projectId.value}/issues/${issue.id}`);
  } catch {
    // store 已提示。
  }
}
</script>

<template>
  <div v-if="!project" class="page">
    <EmptyState title="项目不可用" text="回到项目概览重新选择项目。">
      <button class="btn btn-sm" type="button" @click="router.push('/')">回到项目入口</button>
    </EmptyState>
  </div>
  <div v-else class="page">
    <PageHeader
      title="问题清单"
      :subtitle="`${project.name} · ${issues.length} 个问题`"
    >
      <template #actions>
        <button
          class="btn btn-sm btn-primary"
          type="button"
          :disabled="Boolean(createGate)"
          :title="createGate || undefined"
          @click="openCreate"
        >
          登记问题
        </button>
        <p v-if="createGate" class="field-hint">{{ createGate }}</p>
      </template>
    </PageHeader>

    <EmptyState
      v-if="issues.length === 0"
      title="还没有问题"
      :text="
        tasks.length
          ? '失败用例会自动归并问题；也可以人工登记一个已知问题。'
          : '先建立任务，问题才有归属。'
      "
    >
      <button
        v-if="tasks.length"
        class="btn btn-sm"
        type="button"
        :disabled="Boolean(createGate)"
        :title="createGate || undefined"
        @click="openCreate"
      >
        登记第一个问题
      </button>
      <button
        v-else
        class="btn btn-sm"
        type="button"
        @click="router.push(`/projects/${projectId}/tasks/new`)"
      >
        去建立任务
      </button>
    </EmptyState>

    <template v-else>
      <div class="tabs" role="group" aria-label="按状态筛选问题">
        <button
          v-for="item in statuses"
          :key="item"
          class="tab"
          :class="{ 'is-active': status === item }"
          type="button"
          :aria-pressed="status === item"
          @click="status = item"
        >
          {{ item === "all" ? "全部" : ISSUE_STATUS_LABEL[item] }}
          <span class="filter-count">{{ counts[item] ?? 0 }}</span>
        </button>
      </div>
      <div class="toolbar issue-filter">
        <label class="scope-filter">
          <span>任务</span>
          <select v-model="taskFilter" aria-label="按任务筛选问题">
            <option value="all">全部任务</option>
            <option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.title }}</option>
          </select>
        </label>
        <label class="scope-filter">
          <span>严重度</span>
          <select v-model="severityFilter" aria-label="按严重度筛选问题">
            <option value="all">全部</option>
            <option value="P0">P0</option>
            <option value="P1">P1</option>
            <option value="P2">P2</option>
            <option value="P3">P3</option>
          </select>
        </label>
        <label class="checkline issue-unstable">
          <input v-model="unstableOnly" type="checkbox" />
          <span>只看疑似不稳定</span>
        </label>
        <label class="scope-filter">
          <span>排序</span>
          <select v-model="sort" aria-label="问题排序">
            <option value="severity">按严重度</option>
            <option value="recent">按最近发生</option>
          </select>
        </label>
        <label class="scope-filter scope-filter-search">
          <span>搜索</span>
          <input
            v-model="query"
            type="search"
            placeholder="标题、实际或预期"
            aria-label="搜索问题"
          />
        </label>
        <span class="section-note">{{ filtered.length }} 个结果</span>
      </div>

      <template v-if="filtered.length">
        <IssueTable
          :issues="visibleIssues"
          :tasks="project.tasks"
          @open="(issueId) => router.push(`/projects/${projectId}/issues/${issueId}`)"
        />
        <div v-if="filtered.length > visibleIssues.length" class="form-actions">
          <button class="btn btn-sm" type="button" @click="limit += 40">
            再显示 {{ Math.min(40, filtered.length - visibleIssues.length) }} 个
          </button>
        </div>
      </template>
      <EmptyState v-else title="这个筛选下没有问题" text="切回全部状态或清空搜索，查看其他问题。">
        <button
          class="btn btn-sm"
          type="button"
          @click="
            status = 'all';
            taskFilter = 'all';
            severityFilter = 'all';
            unstableOnly = false;
            query = '';
          "
        >
          清空筛选
        </button>
      </EmptyState>
    </template>

    <IssueCreateDialog
      v-model="createOpen"
      :tasks="tasks"
      :initial-task-id="initialTaskId"
      :busy="workspace.busyKey('issue-create')"
      @save="saveIssue"
    />
  </div>
</template>
