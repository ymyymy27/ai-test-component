<script setup lang="ts">
import { computed, ref } from "vue";

import { projectStatus } from "@/domain/derive";
import { stageLabel } from "@/domain/stateMachine";
import type { Project } from "@/domain/types";
import Icon from "./Icon.vue";

const props = defineProps<{
  projects: Project[];
  currentProjectId: string | null;
  currentTaskId: string | null;
  runningTaskId: string | null;
  open: boolean;
}>();

const emit = defineEmits<{
  selectProject: [projectId: string];
  selectTask: [projectId: string, taskId: string];
  newTask: [projectId: string];
  addProject: [];
  openSettings: [projectId: string];
  close: [];
}>();

const query = ref("");
const searchInput = ref<HTMLInputElement | null>(null);
const closeButton = ref<HTMLButtonElement | null>(null);

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

function toneFor(project: Project): string {
  return projectStatus(project).tone;
}

function selectProject(projectId: string): void {
  emit("selectProject", projectId);
}

function selectTask(taskId: string): void {
  if (!props.currentProjectId) {
    return;
  }
  if (props.runningTaskId && props.runningTaskId !== taskId) {
    return;
  }
  emit("selectTask", props.currentProjectId, taskId);
}

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
        <div
          v-if="project.id === currentProjectId"
          class="side-task-list"
          aria-label="当前项目的任务"
        >
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
          <button
            v-for="task in tasks"
            :key="task.id"
            class="side-task"
            :class="{
              'is-active': task.id === currentTaskId,
              'is-disabled': Boolean(runningTaskId && runningTaskId !== task.id),
            }"
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
          <p v-if="tasks.length === 0 && !draft" class="side-empty">
            还没有任务。从概览新建，或先从右侧开始。
          </p>
        </div>
      </div>
    </div>

    <div class="sidebar-footer">
      <button class="btn btn-ghost btn-sm" type="button" @click="emit('addProject')">
        <Icon name="plus" :size="14" />
        建立项目
      </button>
      <button
        v-if="currentProject"
        class="btn btn-ghost btn-sm"
        type="button"
        @click="emit('openSettings', currentProject.id)"
      >
        <Icon name="settings" :size="14" />
        项目设置
      </button>
    </div>
  </aside>
</template>
