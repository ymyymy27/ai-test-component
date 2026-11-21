<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import { projectStatus } from "@/domain/derive";
import { taskStageLabel } from "@/domain/stateMachine";
import type { Project } from "@/domain/types";
import Icon from "./Icon.vue";

const props = defineProps<{
  projects: Project[];
  currentProjectId: string | null;
  currentTaskId: string | null;
  runningTaskId: string | null;
  section: string;
  open: boolean;
  /** 窄屏抽屉打开时把 Tab 焦点限制在侧栏内。 */
  modal?: boolean;
}>();

const emit = defineEmits<{
  selectProject: [projectId: string];
  openOverview: [projectId: string];
  openIssues: [projectId: string];
  openHistory: [projectId: string];
  openQuick: [projectId: string];
  selectTask: [projectId: string, taskId: string];
  newTask: [projectId: string];
  addProject: [];
  openSettings: [projectId: string];
  openTaskDetails: [projectId: string, taskId: string];
  renameTask: [projectId: string, taskId: string, title: string];
  deleteTask: [projectId: string, taskId: string, title: string];
  close: [];
}>();

const route = useRoute();
const query = ref("");
const asideRef = ref<HTMLElement | null>(null);
const searchInput = ref<HTMLInputElement | null>(null);
const closeButton = ref<HTMLButtonElement | null>(null);
const menuTaskId = ref<string | null>(null);
const projectCollapsed = ref<Record<string, boolean>>({});
const menuRef = ref<HTMLElement | null>(null);
const menuButtons = new Map<string, HTMLButtonElement>();
const menuStyle = ref<Record<string, string>>({});
const PROJECT_COLLAPSE_PREFIX = "aitest.project-collapsed.";
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
        project.id === props.currentProjectId ||
        project.name.toLowerCase().includes(text) ||
        project.root.toLowerCase().includes(text),
    );
});
const tasks = computed(() => {
  const text = query.value.trim().toLowerCase();
  return (currentProject.value?.tasks ?? [])
    .filter((task) => task.stage !== "fresh")
    .filter((task) => !text || task.title.toLowerCase().includes(text));
});
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
  const willOpen = menuTaskId.value !== taskId;
  menuTaskId.value = willOpen ? taskId : null;
  if (willOpen) {
    // 浮层移到 body，避免被侧栏滚动容器裁切；靠近底部时向上展开。
    const button = event.currentTarget;
    if (button instanceof HTMLElement) {
      const rect = button.getBoundingClientRect();
      const width = 148;
      const estimatedHeight = 128;
      const left = Math.min(
        Math.max(8, rect.right - width),
        Math.max(8, window.innerWidth - width - 8),
      );
      const below = rect.bottom + 4;
      const top =
        below + estimatedHeight > window.innerHeight
          ? Math.max(8, rect.top - estimatedHeight - 4)
          : below;
      menuStyle.value = {
        position: "fixed",
        top: `${top}px`,
        left: `${left}px`,
        width: `${width}px`,
      };
    }
    void nextTick(() => {
      const first = menuRef.value?.querySelector<HTMLButtonElement>(
        'button[role="menuitem"]:not(:disabled)',
      );
      first?.focus();
    });
  }
}

function setMenuRef(element: unknown): void {
  menuRef.value = element instanceof HTMLElement ? element : null;
}

function setMenuButton(taskId: string, element: unknown): void {
  if (element instanceof HTMLButtonElement) {
    menuButtons.set(taskId, element);
  } else {
    menuButtons.delete(taskId);
  }
}

function onTaskMenuKeydown(event: KeyboardEvent, taskId: string): void {
  const menu = event.currentTarget;
  if (!(menu instanceof HTMLElement)) {
    return;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    menuTaskId.value = null;
    void nextTick(() => menuButtons.get(taskId)?.focus());
    return;
  }
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
    return;
  }
  const items = Array.from(
    menu.querySelectorAll<HTMLButtonElement>('button[role="menuitem"]:not(:disabled)'),
  );
  if (items.length === 0) {
    return;
  }
  event.preventDefault();
  const current = items.indexOf(document.activeElement as HTMLButtonElement);
  const next =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? items.length - 1
        : event.key === "ArrowDown"
          ? (current + 1) % items.length
          : (current - 1 + items.length) % items.length;
  items[next]?.focus();
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
  if (target instanceof Element && target.closest(".side-task-row, .side-task-menu")) {
    return;
  }
  menuTaskId.value = null;
}

