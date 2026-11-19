<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, RouterView } from "vue-router";

import EmptyState from "@/components/EmptyState.vue";
import { useWorkspaceStore } from "@/stores/workspace";

const workspace = useWorkspaceStore();
const project = computed(() => workspace.currentProject);
</script>

<template>
  <div v-if="project" class="project-layout">
    <RouterView />
  </div>
  <div v-else class="page">
    <EmptyState title="项目不存在" text="这个项目可能已被移除；回到项目列表重新选择。">
      <RouterLink class="btn btn-primary btn-sm" to="/setup">回到项目入口</RouterLink>
    </EmptyState>
  </div>
</template>
