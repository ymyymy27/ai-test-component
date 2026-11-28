<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { Candidate, Project } from "@/domain/types";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";
import Icon from "./Icon.vue";

type CreateIntent = "changes" | "flow" | "requirement";

const props = withDefaults(
  defineProps<{
    project: Project;
    presetGoal?: string;
    inDrawer?: boolean;
  }>(),
  {
    presetGoal: "",
    inDrawer: false,
  },
);

const emit = defineEmits<{
  done: [taskId: string, routeTarget: "plan" | "scope"];
  cancel: [];
}>();

const workspace = useWorkspaceStore();
const ui = useUiStore();

const activeIntent = ref<CreateIntent>(props.presetGoal.trim() ? "requirement" : "changes");
const goalText = ref(props.presetGoal.trim());
const requirementText = ref("");
const selectedFeatureNames = ref<string[]>([]);
const selectedFlowIndex = ref(0);
const selectedCandidateId = ref<string | null>(null);
const submitting = ref(false);
const prepStep = ref<0 | 1 | 2 | 3>(0);

const allFeatures = computed(() => {
  const ignored = new Set(props.project.map?.ignoredFeatures ?? []);
  return (props.project.map?.features ?? []).filter((f) => !ignored.has(f.name));
});

const catalogCount = computed(
  () => props.project.catalog.filter((item) => item.observable && item.status === "active").length,
);

const analyzing = computed(
  () =>
    props.project.analysisStatus === "analyzing" ||
    workspace.busyKey(`analysis:${props.project.id}`),
);

const ready = computed(() => {
  if (workspace.mode === "host") {
    return !analyzing.value;
  }
  return props.project.analysisStatus === "ready" && catalogCount.value > 0;
});

const runningTask = computed(() =>
  props.project.tasks.find((task) => task.stage === "running") ?? null,
);

// 改动感知汇总（无需暴露底层命令，转化为可读业务表述）
const diffFiles = computed(() => {
  const statFiles = (props.project.localGit?.diffStat ?? []).map((item) => item.path);
  const dirty = workspace.dirtyFiles ?? [];
  return Array.from(new Set([...statFiles, ...dirty]));
});

const impactedModules = computed(() => {
  const graph = props.project.codeGraph;
  if (!graph) {
    return [];
  }
  const affected = new Set(graph.impact.affectedNodeIds);
  const mods = new Set<string>();
  graph.nodes.forEach((node) => {
    if (affected.has(node.id) && node.module) {
      mods.add(node.module);
    }
  });
  return Array.from(mods);
});

const suggestedChangeGoal = computed(() => {
  if (impactedModules.value.length > 0) {
    return `检查近期改动涉及的 ${impactedModules.value.slice(0, 3).join("、")} 模块`;
  }
  if (diffFiles.value.length > 0) {
    return `检查本次 ${diffFiles.value.length} 个改动文件相关功能`;
  }
  return `检查 ${props.project.name} 核心功能`;
});

// 自动根据功能点推导推荐业务流程
interface RecommendedFlow {
  title: string;
  description: string;
  steps: string[];
  features: string[];
}

const recommendedFlows = computed<RecommendedFlow[]>(() => {
  const feats = allFeatures.value;
  if (feats.length === 0) {
    return [
      {
        title: "核心主流程",
        description: "覆盖启动、核心操作与异常处理",
        steps: ["服务启动", "核心操作与落盘", "异常与边界处理"],
        features: [],
      },
    ];
  }
  const flows: RecommendedFlow[] = [];
  if (feats.length >= 2) {
    const allNames = feats.slice(0, 4).map((f) => f.name);
    flows.push({
      title: `串联流程：${allNames.join(" → ")}`,
      description: `按顺序检查 ${allNames.length} 个模块及跨模块调用`,
      steps: allNames,
      features: allNames,
    });
  }
  feats.slice(0, 4).forEach((feat) => {
    const steps =
      feat.testableItems.length > 0
        ? feat.testableItems.slice(0, 3)
        : [`${feat.name} 入口`, "正常流程", "异常与边界"];
    flows.push({
      title: feat.name,
      description: `检查「${feat.name}」的入口与输出（${feat.testableItems.length || 1} 项）`,
      steps,
      features: [feat.name],
    });
  });
  return flows;
});

const selectedFlow = computed<RecommendedFlow>(
  () => recommendedFlows.value[selectedFlowIndex.value] ?? recommendedFlows.value[0]!,
);

