<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import CaseLedger from "@/components/CaseLedger.vue";
import ErrorState from "@/components/ErrorState.vue";
import Icon from "@/components/Icon.vue";
import PageHeader from "@/components/PageHeader.vue";
import PreflightAndProbesCard from "@/components/PreflightAndProbesCard.vue";
import RunProgress from "@/components/RunProgress.vue";
import StatusMark from "@/components/StatusMark.vue";
import StatusText from "@/components/StatusText.vue";
import { compareItemNo, currentPlan, currentRun } from "@/domain/derive";
import type { Run, Task } from "@/domain/types";
import type { RunLogTailEntry } from "@/host/dto";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();
const projectId = computed(() => String(route.params.projectId ?? ""));
const taskId = computed(() => String(route.params.taskId ?? ""));
const task = computed<Task | null>(
  () => workspace.currentProject?.tasks.find((entry) => entry.id === taskId.value) ?? null,
);
const run = computed<Run | null>(() => (task.value ? currentRun(task.value) : null));
const plan = computed(() => (task.value ? currentPlan(task.value) : null));
const elapsedMs = ref(0);
const stalled = computed(() => {
  void elapsedMs.value;
  const heartbeat = run.value?.heartbeatAt;
  if (!run.value || run.value.status !== "running" || !heartbeat) {
    return false;
  }
  const since = new Date(heartbeat).getTime();
  return Number.isFinite(since) && Date.now() - since > 90_000;
});
const sortedItems = computed(() =>
  [...(run.value?.items ?? [])].sort((first, second) => compareItemNo(first.no, second.no)),
);
const stopping = ref(false);
const tailOpen = ref(false);
const tailLoading = ref(false);
const tailEntries = ref<RunLogTailEntry[]>([]);
let timer: ReturnType<typeof setInterval> | null = null;
let tailTimer: ReturnType<typeof setInterval> | null = null;

function stopTailPolling(): void {
  if (tailTimer) {
    clearInterval(tailTimer);
    tailTimer = null;
  }
}

async function refreshTail(): Promise<void> {
  if (workspace.mode !== "host" || !run.value || !tailOpen.value) {
    return;
  }
  tailLoading.value = tailEntries.value.length === 0;
  try {
    tailEntries.value = await workspace.fetchRunLogTail(projectId.value, run.value.id);
  } catch {
    // 日志尚未落盘或瞬时失败时保持静默，等待下轮轮询。
  } finally {
    tailLoading.value = false;
  }
}

function onTailToggle(event: Event): void {
  const open = (event.target as HTMLDetailsElement | null)?.open ?? false;
  tailOpen.value = open;
  stopTailPolling();
  if (open && workspace.mode === "host" && run.value?.status === "running") {
    void refreshTail();
    tailTimer = setInterval(() => {
      void refreshTail();
    }, 2500);
  }
}

function tick(): void {
  if (!run.value) {
    return;
  }
  elapsedMs.value = Math.max(0, Date.now() - new Date(run.value.startedAt).getTime());
}

async function addConventionFromProbe(input: {
  pattern: string;
  reason: string;
  scope: string;
}): Promise<void> {
  if (!projectId.value) return;
  try {
    await workspace.addConvention(projectId.value, input);
  } catch {
    // store 已提示。
  }
}

watch(
  () => run.value?.status,
  async (status) => {
    if (status && status !== "running") {
      stopTailPolling();
      await router.replace(`/projects/${projectId.value}/tasks/${taskId.value}/result`);
    }
  },
);

async function stop(): Promise<void> {
  const current = run.value;
  if (!current || stopping.value) {
    return;
  }
  const answer = await ui.askConfirm({
    title: "停止这次测试？",
    body: "正在执行的检查会按阻塞记录，尚未开始的检查标为未运行。已完成的检查与证据会保留，停止后仍会生成报告。",
    confirmLabel: "停止测试",
    cancelLabel: "继续运行",
    tone: "danger",
  });
  if (answer === null) {
    return;
  }
  stopping.value = true;
  try {
    await workspace.stopRun(projectId.value, current.id);
    await router.replace(`/projects/${projectId.value}/tasks/${taskId.value}/result`);
  } catch {
    // store 已提示。
  } finally {
    stopping.value = false;
  }
}

