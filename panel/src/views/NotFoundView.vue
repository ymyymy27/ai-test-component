<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, useRoute } from "vue-router";

import EmptyState from "@/components/EmptyState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { useWorkspaceStore } from "@/stores/workspace";

const route = useRoute();
const workspace = useWorkspaceStore();
const projectId = computed(() => {
  const match = route.fullPath.match(/\/projects\/([^/?#]+)/);
  return match?.[1] ?? workspace.currentProjectId;
});
</script>

<template>
  <div class="page">
    <PageHeader title="页面不存在" subtitle="这个地址可能已失效，或者对应内容已被移除。" />
    <p v-if="workspace.mode === 'local'" class="doc-note">
      本地演示模式：页面数据来自浏览器本地存储，未读取真实仓库。
    </p>
    <EmptyState title="检查地址后继续" text="可以回到当前项目概览，或重新建立项目。">
      <RouterLink
        v-if="projectId"
        class="btn btn-primary btn-sm"
        :to="`/projects/${projectId}/overview`"
      >
        回到项目概览
      </RouterLink>
      <RouterLink class="btn btn-sm" to="/setup">建立项目</RouterLink>
    </EmptyState>
  </div>
</template>
