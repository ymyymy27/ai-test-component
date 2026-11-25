<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";

import ModuleEditorDialog from "@/components/ModuleEditorDialog.vue";
import ProjectOverview from "@/components/ProjectOverview.vue";
import type { Module } from "@/domain/types";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();
const moduleOpen = ref(false);
const editingModule = ref<Module | null>(null);

const project = computed(() => workspace.currentProject);
const projectId = computed(() => project.value?.id ?? "");
const analyzing = computed(
  () =>
    Boolean(project.value) &&
    (project.value?.analysisStatus === "analyzing" ||
      workspace.busyKey(`analysis:${projectId.value}`)),
);
const analyzingBusy = computed(() => workspace.busyKey(`analysis:${projectId.value}`));

function routeForTask(taskId: string): string {
  if (!taskId) {
    return `/projects/${projectId.value}/overview`;
  }
  const task = project.value?.tasks.find((entry) => entry.id === taskId);
  if (!task || !projectId.value) {
    return `/projects/${projectId.value}/overview`;
  }
  switch (task.stage) {
    case "fresh":
      return `/projects/${projectId.value}/tasks/new`;
    case "scope":
      return `/projects/${projectId.value}/tasks/${taskId}/scope`;
    case "planned":
      return `/projects/${projectId.value}/tasks/${taskId}/plan`;
    case "running":
      return `/projects/${projectId.value}/tasks/${taskId}/run`;
    case "result":
      return `/projects/${projectId.value}/tasks/${taskId}/result`;
    default:
      return `/projects/${projectId.value}/overview`;
  }
}

async function openTask(taskId: string): Promise<void> {
  const task = project.value?.tasks.find((entry) => entry.id === taskId);
  if (!task) {
    return;
  }
  if (task.stage !== "fresh") {
    await workspace.setActiveTask(projectId.value, taskId);
  }
  await router.push(routeForTask(taskId));
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

function addModule(): void {
  editingModule.value = null;
  moduleOpen.value = true;
}

function editModule(module: Module): void {
  editingModule.value = module;
  moduleOpen.value = true;
}

async function toggleFeature(featureName: string, ignored: boolean): Promise<void> {
  if (!projectId.value) {
    return;
  }
  try {
    await workspace.setFeatureIgnored(projectId.value, featureName, ignored);
    ui.showToast(
      ignored ? "已忽略该业务功能；可在“已忽略”列表恢复。" : "业务功能已恢复。",
      "info",
    );
  } catch {
    // store 已提示。
  }
}

async function saveModule(input: Parameters<typeof workspace.addModule>[1]): Promise<void> {
  if (!projectId.value) {
    return;
  }
  try {
    if (editingModule.value) {
      await workspace.updateModule(projectId.value, editingModule.value.id, input);
    } else {
      await workspace.addModule(projectId.value, input);
    }
    moduleOpen.value = false;
  } catch {
    // store 已提示；对话框保留输入，可修改后重试。
  }
}

function openSource(file: string, line: number): void {
  void workspace.openSourceLocation(file, line);
}

function refreshGraph(): void {
  if (!projectId.value) {
    return;
  }
  void workspace.refreshProject(projectId.value);
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
  <div v-if="project">
    <ProjectOverview
      :project="project"
      :analyzing="analyzing"
      :analysis-error="project.analysisError"
      :analyzing-busy="analyzingBusy"
      :dirty-files="workspace.dirtyFiles"
      @new-task="ui.openNewTaskDrawer()"
      @open-quick-check="ui.openQuickCheckDrawer()"
      @open-architecture="router.push(`/projects/${projectId}/architecture`)"
      @open-task="openTask"
      @open-run="(runId) => router.push(`/projects/${projectId}/history?run=${runId}`)"
      @open-issue="(issueId) => router.push(`/projects/${projectId}/issues/${issueId}`)"
      @open-history="router.push(`/projects/${projectId}/history`)"
      @open-issues="router.push(`/projects/${projectId}/issues`)"
      @analyze="analyze"
      @add-module="addModule"
      @edit-module="editModule"
      @toggle-feature="toggleFeature"
      @open-source="openSource"
      @refresh-graph="refreshGraph"
      @test-affected="testAffected"
    />
    <ModuleEditorDialog v-model="moduleOpen" :module="editingModule" @save="saveModule" />
  </div>
</template>
