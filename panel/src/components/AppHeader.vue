<script setup lang="ts">
import { computed } from "vue";

import type { Project, ProjectSummary } from "@/api/types";
import Icon from "./Icon.vue";

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

const envCode = computed(() => {
  switch (props.env) {
    case "ok":
      return "READY";
    case "warn":
      return "LIMITED";
    case "error":
      return "ERROR";
    default:
      return "CHECK";
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
    <div class="brand" aria-label="AITEST">
      <span class="brand-name">AITEST</span>
      <span class="brand-cursor" aria-hidden="true"></span>
      <span class="brand-sep" aria-hidden="true">/</span>
    </div>

    <label v-if="project" class="project-select">
      <span class="project-kicker" aria-hidden="true">PROJECT</span>
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
        <span class="env-led" :class="`led-${envTone}`" aria-hidden="true"></span>
        <span class="env-code">{{ envCode }}</span>
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
  height: var(--header-h);
  padding: 0 var(--space-4);
  background: var(--bg);
  border-bottom: 1px solid var(--line);
}

.brand {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: none;
}

.brand-name {
  font-family: var(--font-mono);
  font-size: var(--fs-11);
  font-weight: 600;
  letter-spacing: 0.2em;
  color: var(--text);
}

.brand-cursor {
  width: 6px;
  height: 12px;
  background: var(--accent);
  animation: cursor-blink 1.2s step-end infinite;
}

.brand-sep {
  margin-left: 2px;
  font-family: var(--font-mono);
  font-size: var(--fs-11);
  color: var(--text-3);
}

@keyframes cursor-blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0;
  }
}

.project-select {
  position: relative;
  display: inline-flex;
  align-items: center;
  min-width: 0;
  max-width: 300px;
}

.project-kicker {
  margin-right: 6px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.14em;
  color: var(--text-3);
}

.project-select select {
  appearance: none;
  width: 100%;
  min-height: 30px;
  padding: 4px 26px 4px 8px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-13);
  text-overflow: ellipsis;
  cursor: pointer;
}

.project-select select:hover,
.project-select select:focus-visible {
  background: var(--surface-raised);
  border-color: var(--line);
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
  gap: 7px;
  padding: 5px 6px;
  background: none;
  border: 0;
  border-radius: 0;
}

.env-led {
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

.env-code {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.12em;
  color: var(--text-2);
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

  .project-kicker {
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
