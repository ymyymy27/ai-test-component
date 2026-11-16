<script setup lang="ts">
import { computed } from "vue";

import type { Project, ProjectSummary } from "@/api/types";
import Icon from "./Icon.vue";
import StatusDot from "./StatusDot.vue";

const props = defineProps<{
  projects: ProjectSummary[];
  project: Project | null;
  env: "ok" | "warn" | "error" | "unknown";
}>();

defineEmits<{ select: [projectId: string]; settings: [] }>();

const envLabel = computed(() => {
  switch (props.env) {
    case "ok":
      return "环境正常";
    case "warn":
      return "部分能力缺失";
    case "error":
      return "环境异常";
    default:
      return "检查中";
  }
});

const envTone = computed(() => {
  switch (props.env) {
    case "ok":
      return "ok" as const;
    case "warn":
      return "warn" as const;
    case "error":
      return "error" as const;
    default:
      return "idle" as const;
  }
});
</script>

<template>
  <header class="topbar">
    <div class="ident">
      <span class="mark" aria-hidden="true"><i></i><i></i><i></i></span>
      <span v-if="!project" class="ident-text">AI 辅助测试</span>
    </div>

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

    <div class="right">
      <button type="button" class="env" :title="`${envLabel}，打开设置查看明细`" @click="$emit('settings')">
        <StatusDot :tone="envTone" :label="envLabel" />
      </button>
      <button type="button" class="icon-btn" aria-label="打开设置" title="设置" @click="$emit('settings')">
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
  height: 48px;
  padding: 0 var(--space-4);
  background: rgba(14, 16, 18, 0.92);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(8px);
}

.ident {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.mark {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  width: 18px;
  height: 18px;
  padding: 0 3px;
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  background: var(--surface-raised);
}

.mark i {
  display: block;
  height: 1px;
  background: var(--text-3);
}

.mark i:nth-child(2) {
  width: 70%;
  background: var(--accent);
}

.ident-text {
  font-size: var(--fs-13);
  font-weight: 600;
  letter-spacing: 0.01em;
  white-space: nowrap;
}

.project-select {
  position: relative;
  display: inline-flex;
  align-items: center;
  min-width: 0;
  max-width: 320px;
}

.project-select select {
  appearance: none;
  width: 100%;
  min-height: 30px;
  padding: 4px 26px 4px 10px;
  background: var(--surface-raised);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-13);
  text-overflow: ellipsis;
  cursor: pointer;
}

.project-select select:hover {
  border-color: #454d55;
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
  padding: 5px 8px;
  background: none;
  border: 0;
  border-radius: var(--r-control);
}

.env:hover {
  background: var(--surface-raised);
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
    padding: 0 var(--space-3);
    gap: var(--space-2);
  }

  .ident-text {
    display: none;
  }

  .project-select {
    max-width: 100%;
    flex: 1 1 auto;
  }

  .env {
    padding: 5px 6px;
  }

  .env :deep(.status) {
    font-size: 0;
    gap: 0;
  }

  .env :deep(.dot) {
    width: 7px;
    height: 7px;
  }
}
</style>
