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
}>();

const emit = defineEmits<{
  toggleSidebar: [];
  toggleEnv: [];
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
                ? `${missingCount} 项未接入`
                : "环境正常"
          }}
        </span>
      </button>
      <RouterLink
        v-if="projectId"
        class="icon-btn"
        :to="`/projects/${projectId}/settings`"
        aria-label="打开设置"
      >
        <Icon name="settings" :size="16" />
      </RouterLink>
    </div>
  </header>
</template>
