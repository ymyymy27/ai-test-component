<script setup lang="ts">
import { computed } from "vue";

import type { Project, ProjectSummary } from "@/api/types";
import { THEMES, themeMode, toggleTheme } from "@/state/theme";
import Icon from "./Icon.vue";

const props = defineProps<{
  projects: ProjectSummary[];
  project: Project | null;
  env: "ok" | "warn" | "error" | "unknown";
}>();

defineEmits<{ select: [projectId: string]; add: []; settings: [] }>();

const envLabel = computed(() => {
  switch (props.env) {
    case "ok":
      return "环境正常";
    case "warn":
      return "部分功能不可用";
    case "error":
      return "环境有问题";
    default:
      return "正在检查环境";
  }
});

const envTone = computed(() => {
  switch (props.env) {
    case "ok":
      return "led-ok";
    case "warn":
      return "led-warn";
    case "error":
      return "led-error";
    default:
      return "led-idle";
  }
});

const themeName = computed(
  () => THEMES.find((item) => item.id === themeMode.value)?.name ?? "跟随系统",
);
</script>

<template>
  <header class="topbar">
    <label v-if="project" class="project-select">
      <span class="sr-only">切换项目</span>
      <select
        :value="project.project_id"
        @change="$emit('select', ($event.target as HTMLSelectElement).value)"
      >
        <option v-if="!projects.some((item) => item.project_id === project?.project_id)"
          :value="project.project_id">
          {{ project.name }}
        </option>
        <option v-for="item in projects" :key="item.project_id" :value="item.project_id">
          {{ item.name }}
        </option>
      </select>
      <span class="select-chev" aria-hidden="true"><Icon name="chevron-down" :size="14" /></span>
    </label>

    <button
      type="button"
      class="icon-btn add"
      aria-label="添加项目"
      title="添加项目"
      data-testid="add-project"
      @click="$emit('add')"
    >
      <Icon name="plus" :size="15" />
    </button>

    <div class="right">
      <button
        v-if="env === 'warn' || env === 'error'"
        type="button"
        class="env"
        :title="envLabel"
        :aria-label="envLabel"
        @click="$emit('settings')"
      >
        <span class="env-dot" :class="envTone" aria-hidden="true"></span>
        <span class="env-text">{{ envLabel }}</span>
      </button>
      <button
        type="button"
        class="icon-btn"
        aria-label="切换主题"
        :title="`切换主题（当前：${themeName}）`"
        @click="toggleTheme"
      >
        <Icon name="theme" :size="16" />
      </button>
      <button
        type="button"
        class="icon-btn"
        aria-label="打开设置"
        title="设置"
        @click="$emit('settings')"
      >
        <Icon name="settings" :size="17" />
      </button>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: var(--header-h);
  padding: 0 var(--space-4);
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}

.project-select {
  position: relative;
  display: inline-flex;
  align-items: center;
  min-width: 0;
  max-width: 300px;
}

.project-select select {
  appearance: none;
  width: 100%;
  min-height: 30px;
  padding: 4px 26px 4px 8px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--r-control);
  color: var(--text-2);
  font-size: var(--fs-13);
  text-overflow: ellipsis;
  cursor: pointer;
}

.project-select select:hover,
.project-select select:focus-visible {
  background: var(--surface-raised);
  border-color: var(--line);
  color: var(--text);
}

.select-chev {
  position: absolute;
  right: 7px;
  color: var(--text-3);
  pointer-events: none;
}

.right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.env {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 6px;
  background: none;
  border: 1px solid transparent;
  border-radius: var(--r-control);
  font-size: var(--fs-12);
  color: var(--text-3);
}

.env:hover {
  background: var(--surface-raised);
  border-color: var(--line);
  color: var(--text-2);
}

.env-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-3);
}

.led-ok {
  background: var(--passed);
}

.led-warn {
  background: var(--incomplete);
}

.led-error {
  background: var(--failed);
}

.icon-btn.add {
  flex: none;
  width: 26px;
  height: 26px;
  color: var(--text-3);
}

.icon-btn {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  background: none;
  border: 1px solid transparent;
  border-radius: var(--r-control);
  color: var(--text-2);
}

.icon-btn:hover {
  background: var(--surface-raised);
  border-color: var(--line);
  color: var(--text);
}

@media (max-width: 520px) {
  .topbar {
    gap: var(--space-2);
    padding: 0 var(--space-3);
  }

  .project-select {
    max-width: 100%;
    flex: 1 1 auto;
  }

  .env-text {
    display: none;
  }
}
</style>
