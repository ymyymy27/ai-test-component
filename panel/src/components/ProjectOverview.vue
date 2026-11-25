<script setup lang="ts">
import { computed } from "vue";

import {
  activeTask,
  allRuns,
  currentPlan,
  currentRun,
  openIssues as selectOpenIssues,
  projectStatus,
  runningTask,
} from "@/domain/derive";
import { ISSUE_STATUS_LABEL, VERDICT_LABEL } from "@/domain/labels";
import { taskStageLabel } from "@/domain/stateMachine";
import type { Issue, Module, Project, Task } from "@/domain/types";
import { useWorkspaceStore } from "@/stores/workspace";
import ActivityList from "./ActivityList.vue";
import CurrentTaskStrip from "./CurrentTaskStrip.vue";
import EmptyState from "./EmptyState.vue";
import GitStatusBar from "./GitStatusBar.vue";
import Icon from "./Icon.vue";
import ProjectMap from "./ProjectMap.vue";
import ProjectStats from "./ProjectStats.vue";

const props = withDefaults(
  defineProps<{
    project: Project;
    analyzing: boolean;
    analysisError: string;
    analyzingBusy: boolean;
    dirtyFiles?: string[];
  }>(),
  {
    dirtyFiles: () => [],
  },
);

const workspace = useWorkspaceStore();

const emit = defineEmits<{
  newTask: [];
  openQuickCheck: [];
  openArchitecture: [];
  openTask: [taskId: string];
  openRun: [runId: string];
  openIssue: [issueId: string];
  openHistory: [];
  openIssues: [];
  analyze: [];
  addModule: [];
  editModule: [module: Module];
  toggleFeature: [featureName: string, ignored: boolean];
  openSource: [file: string, line: number];
  refreshGraph: [];
  testAffected: [goalText: string];
}>();

const tasks = computed(() => props.project.tasks.filter((task) => task.stage !== "fresh"));
const recentTasks = computed(() =>
  [...tasks.value]
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
    .slice(0, 5),
);
const draft = computed(() => props.project.tasks.find((task) => task.stage === "fresh") ?? null);
const runs = computed(() => allRuns(props.project));
const issues = computed(() => selectOpenIssues(props.project));
const allFeatures = computed(() => props.project.map?.features ?? []);
const ignoredFeatures = computed(() => props.project.map?.ignoredFeatures ?? []);
const ignoredSet = computed(() => new Set(ignoredFeatures.value));
const features = computed(() =>
  allFeatures.value.filter((feature) => !ignoredSet.value.has(feature.name)),
);
const catalogCount = computed(
  () => props.project.catalog.filter((item) => item.observable && item.status === "active").length,
);
const active = computed(() => activeTask(props.project));
const running = computed(() => runningTask(props.project));
const focusTaskItem = computed(() => active.value ?? draft.value);
const status = computed(() => projectStatus(props.project));
const mapBusy = computed(() => workspace.busyKey(`map:${props.project.id}`));

// 右栏轻量架构健康态势摘要（替代原来的巨型内嵌图）
const archDigest = computed(() => {
  const graph = props.project.codeGraph;
  if (!graph) {
    return null;
  }
  const layerCounts = {
    frontend: 0,
    api: 0,
    backend: 0,
    storage: 0,
  };
  graph.nodes.forEach((n) => {
    layerCounts[n.layer] += 1;
  });
  return {
    totalNodes: graph.nodes.length,
    totalEdges: graph.edges.length,
    affectedCount: graph.impact.affectedNodeIds.length,
    conflictCount: graph.conflicts.length,
    layerCounts,
  };
});

function taskTitle(taskId: string): string {
  return props.project.tasks.find((task) => task.id === taskId)?.title ?? "任务已归档";
}

function planLabel(issue: Issue): string {
  const task = props.project.tasks.find((entry) => entry.id === issue.taskId);
  const plan = task?.plans.find((entry) => entry.id === issue.planId);
  return plan ? `计划 ${plan.no} · 第 ${plan.revision} 版` : "计划已归档";
}

function runLabel(issue: Issue): string {
  const task = props.project.tasks.find((entry) => entry.id === issue.taskId);
  const run = task?.runs.find((entry) => entry.id === issue.runId);
  return run ? new Date(run.startedAt).toLocaleString("zh-CN") : "运行已归档";
}

