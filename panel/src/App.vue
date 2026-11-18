<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";

import { isHostReady } from "@/api/host";
import EmptyState from "@/components/EmptyState.vue";
import ProjectDialog from "@/components/ProjectDialog.vue";
import SkeletonBlock from "@/components/SkeletonBlock.vue";
import HistoryView from "@/views/HistoryView.vue";
import IssuesView from "@/views/IssuesView.vue";
import SettingsView from "@/views/SettingsView.vue";
import TestView from "@/views/TestView.vue";
import {
  bootstrap,
  clearError,
  clearNotice,
  dismissBootError,
  openAddProject,
  setView,
  switchProject,
  workspace,
  type AppView,
} from "@/state/workspace";

const hostReady = computed(() => isHostReady());
const projectMenu = ref(false);
const projectWrap = ref<HTMLElement | null>(null);

onMounted(() => {
  if (hostReady.value) {
    void bootstrap();
  }
  document.addEventListener("click", onDocumentClick, true);
});

onUnmounted(() => {
  document.removeEventListener("click", onDocumentClick, true);
});

function onDocumentClick(event: MouseEvent): void {
  if (!projectMenu.value) {
    return;
  }
  const node = event.target as Node | null;
  if (node && projectWrap.value?.contains(node)) {
    return;
  }
  projectMenu.value = false;
}

function go(view: AppView): void {
  setView(view);
}

function pickProject(projectId: string): void {
  projectMenu.value = false;
  if (projectId !== workspace.project?.project_id) {
    void switchProject(projectId);
  }
}

function addProject(): void {
  projectMenu.value = false;
  openAddProject();
}
</script>

<template>
  <a class="skip-link" href="#main">跳到主内容</a>

  <header v-if="hostReady && workspace.ready" class="topbar">
    <div ref="projectWrap" class="project">
      <button
        type="button"
        class="project-btn"
        :aria-expanded="projectMenu"
        aria-haspopup="menu"
        @click="projectMenu = !projectMenu"
      >
        <span class="project-name">{{ workspace.project?.name ?? "还没有项目" }}</span>
        <span class="caret" aria-hidden="true">▾</span>
      </button>
      <div v-if="projectMenu" class="project-menu" role="menu">
        <button
          v-for="item in workspace.projects"
          :key="item.project_id"
          type="button"
          role="menuitem"
          class="menu-row"
          :class="{ on: item.project_id === workspace.project?.project_id }"
          @click="pickProject(item.project_id)"
        >
          <span class="mark">{{ item.project_id === workspace.project?.project_id ? "●" : "" }}</span>
          <span>{{ item.name }}</span>
        </button>
        <p v-if="!workspace.projects.length" class="menu-empty">还没有项目</p>
        <button type="button" role="menuitem" class="menu-row add" @click="addProject">
          <span class="mark">＋</span>
          <span>添加项目</span>
        </button>
      </div>
    </div>

    <nav class="nav" aria-label="主导航">
      <button
        type="button"
        class="nav-item"
        :class="{ on: workspace.view === 'test' }"
        @click="go('test')"
      >
        测试
      </button>
      <button
        type="button"
        class="nav-item"
        :class="{ on: workspace.view === 'issues' }"
        @click="go('issues')"
      >
        问题
      </button>
      <button
        type="button"
        class="nav-item"
        :class="{ on: workspace.view === 'history' }"
        @click="go('history')"
      >
        历史
      </button>
    </nav>

    <button
      type="button"
      class="nav-item settings"
      :class="{ on: workspace.view === 'settings' }"
      @click="go('settings')"
    >
      设置
    </button>
  </header>

  <main id="main" class="main">
    <EmptyState v-if="!hostReady" title="面板没有连接到编辑器" icon="info">
      <template #body>
        请从编辑器的“AI 测试”面板打开。当前页面只能预览，不能读取项目或运行测试。
      </template>
    </EmptyState>

    <div v-else-if="workspace.booting || !workspace.ready" class="boot">
      <p class="boot-title">测试台账</p>
      <SkeletonBlock :lines="4" label="正在读取项目与环境" />
    </div>

    <template v-else>
      <div v-if="workspace.notice" class="notice" role="status">
        <span>{{ workspace.notice }}</span>
        <button type="button" class="ui-link" @click="clearNotice">关闭</button>
      </div>
      <div v-if="workspace.bootError" class="notice error" role="alert">
        <span>{{ workspace.bootError.title }}。{{ workspace.bootError.next }}</span>
        <button type="button" class="ui-link" @click="dismissBootError">关闭</button>
      </div>
      <div v-if="workspace.lastError" class="notice error" role="alert">
        <span>{{ workspace.lastError.title }}。{{ workspace.lastError.next }}</span>
        <button type="button" class="ui-link" @click="clearError">关闭</button>
      </div>

      <TestView v-if="workspace.view === 'test'" />
      <IssuesView v-else-if="workspace.view === 'issues'" />
      <HistoryView v-else-if="workspace.view === 'history'" />
      <SettingsView v-else />
    </template>
  </main>

  <ProjectDialog v-if="workspace.addProjectOpen" />
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: var(--space-3);
  height: var(--header-h);
  padding: 0 var(--space-4);
  border-bottom: 1px solid var(--line);
  background: var(--bg);
}

