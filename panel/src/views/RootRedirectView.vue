<script setup lang="ts">
import { onMounted } from "vue";
import { RouterLink, useRouter } from "vue-router";

import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import LoadingState from "@/components/LoadingState.vue";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();

async function resolve(): Promise<void> {
  await workspace.bootstrap();
  if (workspace.currentProjectId) {
    await router.replace(`/projects/${workspace.currentProjectId}/overview`);
    return;
  }
  await router.replace("/setup");
}

async function clearAndRetry(): Promise<void> {
  const answer = await ui.askConfirm({
    title: "清空本地面板数据？",
    body: "会删除本机保存的项目、任务、问题与报告。这个动作不能撤销；不会影响被测仓库。",
    confirmLabel: "清空并重新开始",
    tone: "danger",
  });
  if (answer === null) {
    return;
  }
  await workspace.clearData();
  await router.replace("/setup");
}

onMounted(() => {
  void resolve();
});
</script>

<template>
  <div class="page">
    <LoadingState
      v-if="workspace.status === 'loading' || workspace.status === 'idle'"
      text="正在读取本地面板数据…"
    />
    <ErrorState
      v-else-if="workspace.status === 'error'"
      title="本地数据无法读取"
      :text="workspace.error"
      retry-label="重新读取"
      @retry="resolve"
    >
      <button class="btn btn-sm" type="button" @click="clearAndRetry">
        清空本地数据并重新开始
      </button>
    </ErrorState>
    <EmptyState v-else title="还没有项目" text="登记一个本地目录后，面板会整理项目理解与目标候选。">
      <RouterLink class="btn btn-primary btn-sm" to="/setup">建立项目</RouterLink>
    </EmptyState>
  </div>
</template>
