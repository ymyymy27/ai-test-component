<script setup lang="ts">
import { ref } from "vue";
import { RouterLink } from "vue-router";

import Icon from "./Icon.vue";

defineProps<{
  projectId: string | null;
  projectName: string;
  taskTitle: string;
  taskRoute: string | null;
  sectionTitle: string;
  missingCount: number;
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

function isEnvButtonTarget(target: EventTarget | null): boolean {
  return target instanceof Node && Boolean(envButton.value?.contains(target));
}

defineExpose({ focusToggle, isEnvButtonTarget });
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
        <RouterLink :to="`/projects/${projectId}/overview`" aria-current-value="false">{{
          projectName
        }}</RouterLink>
        <span class="breadcrumb-separator" aria-hidden="true">/</span>
        <template v-if="taskTitle">
          <RouterLink v-if="taskRoute" :to="taskRoute" aria-current-value="false">{{
            taskTitle
          }}</RouterLink>
          <span v-else>{{ taskTitle }}</span>
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
        aria-controls="env-popover"
        @click="emit('toggleEnv')"
      >
        <span class="env-dot" :class="{ 'is-warn': missingCount > 0 }" aria-hidden="true"></span>
        <span class="env-text">{{ missingCount > 0 ? `${missingCount} 项缺失` : "环境正常" }}</span>
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