const effectiveGoal = computed(() => {
  if (goalText.value.trim()) {
    return goalText.value.trim();
  }
  if (activeIntent.value === "changes") {
    return suggestedChangeGoal.value;
  }
  if (activeIntent.value === "flow") {
    return selectedFlow.value.title;
  }
  if (selectedFeatureNames.value.length > 0) {
    return `验证 ${selectedFeatureNames.value.join("、")} 功能是否符合预期`;
  }
  if (requirementText.value.trim()) {
    const firstLine = requirementText.value.trim().split(/\r?\n/)[0] ?? "";
    return firstLine.slice(0, 64) || `验证需求变更 · ${props.project.name}`;
  }
  return "";
});

const effectiveFeatureNames = computed(() => {
  if (activeIntent.value === "flow") {
    return selectedFlow.value.features;
  }
  if (activeIntent.value === "requirement") {
    return selectedFeatureNames.value;
  }
  if (impactedModules.value.length > 0) {
    const lowerMods = impactedModules.value.map((m) => m.toLowerCase());
    return allFeatures.value
      .filter((f) =>
        f.modules.some((m) => lowerMods.includes(m.toLowerCase())) ||
        lowerMods.includes(f.name.toLowerCase()),
      )
      .map((f) => f.name);
  }
  return [];
});

const canSubmit = computed(() => {
  if (!ready.value || submitting.value || Boolean(runningTask.value)) {
    return false;
  }
  if (activeIntent.value === "requirement") {
    return Boolean(
      goalText.value.trim() || requirementText.value.trim() || selectedFeatureNames.value.length > 0,
    );
  }
  return Boolean(effectiveGoal.value);
});

watch(
  () => props.presetGoal,
  (val) => {
    const trimmed = val.trim();
    if (trimmed) {
      activeIntent.value = "requirement";
      goalText.value = trimmed;
    }
  },
);

function toggleFeature(name: string): void {
  if (selectedFeatureNames.value.includes(name)) {
    selectedFeatureNames.value = selectedFeatureNames.value.filter((item) => item !== name);
  } else {
    selectedFeatureNames.value = [...selectedFeatureNames.value, name];
  }
}

function applyCandidate(candidate: Candidate): void {
  selectedCandidateId.value = candidate.id;
  activeIntent.value = "requirement";
  goalText.value = candidate.title;
  selectedFeatureNames.value = [...candidate.featureNames];
}

async function analyze(): Promise<void> {
  try {
    await workspace.analyzeProject(props.project.id);
  } catch {
    // store 已提示。
  }
}

/** 一步全自动准备流水线：建任务 → 梳理交付材料 → 生成待确认计划 → 落地计划页等待人工确认 */
async function submitAutoFlow(): Promise<void> {
  if (!canSubmit.value) {
    return;
  }
  const goal = effectiveGoal.value;
  if (!goal) {
    return;
  }
  submitting.value = true;
  prepStep.value = 1;
  try {
    const flowSteps = activeIntent.value === "flow" ? selectedFlow.value.steps : undefined;
    const reqParts: string[] = [];
    if (activeIntent.value === "changes" && diffFiles.value.length > 0) {
      reqParts.push(`近期工作区改动文件：${diffFiles.value.slice(0, 12).join("、")}`);
    }
    if (requirementText.value.trim()) {
      reqParts.push(requirementText.value.trim());
    }
    const task = await workspace.createTask(props.project.id, {
      goal,
      origin: "goal",
      intent: activeIntent.value,
      candidateId: selectedCandidateId.value ?? undefined,
      featureNames: effectiveFeatureNames.value.length ? effectiveFeatureNames.value : undefined,
      flowSteps,
      requirement: reqParts.join("\n").trim() || undefined,
    });

    prepStep.value = 2;
    try {
      await workspace.draftDelivery(props.project.id, task.id);
    } catch {
      // 某些无材料环境自动起草可能被跳过，继续往下生成测试计划
    }

    prepStep.value = 3;
    try {
      await workspace.generatePlan(props.project.id, task.id);
    } catch {
      // 若计划生成报错，跳转至计划页以便用户看到提示与重试按钮
    }

    ui.showToast("测试计划已备妥，请在确认清单核对后开始运行。", "success");
    emit("done", task.id, "plan");
  } catch {
    // createTask 失败由 store 统一提示
  } finally {
    submitting.value = false;
    prepStep.value = 0;
  }
}

