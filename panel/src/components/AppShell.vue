<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";

import { missingEnvironmentCount } from "@/services/environmentService";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";
import ConfirmDialog from "./ConfirmDialog.vue";
import LoadingState from "./LoadingState.vue";
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
const taskScoped = computed(() =>
  ["scope", "plan", "run", "result", "task-details", "report"].includes(String(route.name)),
);
const currentSection = computed(() => {
  const name = String(route.name ?? "");
  if (name === "overview") return "overview";
  if (name === "issues" || name === "issue-detail") return "issues";
  if (name === "history") return "history";
  if (name === "quick-check") return "quick";
  if (name === "settings") return "settings";
  return "";
});
const backRoute = computed(() => {
  const currentProject = project.value;
  if (!currentProject) {
    return null;
  }
  const overview = `/projects/${currentProject.id}/overview`;
  const currentTask = task.value;
  const taskBase = currentTask
    ? `/projects/${currentProject.id}/tasks/${currentTask.id}`
    : "";
  const name = String(route.name ?? "");
  if (name === "overview") {
    return null;
  }
  if (name === "issue-detail") {
    return `/projects/${currentProject.id}/issues`;
  }
  if (name === "report") {
    // 报告属于 report.taskId，不是“当前任务”；从历史打开其他任务报告时返回对应结果。
    const reportId = String(route.params.reportId ?? "");
    const report = workspace.projectReports.find((entry) => entry.id === reportId);
    return report?.taskId
      ? `/projects/${currentProject.id}/tasks/${report.taskId}/result?run=${report.runId}`
      : overview;
  }
  if (name === "result" || name === "run") {
    return currentTask ? `${taskBase}/plan` : overview;
  }
  if (name === "scope" && currentTask?.previousStage) {
    if (currentTask.previousStage === "planned") {
      return `${taskBase}/plan`;
    }
    if (currentTask.previousStage === "result") {
      return `${taskBase}/result`;
    }
    if (currentTask.previousStage === "running") {
      return `${taskBase}/run`;
    }
  }
  return overview;
});
const missingCount = computed(() =>
  workspace.environment.length ? missingEnvironmentCount(workspace.environment) : null,
);

