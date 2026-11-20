<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";

import { projectStatus } from "@/domain/derive";
import { stageLabel } from "@/domain/stateMachine";
import type { Project } from "@/domain/types";
import Icon from "./Icon.vue";

const props = defineProps<{
  projects: Project[];
  currentProjectId: string | null;
  currentTaskId: string | null;
  runningTaskId: string | null;
  section: string;
  open: boolean;
}>();

const emit = defineEmits<{
  selectProject: [projectId: string];
  openOverview: [projectId: string];
  openIssues: [projectId: string];
  openHistory: [projectId: string];
  selectTask: [projectId: string, taskId: string];
  newTask: [projectId: string];
  addProject: [];
  openSettings: [projectId: string];
  openTaskDetails: [projectId: string, taskId: string];
  renameTask: [projectId: string, taskId: string, title: string];
  close: [];
}>();

const query = ref("");
const searchInput = ref<HTMLInputElement | null>(null);
const closeButton = ref<HTMLButtonElement | null>(null);
const menuTaskId = ref<string | null>(null);
const renamingTaskId = ref<string | null>(null);
const renameValue = ref("");
const renameInput = ref<HTMLInputElement | null>(null);

const currentProject = computed(
  () => props.projects.find((project) => project.id === props.currentProjectId) ?? null,
);
const visibleProjects = computed(() => {
  const text = query.value.trim().toLowerCase();
  return props.projects
    .filter((project) => !project.archived)
    .filter(
      (project) =>
        !text ||
        project.name.toLowerCase().includes(text) ||
        project.root.toLowerCase().includes(text),
    );
});
const tasks = computed(() =>
  (currentProject.value?.tasks ?? []).filter((task) => task.stage !== "fresh"),
);
const draft = computed(
  () => currentProject.value?.tasks.find((task) => task.stage === "fresh") ?? null,
);
const openIssueCount = computed(
  () => currentProject.value?.issues.filter((issue) => issue.status !== "closed").length ?? 0,
);
const runCount = computed(
  () =>
    currentProject.value?.tasks.reduce((total, task) => total + task.runs.length, 0) ?? 0,
);

function toneFor(project: Project): string {
  return projectStatus(project).tone;
}

function selectProject(projectId: string): void {
  emit("selectProject", projectId);
}

function selectTask(taskId: string): void {
  menuTaskId.value = null;
  if (!props.currentProjectId) {
    return;
  }
  if (props.runningTaskId && props.runningTaskId !== taskId) {
    return;
  }
  emit("selectTask", props.currentProjectId, taskId);
}

function toggleTaskMenu(taskId: string, event: MouseEvent): void {
  event.stopPropagation();
  menuTaskId.value = menuTaskId.value === taskId ? null : taskId;
}

function openTaskDetails(projectId: string, taskId: string): void {
  menuTaskId.value = null;
  emit("openTaskDetails", projectId, taskId);
}

function beginRename(taskId: string, title: string): void {
  menuTaskId.value = null;
  renamingTaskId.value = taskId;
  renameValue.value = title;
  void nextTick(() => {
    renameInput.value?.focus();
    renameInput.value?.select();
  });
}

function setRenameInput(element: unknown): void {
  renameInput.value = element instanceof HTMLInputElement ? element : null;
}

function cancelRename(): void {
  renamingTaskId.value = null;
  renameValue.value = "";
}

function commitRename(projectId: string, taskId: string, currentTitle: string): void {
  const title = renameValue.value.trim();
  renamingTaskId.value = null;
  renameValue.value = "";
  if (!title || title === currentTitle) {
    return;
  }
  emit("renameTask", projectId, taskId, title);
}

function closeMenuOnOutside(event: MouseEvent): void {
  const target = event.target;
  if (target instanceof Element && target.closest(".side-task-row")) {
    return;
  }
  menuTaskId.value = null;
}

onMounted(() => document.addEventListener("click", closeMenuOnOutside));
onBeforeUnmount(() => document.removeEventListener("click", closeMenuOnOutside));

function focusSearch(): void {
  searchInput.value?.focus();
}

function focusClose(): void {
  closeButton.value?.focus();
}

defineExpose({ focusSearch, focusClose });
</script>