onMounted(async () => {
  tick();
  timer = setInterval(tick, 1000);
  if (task.value && workspace.currentTask?.id !== taskId.value) {
    try {
      await workspace.setActiveTask(projectId.value, taskId.value);
    } catch {
      // store 已提示。
    }
  }
  if (!run.value) {
    await router.replace(`/projects/${projectId.value}/tasks/${taskId.value}/plan`);
  } else if (run.value.status !== "running") {
    await router.replace(`/projects/${projectId.value}/tasks/${taskId.value}/result`);
  }
});

onBeforeUnmount(() => {
  if (timer) {
    clearInterval(timer);
  }
  stopTailPolling();
});
</script>

<template>
  <div v-if="!task" class="page">
    <ErrorState
      title="任务不存在"
      text="回到项目概览重新选择任务。"
      retry-label="回到概览"
      @retry="router.push(`/projects/${projectId}/overview`)"
    />
  </div>
  <div v-else-if="!run" class="page">
    <ErrorState
      title="没有正在运行的记录"
      text="回到计划页确认后再开始。"
      retry-label="回到计划"
      @retry="router.push(`/projects/${projectId}/tasks/${taskId}/plan`)"
    />
  </div>
  <div v-else class="page">
    <PageHeader
      :title="task.title"
      :subtitle="plan ? `计划 ${plan.no} · 第 ${plan.revision} 版 · 正在执行测试` : '正在执行测试'"
    />

    <RunProgress :run="run" :elapsed-ms="elapsedMs" />

    <PreflightAndProbesCard
      :preflight="run.preflight ?? null"
      :probe-results="run.probeResults ?? []"
      :can-open-source="workspace.mode === 'host'"
      @add-convention="addConventionFromProbe"
      @open-source="(file, line) => void workspace.openSourceLocation(file, line)"
    />

    <div v-if="stalled" class="notice is-warn" role="alert">
      <Icon name="warn" :size="14" />
      <span>运行心跳超过 90 秒未更新，可能已中断；可以停止并查看已保存的结果。</span>
    </div>

    <section class="section">
      <div class="section-head">
        <h2 class="section-title">项目基础设施检查</h2>
        <span class="section-note">结果单独记录，不计入验收覆盖</span>
      </div>
      <div class="check-list">
        <div v-for="check in run.l1" :key="check.key" class="check-row">
          <StatusMark :result="check.result" />
          <span class="check-name">{{ check.name }}</span>
          <span class="check-method">{{ check.method }}</span>
          <StatusText :result="check.result" />
          <span v-if="check.reason" class="muted check-reason">{{ check.reason }}</span>
        </div>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2 class="section-title">验收项执行进度 ({{ run.scope.acceptanceItemIds.length }} 项)</h2>
        <span class="section-note">按业务验收项实时更新结果</span>
      </div>
      <CaseLedger :run="run" :items="sortedItems" running />
    </section>

    <details
      v-if="workspace.mode === 'host'"
      class="details-tech run-tail-panel"
      :open="tailOpen"
      @toggle="onTailToggle"
    >
      <summary>实时底层日志输出（每 2.5 秒自动刷新）</summary>
      <div v-if="tailLoading" class="field-hint">正在读取实时日志…</div>
      <div v-else-if="tailEntries.length === 0" class="field-hint">
        暂无最新日志输出，执行产生输出后将自动展示。
      </div>
      <div v-else class="run-tail-list">
        <div v-for="entry in tailEntries" :key="entry.name" class="run-tail-entry">
          <div class="run-tail-stream">{{ entry.name }}</div>
          <pre class="run-tail-pre">{{ entry.text }}</pre>
        </div>
      </div>
    </details>

    <div class="actionbar">
      <div class="actionbar-inner">
        <div class="actionbar-facts">
          <span class="fact-strong">计划 {{ plan?.no ?? "—" }} · 第 {{ plan?.revision ?? "—" }} 版</span>
          <span>本次纳入 {{ run.scope.coverage.selected }} / 共 {{ run.scope.coverage.applicable }} 项</span>
        </div>
        <div class="actionbar-actions">
          <button
            class="btn btn-danger"
            type="button"
            :disabled="stopping"
            :aria-busy="stopping"
            @click="stop"
          >
            <Icon name="stop" :size="14" />
            {{ stopping ? "正在停止…" : "停止测试" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
