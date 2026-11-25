<script setup lang="ts">
import { ref } from "vue";
import { RouterLink } from "vue-router";

import Icon from "./Icon.vue";

defineProps<{
  projectId: string | null;
  projectName: string;
  taskTitle: string;
  taskRoute: string | null;
  backRoute: string | null;
  sectionTitle: string;
  missingCount: number | null;
  envOpen?: boolean;
  sidebarOpen: boolean;
  theme: "light" | "dark";
}>();

const emit = defineEmits<{
  toggleSidebar: [];
  toggleEnv: [];
  openCommandPalette: [];
  toggleTheme: [];
}>();

const toggleButton = ref<HTMLButtonElement | null>(null);
const envButton = ref<HTMLButtonElement | null>(null);

function focusToggle(): void {
  toggleButton.value?.focus();
}

function focusEnv(): void {
  envButton.value?.focus();
}

function isEnvButtonTarget(target: EventTarget | null): boolean {
  return target instanceof Node && Boolean(envButton.value?.contains(target));
}

defineExpose({ focusToggle, focusEnv, isEnvButtonTarget });
</script>

<template>
  <header class="workspace-head">
    <button
      ref="toggleButton"
      class="sidebar-toggle"
      type="button"
      aria-label="打开项目与任务"
      aria-controls="project-sidebar"
      :aria-expanded="sidebarOpen"
      @click="emit('toggleSidebar')"
    >
      <Icon name="menu" :size="16" />
    </button>

    <nav class="breadcrumb" aria-label="当前位置">
      <template v-if="projectId">
        <RouterLink
          v-if="backRoute"
          class="breadcrumb-back"
          :to="backRoute"
          aria-label="返回上一级"
        >
          <Icon name="arrow-left" :size="13" />
          返回上一级
        </RouterLink>
        <span class="breadcrumb-back-separator" aria-hidden="true">/</span>
        <RouterLink
          class="breadcrumb-project"
          :to="`/projects/${projectId}/overview`"
          >{{ projectName }}</RouterLink
        >
        <span class="breadcrumb-separator" aria-hidden="true">/</span>
        <template v-if="taskTitle">
          <RouterLink
            v-if="taskRoute"
            class="breadcrumb-task"
            :to="taskRoute"
            >{{ taskTitle }}</RouterLink
          >
          <span v-else class="breadcrumb-task">{{ taskTitle }}</span>
          <span class="breadcrumb-separator" aria-hidden="true">/</span>
        </template>
        <span class="breadcrumb-current" aria-current="page">{{ sectionTitle }}</span>
      </template>
      <template v-else>
        <span class="breadcrumb-current" aria-current="page">{{ sectionTitle }}</span>
      </template>
    </nav>

    <div class="workspace-actions">
      <button
        class="cmd-trigger-btn"
        type="button"
        aria-label="打开搜索或命令"
        @click="emit('openCommandPalette')"
      >
        <Icon name="search" :size="14" />
        <span>搜索或命令</span>
        <kbd class="cmd-kbd">Ctrl K</kbd>
      </button>

      <button
        ref="envButton"
        class="env-button"
        type="button"
        aria-label="查看环境自检"
        :aria-controls="envOpen ? 'env-popover' : undefined"
        :aria-expanded="envOpen ? 'true' : 'false'"
        @click="emit('toggleEnv')"
      >
        <span
          class="env-dot"
          :class="{ 'is-warn': missingCount === null || missingCount > 0 }"
          aria-hidden="true"
        ></span>
        <span class="env-text">
          {{
            missingCount === null
              ? "未检查"
              : missingCount > 0
                ? `${missingCount} 项待配置`
                : "环境就绪"
          }}
        </span>
      </button>

      <button
        class="header-icon-btn"
        type="button"
        :aria-label="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
        :title="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
        @click="emit('toggleTheme')"
      >
        <Icon :name="theme === 'dark' ? 'sun' : 'moon'" :size="15" />
      </button>
    </div>
  </header>
</template>