/** 次要直通路径：仅创建任务框架，让专业人员自行挑选项目既有范围 */
async function startManualRange(): Promise<void> {
  if (!ready.value || submitting.value || runningTask.value) {
    return;
  }
  submitting.value = true;
  const stamp = new Date().toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
  try {
    const task = await workspace.createTask(props.project.id, {
      goal: `自选范围测试 · ${props.project.name} · ${stamp}`,
      writeProjectGoal: false,
      origin: "range",
    });
    emit("done", task.id, "scope");
  } catch {
    // store 已提示。
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="new-task-composer">
    <div v-if="runningTask" class="notice is-warn" role="alert">
      <Icon name="warn" :size="14" />
      <span>当前已有任务「{{ runningTask.title }}」正在执行，请等待完成或先停止后再发起新测试。</span>
    </div>

    <div v-else-if="analyzing" class="analysis-strip" role="status" aria-live="polite" aria-busy="true">
      <span class="spinner" aria-hidden="true"></span>
      <span class="candidate-brief-main">
        <span class="analysis-strip-title">正在扫描项目结构…</span>
        <span class="analysis-strip-text">完成后可直接生成测试计划。</span>
      </span>
    </div>

    <div v-else-if="project.analysisStatus === 'failed' || (workspace.mode === 'local' && catalogCount === 0)" class="notice is-warn" role="alert">
      <Icon name="warn" :size="14" />
      <span>{{ project.analysisError || "项目可测范围尚未就绪，请先扫描一次功能结构。" }}</span>
      <button class="link-btn" type="button" @click="analyze">重新扫描</button>
    </div>

    <div class="stack-sm">
      <p class="field-label">测试方式</p>
      <div class="tab-pills is-segmented" role="radiogroup" aria-label="测试方式">
        <button
          class="tab-pill"
          :class="{ 'is-active': activeIntent === 'changes' }"
          type="button"
          role="radio"
          :aria-checked="activeIntent === 'changes'"
          :disabled="submitting"
          @click="activeIntent = 'changes'"
        >
          测本次改动
        </button>
        <button
          class="tab-pill"
          :class="{ 'is-active': activeIntent === 'flow' }"
          type="button"
          role="radio"
          :aria-checked="activeIntent === 'flow'"
          :disabled="submitting"
          @click="activeIntent = 'flow'"
        >
          按业务流程测
        </button>
        <button
          class="tab-pill"
          :class="{ 'is-active': activeIntent === 'requirement' }"
          type="button"
          role="radio"
          :aria-checked="activeIntent === 'requirement'"
          :disabled="submitting"
          @click="activeIntent = 'requirement'"
        >
          粘贴需求 / 自选功能
        </button>
      </div>
    </div>

    <!-- 模式一：测本次改动 -->
    <section v-if="activeIntent === 'changes'" class="stack-sm" aria-label="近期代码改动">
      <div class="row-between">
        <strong>当前改动</strong>
        <span class="badge" :class="diffFiles.length ? 'is-accent' : 'is-neutral'">
          {{ diffFiles.length ? `${diffFiles.length} 个改动文件` : "无未提交改动" }}
        </span>
      </div>
      <p class="field-hint">
        <template v-if="diffFiles.length">
          将优先覆盖受影响模块：{{ impactedModules.join("、") || "核心模块" }}。
        </template>
        <template v-else>
          未检测到本地修改，将按项目主路径生成基础检查计划。
        </template>
      </p>
      <details v-if="diffFiles.length" class="details-tech">
        <summary>改动文件 ({{ diffFiles.length }})</summary>
        <ul class="list-plain mono">
          <li v-for="file in diffFiles.slice(0, 15)" :key="file">{{ file }}</li>
        </ul>
      </details>
      <div class="field">
        <label class="field-label" for="composer-goal-changes">测试目标（可选）</label>
        <input
          id="composer-goal-changes"
          v-model="goalText"
          class="input"
          type="text"
          :disabled="submitting"
          :placeholder="suggestedChangeGoal"
        />
      </div>
    </section>

    <!-- 模式二：按业务流程测 -->
    <section v-else-if="activeIntent === 'flow'" class="stack-sm" aria-label="推荐业务流程">
      <p class="field-hint">
        选择要覆盖的业务流程：
      </p>
      <div class="stack-sm">
        <button
          v-for="(flow, idx) in recommendedFlows"
          :key="flow.title"
          type="button"
          class="flow-card-btn"
          :class="{ 'is-active': selectedFlowIndex === idx }"
          :disabled="submitting"
          @click="selectedFlowIndex = idx"
        >
          <div class="row-between">
            <strong>{{ flow.title }}</strong>
            <span v-if="selectedFlowIndex === idx" class="badge is-accent">已选流程</span>
          </div>
          <p class="field-hint">{{ flow.description }}</p>
          <div class="row" style="margin-top: 6px;">
            <span
              v-for="(step, sIdx) in flow.steps"
              :key="`${flow.title}-${sIdx}`"
              class="badge is-neutral"
            >
              {{ sIdx + 1 }}. {{ step }}
            </span>
          </div>
        </button>
      </div>
    </section>

    <!-- 模式三：粘贴需求 / 自选功能 -->
    <section v-else class="stack" aria-label="输入需求与勾选功能">
      <div class="field">
        <label class="field-label" for="composer-goal-req">测试目标</label>
        <input
          id="composer-goal-req"
          v-model="goalText"
          class="input"
          type="text"
          :disabled="submitting"
          placeholder="例如：优惠券结算金额与退券校验"
        />
      </div>
      <div class="field">
        <label class="field-label" for="composer-requirement-text">
          需求或验收说明（可选）
        </label>
        <textarea
          id="composer-requirement-text"
          v-model="requirementText"
          class="textarea"
          rows="4"
          :disabled="submitting"
          placeholder="粘贴需求、接口变更或验收条件…"
        />
      </div>
      <div v-if="allFeatures.length" class="stack-sm">
        <span class="field-label">涉及模块（可选，留空按全部模块分析）</span>
        <div class="row">
          <button
            v-for="feat in allFeatures"
            :key="feat.name"
            type="button"
            class="filter-btn"
            :class="{ 'is-active': selectedFeatureNames.includes(feat.name) }"
            :disabled="submitting"
            @click="toggleFeature(feat.name)"
          >
            {{ feat.name }}
          </button>
        </div>
      </div>

      <details v-if="project.candidates.length" class="details-tech">
        <summary>从推荐目标中选择 ({{ project.candidates.length }})</summary>
        <div class="stack-sm" style="margin-top: var(--space-2);">
          <button
            v-for="cand in project.candidates"
            :key="cand.id"
            type="button"
            class="flow-card-btn"
            :class="{ 'is-active': selectedCandidateId === cand.id }"
            @click="applyCandidate(cand)"
          >
            <div class="row-between">
              <strong>{{ cand.title }}</strong>
              <span class="badge is-neutral">{{ cand.itemCount }} 条可测项</span>
            </div>
          </button>
        </div>
      </details>
    </section>

    <!-- 准备进度提示 -->
    <div v-if="submitting" class="prep-steps" role="status" aria-live="polite">
      <div class="prep-step" :class="prepStep > 1 ? 'is-done' : prepStep === 1 ? 'is-active' : ''">
        <span>{{ prepStep > 1 ? "✓" : "1." }}</span>
        <span>创建任务并关联范围（{{ effectiveGoal }}）</span>
      </div>
      <div class="prep-step" :class="prepStep > 2 ? 'is-done' : prepStep === 2 ? 'is-active' : ''">
        <span>{{ prepStep > 2 ? "✓" : "2." }}</span>
        <span>整理任务说明与验收依据…</span>
      </div>
      <div class="prep-step" :class="prepStep === 3 ? 'is-active' : ''">
        <span>3.</span>
        <span>生成待确认测试计划…</span>
      </div>
    </div>

    <div class="composer-footer row-between">
      <button
        class="link-btn"
        type="button"
        :disabled="!ready || submitting || Boolean(runningTask)"
        @click="startManualRange"
      >
        手动选择范围 →
      </button>

      <div class="row">
        <button
          v-if="inDrawer"
          class="btn btn-sm"
          type="button"
          :disabled="submitting"
          @click="emit('cancel')"
        >
          取消
        </button>
        <button
          class="btn btn-primary"
          type="button"
          :disabled="!canSubmit"
          :aria-busy="submitting"
          @click="submitAutoFlow"
        >
          {{ submitting ? "正在生成计划…" : "生成测试计划" }}
        </button>
      </div>
    </div>
  </div>
</template>
