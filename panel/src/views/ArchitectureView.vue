<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";

import CodeGraphViewer from "@/components/CodeGraphViewer.vue";
import ErrorState from "@/components/ErrorState.vue";
import GitStatusBar from "@/components/GitStatusBar.vue";
import Icon from "@/components/Icon.vue";
import PageHeader from "@/components/PageHeader.vue";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();

const project = computed(() => workspace.currentProject);
const projectId = computed(() => project.value?.id ?? "");
const analyzing = computed(
  () =>
    Boolean(project.value) &&
    (project.value?.analysisStatus === "analyzing" ||
      workspace.busyKey(`analysis:${projectId.value}`)),
);

async function refreshAll(): Promise<void> {
  if (!projectId.value) {
    return;
  }
  try {
    await workspace.refreshProject(projectId.value);
    ui.showToast("架构与仓库视图已更新。", "success");
  } catch {
    // store 已提示。
  }
}

async function analyze(): Promise<void> {
  if (!projectId.value) {
    return;
  }
  try {
    await workspace.analyzeProject(projectId.value);
  } catch {
    // store 已提示。
  }
}

function openSource(file: string, line: number): void {
  void workspace.openSourceLocation(file, line);
}

function testAffected(goalText: string): void {
  if (!projectId.value) {
    return;
  }
  void router.push(
    `/projects/${projectId.value}/tasks/new?mode=target&goal=${encodeURIComponent(goalText)}`,
  );
}
</script>

<template>
  <div v-if="!project" class="page">
    <ErrorState
      title="未选择项目"
      text="请先在左侧选择一个项目，再查看模块与调用链架构视图。"
      retry-label="建立新项目"
      @retry="router.push('/setup')"
    />
  </div>
  <div v-else class="page page-wide">
    <PageHeader
      title="项目架构"
      :subtitle="project.name"
    >
      <template #actions>
        <button
          class="btn btn-ghost btn-sm"
          type="button"
          @click="router.push(`/projects/${projectId}/overview`)"
        >
          <Icon name="arrow-left" :size="14" />
          返回概览
        </button>
        <button
          class="btn btn-sm"
          type="button"
          :disabled="analyzing"
          :aria-busy="analyzing"
          @click="analyze"
        >
          <Icon name="refresh" :size="14" />
          {{ analyzing ? "扫描中…" : "重新扫描" }}
        </button>
        <button
          class="btn btn-primary btn-sm"
          type="button"
          @click="ui.openNewTaskDrawer()"
        >
          <Icon name="plus" :size="14" />
          新建测试任务
        </button>
      </template>
    </PageHeader>

    <GitStatusBar
      :local-git="project.localGit ?? null"
      :code-graph="project.codeGraph ?? null"
      :dirty-files="workspace.dirtyFiles"
      :refreshing="workspace.loading"
      @refresh="refreshAll"
      @test-affected="testAffected"
      @open-source="openSource"
    />

    <CodeGraphViewer
      :graph="project.codeGraph ?? null"
      :project-name="project.name"
      :can-open-source="workspace.mode === 'host'"
      @open-source="openSource"
      @test-affected="testAffected"
      @refresh="refreshAll"
    />
  </div>
</template>