watch(
  () => ui.envOpen,
  async (open) => {
    if (!open) {
      return;
    }
    await nextTick();
    envPopoverRef.value?.querySelector<HTMLElement>("a, button")?.focus();
  },
);
const taskRoute = computed(() => {
  const current = task.value;
  if (!current || !current.id || !project.value) {
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
  if (!taskId) {
    return project.value ? `/projects/${project.value.id}/overview` : "/";
  }
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

async function openProjectSection(
  projectIdValue: string,
  section: "overview" | "issues" | "history",
): Promise<void> {
  ui.closeSidebar();
  await router.push(`/projects/${projectIdValue}/${section}`);
}

async function openQuick(projectIdValue: string): Promise<void> {
  ui.closeSidebar();
  await router.push(`/projects/${projectIdValue}/quick-check`);
}

async function selectProject(projectIdValue: string): Promise<void> {
  try {
    await workspace.setCurrentProject(projectIdValue);
  } catch (error) {
    ui.showToast(error instanceof Error ? error.message : "切换项目失败，请重试。", "error");
    return;
  }
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

async function openTaskDetails(projectIdValue: string, taskId: string): Promise<void> {
  try {
    await workspace.setActiveTask(projectIdValue, taskId);
    ui.closeSidebar();
    await router.push(`/projects/${projectIdValue}/tasks/${taskId}/details`);
  } catch {
    // store 已提示错误。
  }
}

async function renameTask(
  projectIdValue: string,
  taskId: string,
  title: string,
): Promise<void> {
  try {
    await workspace.updateTask(projectIdValue, taskId, { title });
    ui.showToast("任务已重命名。", "success");
  } catch (error) {
    ui.showToast(error instanceof Error ? error.message : "重命名失败。", "error");
  }
}

async function deleteTask(
  projectIdValue: string,
  taskId: string,
  title: string,
): Promise<void> {
  const confirmed = await ui.askConfirm({
    title: "删除任务",
    body: `删除「${title}」及其计划、运行、报告、问题和证据。删除后不可恢复。`,
    confirmLabel: "删除",
    tone: "danger",
  });
  if (confirmed === null) {
    return;
  }
  const wasCurrent = workspace.currentTask?.id === taskId;
  try {
    await workspace.deleteTask(projectIdValue, taskId);
    if (wasCurrent) {
      await router.push(`/projects/${projectIdValue}/overview`);
    }
    sidebarRef.value?.focusTasks();
    ui.showToast("任务已删除。", "success");
  } catch (error) {
    ui.showToast(error instanceof Error ? error.message : "删除失败。", "error");
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
  // 模态对话框在顶层：一次 Esc 只关闭最上层，不能同时关掉抽屉/浮层。
  if (typeof document !== "undefined" && document.querySelector("dialog[open]")) {
    return;
  }
  if (ui.sidebarOpen) {
    event.preventDefault();
    closeSidebar();
    return;
  }
  if (ui.envOpen) {
    ui.closeEnv();
    void nextTick(() => headerRef.value?.focusEnv());
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

let lastRouteName = String(route.name ?? "");
watch(
  () => route.fullPath,
  async () => {
    ui.closeEnv();
    const name = String(route.name ?? "");
    // 只有页面变化才移动焦点；筛选条件等 query-only 变化不能抢走键盘焦点。
    if (name === lastRouteName) {
      return;
    }
    lastRouteName = name;
    await nextTick();
    mainRef.value?.focus({ preventScroll: true });
  },
);

function syncSidebarScrollLock(): void {
  if (typeof document === "undefined") {
    return;
  }
  document.body.style.overflow = ui.narrow && ui.sidebarOpen ? "hidden" : "";
}

watch(() => [ui.narrow, ui.sidebarOpen], syncSidebarScrollLock);

onMounted(() => {
  window.addEventListener("keydown", onKeydown);
  document.addEventListener("pointerdown", onDocumentPointer);
  syncSidebarScrollLock();
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
  document.removeEventListener("pointerdown", onDocumentPointer);
  document.body.style.overflow = "";
});
</script>

<template>
  <a
    v-if="!(ui.narrow && ui.sidebarOpen)"
    class="skip-link"
    href="#main-content"
    @click="skipToMain"
  >
    跳到主要内容
  </a>
  <div class="app-shell">
    <ProjectSidebar
      ref="sidebarRef"
      :inert="ui.narrow && !ui.sidebarOpen"
      :projects="workspace.projects"
      :current-project-id="workspace.currentProjectId"
      :current-task-id="taskScoped ? (workspace.currentTask?.id ?? null) : null"
      :running-task-id="workspace.runningTask?.id ?? null"
      :section="currentSection"
      :open="ui.sidebarOpen"
      :modal="ui.narrow && ui.sidebarOpen"
      @select-project="selectProject"
      @open-overview="(id) => openProjectSection(id, 'overview')"
      @open-issues="(id) => openProjectSection(id, 'issues')"
      @open-history="(id) => openProjectSection(id, 'history')"
      @open-quick="openQuick"
      @select-task="selectTask"
      @new-task="newTask"
      @add-project="addProject"
      @open-settings="openSettings"
      @open-task-details="openTaskDetails"
      @rename-task="renameTask"
      @delete-task="deleteTask"
      @close="closeSidebar"
    />
    <button
      v-if="ui.sidebarOpen"
      class="sidebar-backdrop"
      type="button"
      tabindex="-1"
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
        :back-route="backRoute"
        :section-title="sectionTitle"
        :missing-count="missingCount"
        :env-open="ui.envOpen"
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
        tabindex="-1"
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
            missingCount === null
              ? "还没有检查结果；打开设置重新检查。"
              : missingCount > 0
                ? `${missingCount} 项未接入；本地记录、证据与报告仍可用。`
                : "全部检查通过。"
          }}
        </p>
        <RouterLink
          v-if="projectId"
          :to="`/projects/${projectId}/settings?section=settings-environment`"
          class="link-btn"
        >
          打开环境设置
        </RouterLink>
      </div>
      <div class="workspace-main">
        <main id="main-content" ref="mainRef" class="main" tabindex="-1">
          <div v-if="workspace.loading && !workspace.initialized" class="page">
            <LoadingState text="正在读取工作空间…" :lines="3" />
          </div>
          <RouterView v-else />
        </main>
      </div>
    </div>
  </div>
  <Toast />
  <ConfirmDialog />
</template>
