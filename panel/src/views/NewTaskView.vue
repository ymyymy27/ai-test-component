<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";

import ErrorState from "@/components/ErrorState.vue";
import Icon from "@/components/Icon.vue";
import NewTaskComposer from "@/components/NewTaskComposer.vue";
import PageHeader from "@/components/PageHeader.vue";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();

const projectId = computed(() => String(route.params.projectId ?? ""));
const project = computed(() => workspace.currentProject);
const presetGoal = computed(() =>
  typeof route.query.goal === "string" ? route.query.goal : "",
);

onMounted(async () => {
  if (!project.value) {
    return;
  }
  if (String(route.query.mode ?? "") === "quick") {
    ui.openQuickCheckDrawer();
  }
  if (
    !workspace.busyKey(`analysis:${projectId.value}`) &&
    project.value.analysisStatus === "idle"
  ) {
    try {
      await workspace.analyzeProject(projectId.value);
    } catch {
      // store 已提示。
    }
  }
});

async function onDone(taskId: string, routeTarget: "plan" | "scope"): Promise<void> {
  await router.push(`/projects/${projectId.value}/tasks/${taskId}/${routeTarget}`);
}
</script>

<template>
  <div v-if="!project" class="page">
    <ErrorState
      title="项目不可用"
      text="回到项目概览重新选择项目。"
      retry-label="回到概览"
      @retry="router.push('/')"
    />
  </div>
  <div v-else class="page">
    <PageHeader
      title="新建测试"
      :subtitle="project.name"
    >
      <template #actions>
        <button
          class="btn btn-ghost btn-sm"
          type="button"
          @click="ui.openQuickCheckDrawer()"
        >
          <Icon name="bolt" :size="14" />
          快速检查
        </button>
        <button
          class="btn btn-ghost btn-sm"
          type="button"
          @click="router.push(`/projects/${projectId}/overview`)"
        >
          返回概览
        </button>
      </template>
    </PageHeader>

    <NewTaskComposer
      :project="project"
      :preset-goal="presetGoal"
      @done="onDone"
    />
  </div>
</template>