function onMenuFocusout(event: FocusEvent): void {
  const next = event.relatedTarget;
  if (next instanceof Node && event.currentTarget instanceof HTMLElement) {
    if (event.currentTarget.contains(next)) {
      return;
    }
  }
  menuTaskId.value = null;
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape" && menuTaskId.value) {
    const taskId = menuTaskId.value;
    menuTaskId.value = null;
    void nextTick(() => menuButtons.get(taskId)?.focus());
  }
}

function focusableItems(): HTMLElement[] {
  const root = asideRef.value;
  if (!root) {
    return [];
  }
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
    ),
  ).filter((element) => element.offsetParent !== null || element === document.activeElement);
}

function onAsideKeydown(event: KeyboardEvent): void {
  if (!props.modal || event.key !== "Tab") {
    return;
  }
  const items = focusableItems();
  if (items.length === 0) {
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}

function clearSearch(): void {
  query.value = "";
  searchInput.value?.focus();
}

watch(
  () => route.fullPath,
  () => {
    menuTaskId.value = null;
  },
);

onMounted(() => {
  document.addEventListener("click", closeMenuOnOutside);
  document.addEventListener("keydown", onDocumentKeydown);
});
onBeforeUnmount(() => {
  document.removeEventListener("click", closeMenuOnOutside);
  document.removeEventListener("keydown", onDocumentKeydown);
});

watch(
  () => props.currentProjectId,
  (projectId) => {
    if (!projectId) {
      return;
    }
    try {
      projectCollapsed.value = {
        ...projectCollapsed.value,
        [projectId]: localStorage.getItem(`${PROJECT_COLLAPSE_PREFIX}${projectId}`) === "1",
      };
    } catch {
      // 本地存储不可用时保持展开。
    }
  },
  { immediate: true },
);

function isProjectCollapsed(projectId: string): boolean {
  return projectCollapsed.value[projectId] === true;
}

function setProjectCollapsed(projectId: string, collapsed: boolean): void {
  projectCollapsed.value = { ...projectCollapsed.value, [projectId]: collapsed };
  try {
    localStorage.setItem(`${PROJECT_COLLAPSE_PREFIX}${projectId}`, collapsed ? "1" : "0");
  } catch {
    // 本地存储不可用时只保留当前状态。
  }
}

function toggleProject(projectId: string): void {
  setProjectCollapsed(projectId, !isProjectCollapsed(projectId));
}

function handleProjectClick(project: Project): void {
  if (project.id !== props.currentProjectId) {
    if (isProjectCollapsed(project.id)) {
      setProjectCollapsed(project.id, false);
    }
    selectProject(project.id);
    return;
  }
  if (props.section === "overview") {
    toggleProject(project.id);
    return;
  }
  selectProject(project.id);
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
  <aside
    id="project-sidebar"
    ref="asideRef"
    class="sidebar"
    :class="{ 'is-open': open }"
    aria-label="项目与任务"
    @keydown="onAsideKeydown"
  >
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
      <span class="visually-hidden">搜索项目和任务</span>
      <input
        ref="searchInput"
        v-model="query"
        type="search"
        placeholder="搜索项目、路径或任务"
        autocomplete="off"
      />
    </label>
    <button v-if="query" class="link-btn sidebar-clear-search" type="button" @click="clearSearch">
      清除搜索
    </button>

    <div class="sidebar-scroll">
      <div class="sidebar-section-label">
        <span>项目</span>
        <span>{{ visibleProjects.length }}</span>
      </div>
      <p v-if="visibleProjects.length === 0" class="side-empty">
        {{ projects.length === 0 ? "还没有项目。先建立一个本地目录。" : "没有匹配的项目。" }}
      </p>
      <div
        v-for="project in visibleProjects"
        :key="project.id"
        class="side-project"
        :class="{ 'is-collapsed': isProjectCollapsed(project.id) }"
      >
        <button
          class="side-project-row"
          type="button"
          :aria-expanded="
            project.id === currentProjectId ? !isProjectCollapsed(project.id) : undefined
          "
          :aria-controls="
            project.id === currentProjectId ? `project-body-${project.id}` : undefined
          "
          :title="
            project.id === currentProjectId
              ? isProjectCollapsed(project.id)
                ? '点击展开项目内容'
                : '点击收起项目内容'
              : '打开项目概览'
          "
          @click="handleProjectClick(project)"
        >
          <span class="side-status-dot" :class="`is-${toneFor(project)}`" aria-hidden="true"></span>
          <span class="side-project-main">
            <span class="side-project-name">{{ project.name }}</span>
            <span class="side-project-meta">{{ projectStatus(project).label }}</span>
          </span>
        </button>
        <div
          v-if="project.id === currentProjectId"
          v-show="!isProjectCollapsed(project.id)"
          :id="`project-body-${project.id}`"
        >
        <div
          class="side-nav-block"
        >
          <div class="side-task-head">
            <span class="side-task-label">视图</span>
          </div>
          <div class="side-tree-body">
            <nav class="side-nav" aria-label="项目分区">
              <button
                class="side-nav-item"
                type="button"
                :class="{ 'is-active': section === 'overview' }"
                :aria-current="section === 'overview' ? 'page' : undefined"
                @click="emit('openOverview', project.id)"
              >
                <Icon name="overview" :size="14" />
                <span class="side-nav-label">概览</span>
              </button>
              <button
                class="side-nav-item"
                type="button"
                :class="{ 'is-active': section === 'issues' }"
                :aria-current="section === 'issues' ? 'page' : undefined"
                @click="emit('openIssues', project.id)"
              >
                <Icon name="warn" :size="14" />
                <span class="side-nav-label">问题</span>
                <span v-if="openIssueCount" class="side-nav-count">{{ openIssueCount }}</span>
              </button>
              <button
                class="side-nav-item"
                type="button"
                :class="{ 'is-active': section === 'history' }"
                :aria-current="section === 'history' ? 'page' : undefined"
                @click="emit('openHistory', project.id)"
              >
                <Icon name="history" :size="14" />
                <span class="side-nav-label">运行记录</span>
                <span v-if="runCount" class="side-nav-count">{{ runCount }}</span>
              </button>
              <div class="sidebar-section-label"><span>项目设置</span></div>
              <button
                class="side-nav-item"
                type="button"
                :class="{ 'is-active': section === 'quick' }"
                :aria-current="section === 'quick' ? 'page' : undefined"
                @click="emit('openQuick', project.id)"
              >
                <Icon name="shield" :size="14" />
                <span class="side-nav-label">快速检查</span>
              </button>
              <button
                class="side-nav-item"
                type="button"
                :class="{ 'is-active': section === 'settings' }"
                :aria-current="section === 'settings' ? 'page' : undefined"
                @click="emit('openSettings', project.id)"
              >
                <Icon name="settings" :size="14" />
                <span class="side-nav-label">设置</span>
              </button>
            </nav>
          </div>
        </div>
        <div
          v-if="project.id === currentProjectId"
          class="side-task-list"
          aria-label="当前项目的任务"
        >
          <div class="side-task-head">
            <span class="side-task-label">
              任务
              <span v-if="tasks.length" class="side-task-count">{{ tasks.length }}</span>
            </span>
            <div class="side-task-actions">
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
          </div>
          <div class="side-task-body">
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
              :title="task.title"
              :aria-current="task.id === currentTaskId ? 'page' : undefined"
              :disabled="Boolean(runningTaskId && runningTaskId !== task.id)"
              @click="selectTask(task.id)"
            >
              <span class="side-task-main">
                <span class="side-task-title">{{ task.title }}</span>
                <span class="side-task-meta">{{ taskStageLabel(task) }}</span>
              </span>
            </button>
            <button
              v-if="renamingTaskId !== task.id"
              :ref="(element) => setMenuButton(task.id, element)"
              class="side-task-more"
              type="button"
              aria-label="任务操作"
              aria-haspopup="menu"
              :aria-expanded="menuTaskId === task.id"
              @click.stop="toggleTaskMenu(task.id, $event)"
            >
              <Icon name="more" :size="14" />
            </button>
            <Teleport to="body">
              <div
                v-if="menuTaskId === task.id"
                :ref="setMenuRef"
                class="side-task-menu"
                :style="menuStyle"
                role="menu"
                @keydown="onTaskMenuKeydown($event, task.id)"
                @focusout="onMenuFocusout"
              >
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
                <button
                  class="is-danger"
                  type="button"
                  role="menuitem"
                  :disabled="Boolean(runningTaskId)"
                  @click="menuTaskId = null; emit('deleteTask', project.id, task.id, task.title)"
                >
                  删除
                </button>
              </div>
            </Teleport>
          </div>
          <p v-if="tasks.length === 0 && !draft" class="side-empty">
            {{ query ? "没有匹配的任务。" : "还没有任务。点“新建”开始。" }}
          </p>
          </div>
        </div>
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
