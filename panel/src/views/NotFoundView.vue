<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, useRoute } from "vue-router";

import EmptyState from "@/components/EmptyState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { useWorkspaceStore } from "@/stores/workspace";

const route = useRoute();
const workspace = useWorkspaceStore();
const projectId = computed(() => workspace.currentProjectId);
</script>

<template>
  <div class="page">
    <PageHeader title="页面不存在" kicker="404" :subtitle="`没有找到 ${route.fullPath}`" />
    <EmptyState title="这个地址没有对应页面" text="检查地址，或回到项目概览继续。">
      <RouterLink v-if="projectId" class="btn btn-sm" :to="`/projects/${projectId}/overview`">
        回到项目概览
      </RouterLink>
      <RouterLink class="btn btn-primary btn-sm" to="/setup">项目入口</RouterLink>
    </EmptyState>
  </div>
</template>
