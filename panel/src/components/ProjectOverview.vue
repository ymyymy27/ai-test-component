<script setup lang="ts">
import { computed } from "vue";

import {
  activeTask,
  allRuns,
  openIssues as selectOpenIssues,
  projectStatus,
  runningTask,
} from "@/domain/derive";
import { ISSUE_STATUS_LABEL } from "@/domain/labels";
import type { Issue, Module, Project } from "@/domain/types";
import ActivityList from "./ActivityList.vue";
import CandidateBrief from "./CandidateBrief.vue";
import CurrentTaskStrip from "./CurrentTaskStrip.vue";
import Icon from "./Icon.vue";
import ProjectMap from "./ProjectMap.vue";
import ProjectStats from "./ProjectStats.vue";
import { useWorkspaceStore } from "@/stores/workspace";

const props = defineProps<{
  project: Project;
  analyzing: boolean;
  analysisError: string;
  analyzingBusy: boolean;
}>();

const workspace = useWorkspaceStore();

const emit = defineEmits<{
  newTask: [];
  viewCandidates: [];
  openTask: [taskId: string];
  openRun: [runId: string];
  openIssue: [issueId: string];
  openHistory: [];
  openIssues: [];
  analyze: [];
  addModule: [];
  editModule: [module: Module];
  toggleFeature: [featureName: string, ignored: boolean];
}>();

const tasks = computed(() => props.project.tasks.filter((task) => task.stage !== "fresh"));
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
const boundRule = computed(
  () => props.project.rules.find((rule) => rule.id === props.project.rulesVersionId) ?? null,
);
const latestRunText = computed(() => {
  const latest = runs.value[0];
  return latest ? new Date(latest.startedAt).toLocaleString("zh-CN") : "还没有运行";
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
  <div class="page">
    <header class="page-head">
      <div class="page-head-main">
        <span class="page-kicker">项目概览</span>
        <h1 class="page-title">{{ project.name }}</h1>
        <p class="page-subtitle">
          <span class="current-task-state">
            <span class="side-status-dot" :class="`is-${status.tone}`" aria-hidden="true"></span>
            {{ status.label }}
          </span>
          <span class="muted"> · </span>
          <span class="mono">{{ project.root }}</span>
        </p>
      </div>
      <div class="page-actions">
        <button
          class="btn btn-sm btn-primary"
          type="button"
          :disabled="Boolean(running)"
          :title="running ? '有任务正在运行，完成或停止后再新建' : undefined"
          @click="emit('newTask')"
        >
          新建任务
        </button>
      </div>
    </header>

    <ProjectStats
      :tasks="tasks.length"
      :runs="runs.length"
      :open-issues="issues.length"
      :features="features.length"
    />
    <p class="overview-summary">
      最近运行 {{ latestRunText }} · {{ project.modules.length }} 个模块已识别
    </p>

    <div v-if="analyzing" class="analysis-strip" role="status" aria-live="polite" aria-busy="true">
      <span class="spinner" aria-hidden="true"></span>
      <span class="candidate-brief-main">
        <span class="analysis-strip-title">AI 分析中</span>
        <span class="analysis-strip-text">正在整理入口、模块、功能与目标候选。</span>
      </span>
    </div>
    <div v-else-if="analysisError" class="notice is-warn" role="alert">
      <Icon name="warn" :size="14" />
      <span>项目分析没有完成：{{ analysisError }}</span>
      <button class="link-btn" type="button" :disabled="analyzingBusy" @click="emit('analyze')">
        {{ analyzingBusy ? "分析中…" : "重新分析" }}
      </button>
    </div>
    <CandidateBrief
      v-else-if="tasks.length === 0 && project.candidates.length > 0"
      :candidates="project.candidates"
      @view="emit('viewCandidates')"
    />

    <CurrentTaskStrip
      v-if="focusTaskItem"
      :task="focusTaskItem"
      :open-issue-count="issues.length"
      :running-elsewhere="Boolean(running && running.id !== focusTaskItem.id)"
      @open="openTaskById(focusTaskItem.id)"
    />

    <div class="overview-grid">
      <section class="section overview-feed">
        <div class="section-head">
          <span class="section-title">最近活动</span>
          <button class="link-btn" type="button" @click="emit('openHistory')">全部历史</button>
        </div>
        <ActivityList :runs="runs" :tasks="project.tasks" @open="emit('openRun', $event)" />
      </section>

      <div class="overview-side">
        <ProjectMap
          :map="project.map"
          :modules="project.modules"
          :features="allFeatures"
          :ignored-features="ignoredFeatures"
          :catalog-count="catalogCount"
          :analyzing="analyzing"
          :busy="analyzingBusy"
          @analyze="emit('analyze')"
          @add-module="emit('addModule')"
          @edit-module="emit('editModule', $event)"
          @toggle-feature="toggleFeature"
        />

        <section class="section">
          <div class="section-head">
            <span class="section-title">未关闭问题</span>
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
              <span class="severity" :class="{ 'is-high': issue.severity === 'P1' }">
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
          <p v-else class="doc-note">当前没有未关闭问题。</p>
        </section>
      </div>
    </div>

    <section class="section overview-info">
      <dl class="info-grid">
        <div class="info-cell">
          <dt>项目目标</dt>
          <dd>{{ project.goal || "还没有填写" }}</dd>
        </div>
        <div class="info-cell">
          <dt>当前规则</dt>
          <dd>{{ boundRule ? `${boundRule.name} v${boundRule.version}` : "未绑定" }}</dd>
        </div>
        <div class="info-cell">
          <dt>工作空间</dt>
          <dd>{{ workspace.mode === "host" ? "宿主 CLI 工作空间 · 面板不直接写业务文件" : "浏览器本地存储 · 不写入被测仓库" }}</dd>
        </div>
      </dl>
    </section>
  </div>
</template>