<template>
  <aside id="project-sidebar" class="sidebar" :class="{ 'is-open': open }" aria-label="项目与任务">
    <div class="sidebar-brand">
      <span class="sidebar-brand-mark" aria-hidden="true">
        <Icon name="shield" :size="16" />
      </span>
      <span class="sidebar-brand-name">AI 测试</span>
      <span class="sidebar-brand-note">本机</span>
      <button
        ref="closeButton"
        class="sidebar-close"
        type="button"
        aria-label="关闭项目与任务"
        @click="emit('close')"
      >
        <Icon name="close" :size="16" />
      </button>
    </div>

    <label class="sidebar-search">
      <span class="visually-hidden">搜索项目</span>
      <input
        ref="searchInput"
        v-model="query"
        type="search"
        placeholder="搜索项目或路径"
        autocomplete="off"
      />
    </label>

    <div class="sidebar-scroll">
      <div class="sidebar-section-label">
        <span>项目</span>
        <span>{{ visibleProjects.length }}</span>
      </div>
      <p v-if="visibleProjects.length === 0" class="side-empty">
        {{ projects.length === 0 ? "还没有项目。先建立一个本地目录。" : "没有匹配的项目。" }}
      </p>
      <div v-for="project in visibleProjects" :key="project.id" class="side-project">
        <button
          class="side-project-row"
          type="button"
          :aria-current="project.id === currentProjectId ? 'page' : undefined"
          @click="selectProject(project.id)"
        >
          <span class="side-status-dot" :class="`is-${toneFor(project)}`" aria-hidden="true"></span>
          <span class="side-project-main">
            <span class="side-project-name">{{ project.name }}</span>
            <span class="side-project-meta">{{ projectStatus(project).label }}</span>
          </span>
        </button>
        <nav
          v-if="project.id === currentProjectId"
          class="side-nav"
          aria-label="项目分区"
        >
          <button
            type="button"
            :class="{ 'is-active': section === 'overview' }"
            :aria-current="section === 'overview' ? 'page' : undefined"
            @click="emit('openOverview', project.id)"
          >
            概览
          </button>
          <button
            type="button"
            :class="{ 'is-active': section === 'issues' }"
            :aria-current="section === 'issues' ? 'page' : undefined"
            @click="emit('openIssues', project.id)"
          >
            问题
            <span v-if="openIssueCount" class="side-nav-count">{{ openIssueCount }}</span>
          </button>
          <button
            type="button"
            :class="{ 'is-active': section === 'history' }"
            :aria-current="section === 'history' ? 'page' : undefined"
            @click="emit('openHistory', project.id)"
          >
            记录
            <span v-if="runCount" class="side-nav-count">{{ runCount }}</span>
          </button>
          <button
            type="button"
            :class="{ 'is-active': section === 'settings' }"
            :aria-current="section === 'settings' ? 'page' : undefined"
            @click="emit('openSettings', project.id)"
          >
            设置
          </button>
        </nav>
        <div
          v-if="project.id === currentProjectId"
          class="side-task-list"
          aria-label="当前项目的任务"
        >
          <div class="side-task-head">
            <span>任务</span>
            <button
              class="side-add"
              type="button"
              :disabled="Boolean(runningTaskId)"
              @click="emit('newTask', project.id)"
            >
              <Icon name="plus" :size="12" />
              新建
            </button>
          </div>
          <button
            v-if="draft"
            class="side-task"
            :class="{ 'is-disabled': Boolean(runningTaskId) }"
            type="button"
            :disabled="Boolean(runningTaskId)"
            @click="emit('newTask', project.id)"
          >
            <span class="side-task-main">
              <span class="side-task-title">新任务 · 待确认目标</span>
              <span class="side-task-meta">继续确认</span>
            </span>
          </button>
          <div
            v-for="task in tasks"
            :key="task.id"
            class="side-task-row"
            :class="{
              'is-active': task.id === currentTaskId,
              'is-disabled': Boolean(runningTaskId && runningTaskId !== task.id),
            }"
          >
            <form
              v-if="renamingTaskId === task.id"
              class="side-task-rename"
              @submit.prevent="commitRename(project.id, task.id, task.title)"
            >
              <input
                :ref="setRenameInput"
                v-model="renameValue"
                type="text"
                maxlength="80"
                aria-label="任务名称"
                @keydown.esc.prevent="cancelRename"
                @blur="commitRename(project.id, task.id, task.title)"
              />
            </form>
            <button
              v-else
              class="side-task"
              :class="{ 'is-active': task.id === currentTaskId }"
              type="button"
              :aria-current="task.id === currentTaskId ? 'page' : undefined"
              :disabled="Boolean(runningTaskId && runningTaskId !== task.id)"
              @click="selectTask(task.id)"
            >
              <span class="side-task-main">
                <span class="side-task-title">{{ task.title }}</span>
                <span class="side-task-meta">{{ stageLabel(task.stage) }}</span>
              </span>
            </button>
            <button
              v-if="renamingTaskId !== task.id"
              class="side-task-more"
              type="button"
              aria-label="任务操作"
              aria-haspopup="menu"
              :aria-expanded="menuTaskId === task.id"
              @click.stop="toggleTaskMenu(task.id, $event)"
            >
              <Icon name="more" :size="14" />
            </button>
            <div v-if="menuTaskId === task.id" class="side-task-menu" role="menu">
              <button
                type="button"
                role="menuitem"
                :disabled="runningTaskId === task.id"
                @click="beginRename(task.id, task.title)"
              >
                重命名
              </button>
              <button
                type="button"
                role="menuitem"
                @click="openTaskDetails(project.id, task.id)"
              >
                编辑资料
              </button>
            </div>
          </div>
          <p v-if="tasks.length === 0 && !draft" class="side-empty">
            还没有任务。点“新建”开始。
          </p>
        </div>
      </div>
    </div>

    <div class="sidebar-footer">
      <button class="btn btn-ghost btn-sm" type="button" @click="emit('addProject')">
        <Icon name="plus" :size="14" />
        建立项目
      </button>
    </div>
  </aside>
</template>
