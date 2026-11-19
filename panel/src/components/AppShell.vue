<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";

import { missingEnvironmentCount } from "@/services/environmentService";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";
import ConfirmDialog from "./ConfirmDialog.vue";
import ProjectSidebar from "./ProjectSidebar.vue";
import Toast from "./Toast.vue";
import WorkspaceHeader from "./WorkspaceHeader.vue";

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();

const mainRef = ref<HTMLElement | null>(null);
const sidebarRef = ref<InstanceType<typeof ProjectSidebar> | null>(null);
const headerRef = ref<InstanceType<typeof WorkspaceHeader> | null>(null);
const envPopoverRef = ref<HTMLElement | null>(null);

const project = computed(() => workspace.currentProject);
const task = computed(() => workspace.currentTask);
const sectionTitle = computed(() =>
  typeof route.meta.title === "string" ? route.meta.title : "AI 测试",
);
const missingCount = computed(() => missingEnvironmentCount(workspace.environment));
const taskRoute = computed(() => {
  const current = task.value;
  if (!current || !project.value) {
    return null;
  }
  const projectId = project.value.id;
  switch (current.stage) {
    case "fresh":
      return `/projects/${projectId}/tasks/new`;
    case "scope":
      return `/projects/${projectId}/tasks/${current.id}/scope`;
    case "planned":
      return `/projects/${projectId}/tasks/${current.id}/plan`;
    case "running":
      return `/projects/${projectId}/tasks/${current.id}/run`;
    case "result":
      return `/projects/${projectId}/tasks/${current.id}/result`;
    default:
      return null;
  }
});
const showTaskCrumb = computed(() =>
  ["task-details", "scope", "plan", "run", "result"].includes(String(route.name)),
);
const projectId = computed(() => project.value?.id ?? null);
const projectName = computed(() => project.value?.name ?? "");
const taskTitle = computed(() => (showTaskCrumb.value ? (task.value?.title ?? "") : ""));

function routeForTask(taskId: string): string {
  const current = project.value?.tasks.find((entry) => entry.id === taskId);
  const id = project.value?.id;
  if (!current || !id) {
    return "/";
  }
  switch (current.stage) {
    case "fresh":
      return `/projects/${id}/tasks/new`;
    case "scope":
      return `/projects/${id}/tasks/${taskId}/scope`;
    case "planned":
      return `/projects/${id}/tasks/${taskId}/plan`;
    case "running":
      return `/projects/${id}/tasks/${taskId}/run`;
    case "result":
      return `/projects/${id}/tasks/${taskId}/result`;
    default:
      return `/projects/${id}/overview`;
  }
}

async function selectProject(projectIdValue: string): Promise<void> {
  workspace.setCurrentProject(projectIdValue);
  ui.closeSidebar();
  await router.push(`/projects/${projectIdValue}/overview`);
}

async function selectTask(projectIdValue: string, taskId: string): Promise<void> {
  try {
    await workspace.setActiveTask(projectIdValue, taskId);
    ui.closeSidebar();
    await router.push(routeForTask(taskId));
  } catch {
    // store 已提示错误。
  }
}

async function newTask(projectIdValue: string): Promise<void> {
  ui.closeSidebar();
  await router.push(`/projects/${projectIdValue}/tasks/new`);
}

async function addProject(): Promise<void> {
  ui.closeSidebar();
  await router.push("/setup");
}

async function openSettings(projectIdValue: string): Promise<void> {
  ui.closeSidebar();
  await router.push(`/projects/${projectIdValue}/settings`);
}

async function toggleSidebar(): Promise<void> {
  if (ui.sidebarOpen) {
    ui.closeSidebar();
    await nextTick();
    headerRef.value?.focusToggle();
    return;
  }
  ui.openSidebar();
  await nextTick();
  sidebarRef.value?.focusClose();
}

function closeSidebar(): void {
  ui.closeSidebar();
  void nextTick(() => headerRef.value?.focusToggle());
}

function skipToMain(event: MouseEvent): void {
  event.preventDefault();
  mainRef.value?.focus();
  mainRef.value?.scrollIntoView({ behavior: "auto", block: "start" });
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== "Escape") {
    return;
  }
  if (ui.sidebarOpen) {
    event.preventDefault();
    closeSidebar();
    return;
  }
  if (ui.envOpen) {
    ui.closeEnv();
  }
}

function onDocumentPointer(event: PointerEvent): void {
  if (!ui.envOpen) {
    return;
  }
  const target = event.target;
  if (target instanceof Node && envPopoverRef.value?.contains(target)) {
    return;
  }
  if (headerRef.value?.isEnvButtonTarget(target)) {
    return;
  }
  ui.closeEnv();
}

watch(
  () => route.fullPath,
  async () => {
    ui.closeEnv();
    await nextTick();
    mainRef.value?.focus({ preventScroll: true });
  },
);

onMounted(() => {
  window.addEventListener("keydown", onKeydown);
  document.addEventListener("pointerdown", onDocumentPointer);
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
  document.removeEventListener("pointerdown", onDocumentPointer);
});
</script>

<template>
  <a class="skip-link" href="#main-content" @click="skipToMain">跳到主要内容</a>
  <div class="app-shell">
    <ProjectSidebar
      ref="sidebarRef"
      :inert="ui.narrow && !ui.sidebarOpen"
      :projects="workspace.projects"
      :current-project-id="workspace.currentProjectId"
      :current-task-id="workspace.currentTask?.id ?? null"
      :running-task-id="workspace.runningTask?.id ?? null"
      :open="ui.sidebarOpen"
      @select-project="selectProject"
      @select-task="selectTask"
      @new-task="newTask"
      @add-project="addProject"
      @open-settings="openSettings"
      @close="closeSidebar"
    />
    <button
      v-if="ui.sidebarOpen"
      class="sidebar-backdrop"
      type="button"
      aria-label="关闭项目与任务"
      @click="closeSidebar"
    ></button>

    <div class="workspace" :inert="ui.narrow && ui.sidebarOpen">
      <WorkspaceHeader
        ref="headerRef"
        :project-id="projectId"
        :project-name="projectName"
        :task-title="taskTitle"
        :task-route="taskRoute"
        :section-title="sectionTitle"
        :missing-count="missingCount"
        :sidebar-open="ui.sidebarOpen"
        @toggle-sidebar="toggleSidebar"
        @toggle-env="ui.toggleEnv"
      />
      <div
        v-if="ui.envOpen"
        id="env-popover"
        ref="envPopoverRef"
        class="env-popover"
        role="region"
        aria-label="环境自检"
      >
        <h2>环境自检</h2>
        <div v-for="check in workspace.environment" :key="check.key" class="env-popover-row">
          <span>{{ check.name }}</span>
          <span>{{ check.value }}</span>
          <span>{{ check.ok ? "通过" : "缺失" }}</span>
        </div>
        <p class="field-hint env-popover-note">
          {{
            missingCount > 0
              ? `${missingCount} 项未接入；本地记录、证据与报告仍可用。`
              : "全部检查通过。"
          }}
        </p>
        <RouterLink v-if="projectId" :to="`/projects/${projectId}/settings`" class="link-btn">
          打开设置
        </RouterLink>
      </div>
      <div class="workspace-main">
        <main id="main-content" ref="mainRef" class="main" tabindex="-1">
          <RouterView />
        </main>
      </div>
    </div>
  </div>
  <Toast />
  <ConfirmDialog />
</template>