function taskSummaryStatus(task: Task): { label: string; badgeClass: string; coverage: string } {
  const run = currentRun(task);
  const plan = currentPlan(task);
  const coverage = plan
    ? `覆盖 ${plan.scope.coverage.selected}/${plan.scope.coverage.applicable}`
    : `${task.selectedItemIds.length} 项待测`;
  if (task.stage === "result" && run) {
    const cls =
      run.verdict === "passed" ? "is-pass" : run.verdict === "failed" ? "is-fail" : "is-warn";
    return { label: VERDICT_LABEL[run.verdict], badgeClass: cls, coverage };
  }
  if (task.stage === "running") {
    return { label: "运行中", badgeClass: "is-accent", coverage };
  }
  return { label: taskStageLabel(task), badgeClass: "is-neutral", coverage };
}

function openTaskById(taskId: string): void {
  if (taskId) {
    emit("openTask", taskId);
  }
}

function toggleFeature(name: string, ignored: boolean): void {
  emit("toggleFeature", name, ignored);
}
</script>

<template>
  <div class="page page-wide">
    <header class="page-rule-head">
      <div class="page-head-main">
        <div class="overview-title-row">
          <h1 class="page-title">{{ project.name }}</h1>
          <span class="overview-status-pill" :class="`is-${status.tone}`">
            <span class="side-status-dot" :class="`is-${status.tone}`" aria-hidden="true"></span>
            {{ status.label }}
          </span>
        </div>
        <div class="overview-meta-row">
          <span class="overview-meta-chip mono" :title="project.root">
            <Icon name="folder" :size="13" />
            <span>{{ project.root }}</span>
          </span>
          <span v-if="project.goal" class="overview-meta-chip">
            <Icon name="info" :size="13" />
            <span>{{ project.goal }}</span>
          </span>
        </div>
      </div>

      <div class="page-actions">
        <button
          class="btn btn-sm"
          type="button"
          @click="emit('openQuickCheck')"
        >
          <Icon name="bolt" :size="14" />
          <span>快速检查</span>
        </button>
        <button
          class="btn btn-primary"
          type="button"
          :disabled="Boolean(running)"
          :title="running ? '有任务正在运行，完成或停止后再开始' : undefined"
          @click="emit('newTask')"
        >
          <Icon name="plus" :size="14" />
          <span>新建测试任务</span>
        </button>
      </div>
    </header>

    <ProjectStats
      :tasks="tasks.length"
      :runs="runs.length"
      :open-issues="issues.length"
      :features="features.length"
    />

    <GitStatusBar
      :local-git="project.localGit ?? null"
      :code-graph="project.codeGraph ?? null"
      :dirty-files="dirtyFiles"
      :refreshing="workspace.loading"
      @refresh="emit('refreshGraph')"
      @test-affected="emit('testAffected', $event)"
      @open-source="(file, line) => emit('openSource', file, line)"
    />

    <div v-if="analyzing" class="analysis-strip" role="status" aria-live="polite" aria-busy="true">
      <span class="spinner" aria-hidden="true"></span>
      <span class="candidate-brief-main">
        <span class="analysis-strip-title">正在扫描项目结构</span>
        <span class="analysis-strip-text">分析模块、入口与可测项。</span>
      </span>
    </div>
    <div v-else-if="analysisError" class="notice is-warn" role="alert">
      <Icon name="warn" :size="14" />
      <span>项目结构扫描中断：{{ analysisError }}</span>
      <button class="link-btn" type="button" :disabled="analyzingBusy" @click="emit('analyze')">
        {{ analyzingBusy ? "分析中…" : "重新分析" }}
      </button>
    </div>

    <CurrentTaskStrip
      v-if="focusTaskItem"
      :task="focusTaskItem"
      :running-elsewhere="Boolean(running && running.id !== focusTaskItem.id)"
      @open="openTaskById(focusTaskItem.id)"
    />

    <div class="overview-grid">
      <!-- 左栏：开放式工作台动态（最近任务 + 运行活动） -->
      <section class="overview-feed stack">
        <section class="stack-sm" aria-label="近期测试任务">
          <div class="section-head">
            <h2 class="section-title">近期测试任务</h2>
            <button
              v-if="tasks.length"
              class="link-btn"
              type="button"
              :disabled="Boolean(running)"
              @click="emit('newTask')"
            >
              + 新建任务
            </button>
          </div>
          <div v-if="recentTasks.length" class="overview-task-list">
            <button
              v-for="item in recentTasks"
              :key="item.id"
              class="overview-task-row"
              type="button"
              @click="openTaskById(item.id)"
            >
              <span style="min-width: 0; flex: 1;">
                <span class="row-title">{{ item.title || "未命名任务" }}</span>
                <span class="row-meta">
                  <span>{{ taskSummaryStatus(item).coverage }}</span>
                  <span>{{ new Date(item.updatedAt).toLocaleString("zh-CN") }}</span>
                </span>
              </span>
              <span class="badge" :class="taskSummaryStatus(item).badgeClass">
                {{ taskSummaryStatus(item).label }}
              </span>
              <Icon name="chevron-right" :size="14" />
            </button>
          </div>
          <EmptyState
            v-else
            title="暂无测试任务"
            text="点击右上「新建测试任务」开始。"
          />
        </section>

        <section class="stack-sm" aria-label="最近运行活动">
          <div class="section-head">
            <h2 class="section-title">最近运行记录</h2>
            <button class="link-btn" type="button" @click="emit('openHistory')">全部记录</button>
          </div>
          <ActivityList :runs="runs" :tasks="project.tasks" @open="emit('openRun', $event)" />
        </section>
      </section>

      <!-- 右栏：焦点卡片（模块与依赖 + 未关闭问题 + 功能模块） -->
      <div class="overview-side stack">
        <section class="elevated-panel arch-mini-card" aria-label="模块与依赖">
          <div class="row-between">
            <h2 class="section-title">模块与依赖</h2>
            <button
              class="link-btn"
              type="button"
              @click="emit('openArchitecture')"
            >
              查看架构图
            </button>
          </div>
          <div v-if="archDigest" class="arch-mini-rows">
            <div class="arch-mini-row">
              <span class="muted">模块分布</span>
              <span class="num">
                前端 {{ archDigest.layerCounts.frontend }} · 接口 {{ archDigest.layerCounts.api }} ·
                后端 {{ archDigest.layerCounts.backend }} · 存储 {{ archDigest.layerCounts.storage }}
              </span>
            </div>
            <div class="arch-mini-row">
              <span class="muted">变更影响</span>
              <span class="status-text" :class="archDigest.affectedCount ? 'is-blocked' : 'is-not_run'">
                {{ archDigest.affectedCount ? `${archDigest.affectedCount} 个节点受影响` : "无变更波及" }}
              </span>
            </div>
            <div class="arch-mini-row">
              <span class="muted">跨层冲突</span>
              <span class="status-text" :class="archDigest.conflictCount ? 'is-failed' : 'is-passed'">
                {{ archDigest.conflictCount ? `${archDigest.conflictCount} 项冲突` : "无冲突" }}
              </span>
            </div>
          </div>
          <p v-else class="field-hint">
            尚未生成代码调用图；扫描后可在此查看模块影响范围。
          </p>
        </section>

        <section class="elevated-panel stack-sm" aria-label="未关闭问题">
          <div class="section-head">
            <h2 class="section-title">未关闭问题</h2>
            <button class="link-btn" type="button" @click="emit('openIssues')">全部问题</button>
          </div>
          <div v-if="issues.length">
            <button
              v-for="issue in issues.slice(0, 4)"
              :key="issue.id"
              class="issue-row"
              type="button"
              @click="emit('openIssue', issue.id)"
            >
              <span
                class="severity"
                :class="{
                  'is-critical': issue.severity === 'P0',
                  'is-high': issue.severity === 'P1',
                }"
              >
                {{ issue.severity }}
              </span>
              <span>
                <span class="row-title">{{ issue.title }}</span>
                <span class="row-meta">
                  <span>{{ taskTitle(issue.taskId) }}</span>
                  <span>{{ planLabel(issue) }}</span>
                  <span>{{ runLabel(issue) }}</span>
                </span>
              </span>
              <span class="badge" :class="issue.status === 'closed' ? 'is-neutral' : 'is-warn'">
                {{ ISSUE_STATUS_LABEL[issue.status] }}
              </span>
              <Icon class="issue-chevron" name="chevron-right" :size="13" />
            </button>
          </div>
          <EmptyState
            v-else
            title="暂无未关闭问题"
            text="失败用例会自动按签名归并。"
          />
        </section>

        <details class="elevated-panel details-tech" style="margin-top: 0;">
          <summary>功能模块 ({{ features.length }} 个功能 / {{ catalogCount }} 条验收点)</summary>
          <div style="margin-top: 12px;">
            <ProjectMap
              :map="project.map"
              :modules="project.modules"
              :features="allFeatures"
              :ignored-features="ignoredFeatures"
              :catalog-count="catalogCount"
              :analyzing="analyzing"
              :busy="mapBusy"
              @analyze="emit('analyze')"
              @add-module="emit('addModule')"
              @edit-module="emit('editModule', $event)"
              @toggle-feature="toggleFeature"
            />
          </div>
        </details>
      </div>
    </div>
  </div>
</template>
