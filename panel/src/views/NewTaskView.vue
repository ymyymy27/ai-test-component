<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import CandidateList from "@/components/CandidateList.vue";
import ErrorState from "@/components/ErrorState.vue";
import GoalComposer from "@/components/GoalComposer.vue";
import Icon from "@/components/Icon.vue";
import PageHeader from "@/components/PageHeader.vue";
import { useWorkspaceStore } from "@/stores/workspace";

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const projectId = computed(() => String(route.params.projectId ?? ""));
const project = computed(() => workspace.currentProject);
const goal = ref("");
const selectedId = ref<string | null>(null);

const catalogCount = computed(() =>
  project.value
    ? project.value.catalog.filter((item) => item.observable && item.status === "active").length
    : 0,
);
const featureCount = computed(() => project.value?.map?.features.length ?? 0);
const analyzing = computed(
  () =>
    project.value?.analysisStatus === "analyzing" ||
    workspace.busyKey(`analysis:${projectId.value}`),
);
const analysisBusy = computed(() => workspace.busyKey(`analysis:${projectId.value}`));
const ready = computed(() => project.value?.analysisStatus === "ready" && catalogCount.value > 0);
const startBusy = computed(() => workspace.busyKey(`create-task:${projectId.value}`));
const firstTask = computed(
  () => (project.value?.tasks.filter((task) => task.stage !== "fresh").length ?? 0) === 0,
);
const candidates = computed(() => project.value?.candidates ?? []);

watch(
  candidates,
  (value) => {
    if (!selectedId.value || !value.some((candidate) => candidate.id === selectedId.value)) {
      selectedId.value = value[0]?.id ?? null;
    }
  },
  { immediate: true },
);

onMounted(async () => {
  if (!project.value) {
    return;
  }
  const staleCandidates =
    project.value.analysisStatus === "ready" && candidates.value.length === 0;
  if (
    !analysisBusy.value &&
    (project.value.analysisStatus === "idle" ||
      project.value.analysisStatus === "failed" ||
      staleCandidates)
  ) {
    try {
      await workspace.analyzeProject(projectId.value);
    } catch {
      // store 已提示。
    }
  }
});

async function analyze(): Promise<void> {
  try {
    await workspace.analyzeProject(projectId.value);
  } catch {
    // store 已提示。
  }
}

async function createFromGoal(text: string): Promise<void> {
  try {
    const task = await workspace.createTask(projectId.value, { goal: text });
    await router.push(`/projects/${projectId.value}/tasks/${task.id}/scope`);
  } catch {
    // store 已提示。
  }
}

async function createFromCandidate(): Promise<void> {
  const candidate = candidates.value.find((entry) => entry.id === selectedId.value);
  if (!candidate) {
    return;
  }
  try {
    const task = await workspace.createTask(projectId.value, {
      goal: candidate.title,
      candidateId: candidate.id,
    });
    await router.push(`/projects/${projectId.value}/tasks/${task.id}/scope`);
  } catch {
    // store 已提示。
  }
}

async function createFromCandidateAndPlan(): Promise<void> {
  const candidate = candidates.value.find((entry) => entry.id === selectedId.value);
  if (!candidate) {
    return;
  }
  try {
    const task = await workspace.createTask(projectId.value, {
      goal: candidate.title,
      candidateId: candidate.id,
    });
    try {
      await workspace.generatePlan(projectId.value, task.id);
      await router.push(`/projects/${projectId.value}/tasks/${task.id}/plan`);
    } catch {
      await router.push(`/projects/${projectId.value}/tasks/${task.id}/scope`);
    }
  } catch {
    // store 已提示。
  }
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
      kicker="新建任务"
      title="新建任务"
      :subtitle="
        firstTask
          ? `${project.name} · 先确认项目目标，再创建首个任务`
          : `${project.name} · 先确认这次要验证什么`
      "
    />

    <div v-if="analyzing" class="analysis-strip" role="status" aria-live="polite" aria-busy="true">
      <span class="spinner" aria-hidden="true"></span>
      <span class="candidate-brief-main">
        <span class="analysis-strip-title">AI 分析中</span>
        <span class="analysis-strip-text">
          正在整理模块、业务功能与目标候选。分析完成后才能创建带验收项的任务；快速检查不受影响。
        </span>
      </span>
    </div>
    <div v-else-if="project.analysisStatus === 'failed'" class="notice is-warn" role="alert">
      <Icon name="warn" :size="14" />
      <span>项目分析没有完成：{{ project.analysisError || "未知原因" }}</span>
      <button class="link-btn" type="button" :disabled="analysisBusy" @click="analyze">
        重新分析
      </button>
    </div>

    <GoalComposer
      v-model="goal"
      :busy="startBusy"
      :analysis-busy="analysisBusy"
      :ready="ready"
      :catalog-count="catalogCount"
      :feature-count="featureCount"
      @start="createFromGoal"
      @analyze="analyze"
      @quick-check="router.push(`/projects/${projectId}/quick-check`)"
    />

    <CandidateList
      :candidates="candidates"
      :selected-id="selectedId"
      :busy="startBusy"
      @select="selectedId = $event"
      @create="createFromCandidate"
      @create-and-plan="createFromCandidateAndPlan"
    />

    <div class="target-context">
      <span>未关闭问题 {{ workspace.openIssues.length }} 个</span>
      <span>识别 {{ project.modules.length }} 个模块 · {{ featureCount }} 个业务功能</span>
      <span v-if="firstTask">确认后写入项目目标</span>
      <span v-else>只改本次任务目标</span>
    </div>
  </div>
</template>
