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
const mode = ref<"target" | "range" | "quick" | null>(null);

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
const selectedCandidate = computed(
  () => candidates.value.find((candidate) => candidate.id === selectedId.value) ?? null,
);
const canCreate = computed(
  () => Boolean(goal.value.trim()) && ready.value && !startBusy.value,
);

watch(candidates, (value) => {
  if (selectedId.value && !value.some((candidate) => candidate.id === selectedId.value)) {
    selectedId.value = null;
  }
});

function chooseMode(next: "target" | "range" | "quick"): void {
  mode.value = next;
  void router.replace({ query: { ...route.query, mode: next } });
}

onMounted(async () => {
  if (!project.value) {
    return;
  }
  const requested = String(route.query.mode ?? "");
  if (requested === "target" || requested === "range" || requested === "quick") {
    chooseMode(requested);
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

function updateGoal(value: string): void {
  goal.value = value;
  if (selectedCandidate.value && value.trim() !== selectedCandidate.value.title) {
    selectedId.value = null;
  }
}

function selectCandidate(candidateId: string): void {
  const candidate = candidates.value.find((entry) => entry.id === candidateId);
  if (!candidate) {
    return;
  }
  selectedId.value = candidate.id;
  goal.value = candidate.title;
}

async function submit(): Promise<void> {
  const text = goal.value.trim();
  if (!text || !canCreate.value) {
    return;
  }
  try {
    const task = await workspace.createTask(projectId.value, {
      goal: text,
      candidateId: selectedId.value ?? undefined,
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

function startQuick(): void {
  void router.push(`/projects/${projectId.value}/quick-check`);
}

async function startRange(): Promise<void> {
  if (!project.value || startBusy.value) {
    return;
  }
  const when = new Date().toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
  const text = `范围测试 · ${project.value.name} · ${when}`;
  try {
    const task = await workspace.createTask(projectId.value, {
      goal: text,
      writeProjectGoal: false,
    });
    await router.push(`/projects/${projectId.value}/tasks/${task.id}/scope`);
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
      kicker="测试"
      title="开始测试"
      :subtitle="`${project.name} · 选择一种测试方式`"
    />

    <div v-if="analyzing" class="analysis-strip" role="status" aria-live="polite" aria-busy="true">
      <span class="spinner" aria-hidden="true"></span>
      <span class="candidate-brief-main">
        <span class="analysis-strip-title">AI 分析中</span>
        <span class="analysis-strip-text">正在整理模块、功能与目标候选。</span>
      </span>
    </div>
    <div v-else-if="project.analysisStatus === 'failed'" class="notice is-warn" role="alert">
      <Icon name="warn" :size="14" />
      <span>项目分析没有完成：{{ project.analysisError || "未知原因" }}</span>
      <button class="link-btn" type="button" :disabled="analysisBusy" @click="analyze">
        重新分析
      </button>
    </div>

    <div class="test-mode-grid" role="radiogroup" aria-label="测试方式">
      <label class="test-mode" :class="{ 'is-active': mode === 'target' }">
        <input
          class="visually-hidden"
          type="radio"
          name="test-mode"
          value="target"
          :checked="mode === 'target'"
          @change="chooseMode('target')"
        />
        <span class="test-mode-icon"><Icon name="play" :size="16" /></span>
        <span class="test-mode-title">按目标测试</span>
        <span class="test-mode-desc">写一句目标或选一个候选，自动生成任务和计划。</span>
      </label>
      <label class="test-mode" :class="{ 'is-active': mode === 'range' }">
        <input
          class="visually-hidden"
          type="radio"
          name="test-mode"
          value="range"
          :checked="mode === 'range'"
          @change="chooseMode('range')"
        />
        <span class="test-mode-icon"><Icon name="folder" :size="16" /></span>
        <span class="test-mode-title">按范围测试</span>
        <span class="test-mode-desc">先从项目理解中勾选业务功能和验收项，再生成计划。</span>
      </label>
      <label class="test-mode" :class="{ 'is-active': mode === 'quick' }">
        <input
          class="visually-hidden"
          type="radio"
          name="test-mode"
          value="quick"
          :checked="mode === 'quick'"
          @change="chooseMode('quick')"
        />
        <span class="test-mode-icon"><Icon name="shield" :size="16" /></span>
        <span class="test-mode-title">快速检查</span>
        <span class="test-mode-desc">只检查构建、启动入口和最小测试，不建任务。</span>
      </label>
    </div>

    <section v-if="!mode" class="start-hint">
      <p>
        按目标测试适合验证一个具体改动；按范围测试适合回归已有功能；快速检查只确认项目能跑。
      </p>
      <div class="start-facts">
        <span><strong class="num">{{ featureCount }}</strong> 个业务功能</span>
        <span><strong class="num">{{ catalogCount }}</strong> 个可测项</span>
        <button
          class="link-btn"
          type="button"
          @click="router.push(`/projects/${projectId}/overview`)"
        >
          查看项目理解
        </button>
      </div>
    </section>

    <template v-if="mode === 'target'">
      <GoalComposer
        :model-value="goal"
        :busy="startBusy"
        :catalog-count="catalogCount"
        :feature-count="featureCount"
        @update:model-value="updateGoal"
        @start="submit"
      />

      <CandidateList
        :candidates="candidates"
        :selected-id="selectedId"
        :busy="startBusy"
        @select="selectCandidate"
      />
    </template>

    <div v-if="mode" class="actionbar new-task-actionbar">
      <div class="actionbar-inner">
        <div class="actionbar-facts">
          <template v-if="mode === 'target'">
            <span v-if="selectedCandidate" class="fact-strong">
              已选候选 · {{ selectedCandidate.title }}
            </span>
            <span v-else-if="goal.trim()" class="fact-strong">目标 · {{ goal.trim() }}</span>
            <span v-else>写目标或选一个候选</span>
            <span v-if="!ready" class="field-error">分析完成后可创建带验收项的任务。</span>
            <span v-else-if="firstTask">确认后写入项目目标</span>
            <span v-else>只改本次任务目标</span>
          </template>
          <span v-else-if="mode === 'range'" class="fact-strong">进入范围页选择验收项</span>
          <span v-else class="fact-strong">项目级检查 · 不进入验收覆盖</span>
        </div>
        <div class="actionbar-actions">
          <button
            v-if="mode === 'target'"
            class="btn btn-primary"
            type="button"
            :disabled="!canCreate"
            :aria-busy="startBusy"
            @click="submit"
          >
            {{ startBusy ? "正在创建并生成计划…" : "创建任务并生成计划" }}
          </button>
          <button
            v-else-if="mode === 'range'"
            class="btn btn-primary"
            type="button"
            :disabled="!ready || startBusy"
            @click="startRange"
          >
            选择范围
          </button>
          <button v-else class="btn btn-primary" type="button" @click="startQuick">
            开始快速检查
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