.project {
  position: relative;
  min-width: 0;
}

.project-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 240px;
  padding: 4px 7px 4px 6px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm, 3px);
  background: transparent;
  color: var(--text);
  font-size: var(--fs-13);
  font-weight: 600;
  line-height: 1.3;
}

.project-btn:hover,
.project-btn[aria-expanded="true"] {
  border-color: var(--line-strong);
  background: var(--surface-raised);
}

.project-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.caret {
  flex: none;
  color: var(--text-3);
  font-size: 9px;
}

.project-menu {
  position: absolute;
  left: 0;
  top: 36px;
  z-index: 30;
  width: 250px;
  padding: 4px;
  border: 1px solid var(--line-strong);
  background: var(--surface);
  box-shadow: var(--shadow-pop);
}

.menu-row {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 7px;
  border: 0;
  background: transparent;
  color: var(--text-2);
  font-size: var(--fs-11);
  line-height: 1.4;
  text-align: left;
}

.menu-row:hover,
.menu-row.on {
  background: var(--surface-raised);
  color: var(--text);
}

.menu-row .mark {
  flex: none;
  width: 12px;
  color: var(--accent);
  font-size: 9px;
}

.menu-row.add {
  border-top: 1px solid var(--line);
  color: var(--text-3);
}

.menu-empty {
  padding: 6px 7px;
  color: var(--text-3);
  font-size: var(--fs-10);
}

.nav {
  display: flex;
  align-items: center;
  gap: 2px;
}

.nav-item {
  position: relative;
  padding: 6px 8px;
  border: 0;
  background: transparent;
  color: var(--text-3);
  font-size: var(--fs-11);
  line-height: 1;
}

.nav-item:hover {
  color: var(--text);
}

.nav-item.on {
  color: var(--text);
}

.nav-item.on::after {
  content: "";
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: -12px;
  height: 1px;
  background: var(--accent);
}

.settings {
  justify-self: end;
}

.main {
  width: 100%;
  min-height: calc(100vh - var(--header-h));
}

.boot {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5) var(--space-4);
}

.boot-title {
  font-size: var(--fs-18);
  font-weight: 600;
}

.notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin: var(--space-3) var(--space-4) 0;
  padding: 7px 10px;
  border-left: 2px solid var(--accent);
  background: var(--surface-raised);
  color: var(--text-2);
  font-size: var(--fs-11);
}

.notice.error {
  border-left-color: var(--failed);
}

@media (max-width: 520px) {
  .topbar {
    padding: 0 var(--space-3);
    gap: var(--space-2);
  }

  .project-btn {
    max-width: 132px;
  }

  .nav {
    gap: 0;
  }

  .nav-item {
    padding: 6px;
  }

  .notice {
    margin: var(--space-2) var(--space-3) 0;
  }
}
</style>
