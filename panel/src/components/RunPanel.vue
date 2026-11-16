<script setup lang="ts">
import { computed, ref } from "vue";

import { describeError, type FriendlyError } from "@/api/errors";
import {
  CASE_RESULT_LABEL,
  RUN_STATUS_BADGE,
  RUN_STATUS_LABEL,
  formatDateTime,
} from "@/api/labels";
import type { CaseResult } from "@/api/types";
import AppButton from "@/components/AppButton.vue";
import Badge from "@/components/Badge.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Icon from "@/components/Icon.vue";
import {
  cancelActiveRun,
  isBusy,
  openEvidenceDrawer,
  session,
} from "@/state/session";

const confirmCancel = ref(false);
const cancelError = ref<FriendlyError | null>(null);

const run = computed(() => session.result?.run ?? null);
const total = computed(() => run.value?.cases.length ?? 0);
const processed = computed(
  () => run.value?.cases.filter((item) => item.result !== "not_run").length ?? 0,
);
const progress = computed(() =>
  total.value > 0 ? Math.round((processed.value / total.value) * 100) : 0,
);

const planCases = computed(() => {
  const map = new Map<string, string>();
  for (const planCase of session.plan?.cases ?? []) {
    if (planCase.acceptance_item_id) {
      map.set(planCase.case_id, planCase.acceptance_item_id);
    }
  }
  return map;
});

const resultsByItem = computed(() => {
  const map = new Map<string, CaseResult[]>();
  for (const runCase of run.value?.cases ?? []) {
    const itemId = planCases.value.get(runCase.case_id);
    if (!itemId) {
      continue;
    }
    const list = map.get(itemId) ?? [];
    list.push(runCase.result);
    map.set(itemId, list);
  }
  return map;
});

const currentCase = computed(() => {
  const next = run.value?.cases.find((item) => item.result === "not_run");
  if (!next) {
    return null;
  }
  const planCase = session.plan?.cases.find((item) => item.case_id === next.case_id);
  return {
    title: planCase?.title || planCase?.expected || "正在执行",
    layer: planCase?.layer ?? "",
  };
});

const evidenceCount = computed(() => {
  const refs = new Set<string>(run.value?.evidence_refs ?? []);
  for (const item of run.value?.cases ?? []) {
    for (const ref of item.evidence_refs ?? []) {
      refs.add(ref);
    }
    for (const attempt of item.attempts ?? []) {
      for (const ref of attempt.evidence_refs ?? []) {
        refs.add(ref);
      }
    }
  }
  return refs.size;
});

const statusNote = computed(() => {
  switch (run.value?.status) {
    case "running":
      return "运行在后台继续，关闭面板不会中断。";
    case "interrupted":
      return "执行中断：心跳停止且进程已不在，已完成的用例与证据保留。";
    case "cancelled":
      return "已取消：在用例边界停止，未完成部分不计入通过。";
    case "execution_error":
      return "执行异常：查看失败用例与证据，必要时重新运行。";
    case "completed":
      return "运行正常结束。";
    default:
      return "";
  }
});

interface ItemState {
  label: string;
  tone: "passed" | "failed" | "incomplete" | "neutral" | "running";
}

function itemState(itemId: string): ItemState {
  const results = resultsByItem.value.get(itemId) ?? [];
  if (results.length === 0) {
    return { label: "未生成用例", tone: "neutral" };
  }
  if (results.includes("failed")) {
    return { label: CASE_RESULT_LABEL.failed, tone: "failed" };
  }
  if (results.includes("error")) {
    return { label: CASE_RESULT_LABEL.error, tone: "incomplete" };
  }
  if (results.includes("blocked")) {
    return { label: CASE_RESULT_LABEL.blocked, tone: "incomplete" };
  }
  if (results.includes("manual_pending")) {
    return { label: CASE_RESULT_LABEL.manual_pending, tone: "incomplete" };
  }
  if (results.includes("not_run")) {
    return { label: "执行中", tone: "running" };
  }
  return { label: CASE_RESULT_LABEL.passed, tone: "passed" };
}

const liveItems = computed(() => {
  const scope = new Set(run.value?.scope.acceptance_item_ids ?? []);
  return (session.task?.acceptance_items ?? [])
    .filter((item) => item.status === "active")
    .filter((item) => scope.size === 0 || scope.has(item.item_id))
    .map((item) => ({ item, state: itemState(item.item_id) }));
});

async function cancelRun(): Promise<void> {
  cancelError.value = null;
  try {
    await cancelActiveRun();
    confirmCancel.value = false;
  } catch (error) {
    cancelError.value = describeError(error);
  }
}
</script>

<template>
  <section class="run-panel" data-testid="run-panel">
    <div class="status-top">
      <div class="status-main">
        <Badge :tone="run ? RUN_STATUS_BADGE[run.status] : 'running'">
          {{ run ? RUN_STATUS_LABEL[run.status] : "运行中" }}
        </Badge>
        <span v-if="run" class="started">开始于 {{ formatDateTime(run.started_at) }}</span>
      </div>
      <span v-if="run?.status === 'running'" class="live">
        <span class="live-dot" aria-hidden="true"></span>
        每 2 秒更新
      </span>
    </div>

    <div class="progress-head">
      <span class="progress-label">用例进度</span>
      <span class="progress-value numeric">{{ processed }} / {{ total }}</span>
    </div>
    <div
      class="progress"
      role="progressbar"
      :aria-valuenow="processed"
      :aria-valuemin="0"
      :aria-valuemax="total"
      aria-label="用例进度"
    >
      <span class="progress-fill" :style="{ width: `${progress}%` }"></span>
    </div>
    <p class="hint">{{ statusNote }}</p>

    <div v-if="currentCase" class="current">
      <span class="current-label">当前用例</span>
      <span class="current-title">{{ currentCase.title }}</span>
      <Badge v-if="currentCase.layer" tone="neutral">{{ currentCase.layer }}</Badge>
    </div>
    <div v-else-if="run?.status === 'running'" class="current">
      <span class="current-label">当前用例</span>
      <span class="current-title">正在收尾，等待下一次状态更新</span>
    </div>

    <ul v-if="liveItems.length" class="live-list" aria-label="验收项实时状态">
      <li v-for="row in liveItems" :key="row.item.item_id" class="live-row">
        <span class="live-text">{{ row.item.text }}</span>
        <Badge :tone="row.state.tone">{{ row.state.label }}</Badge>
      </li>
    </ul>

    <ErrorNotice
      v-if="cancelError"
      :error="cancelError"
      :show-retry="false"
      @retry="() => {}"
    />

    <footer class="run-foot">
      <AppButton variant="secondary" data-testid="live-evidence" @click="openEvidenceDrawer">
        <Icon name="file" :size="14" />
        实时证据（{{ evidenceCount }}）
      </AppButton>

      <div v-if="run?.status === 'running'" class="cancel-zone">
        <template v-if="!confirmCancel">
          <AppButton variant="danger" size="sm" @click="confirmCancel = true">
            取消这次运行
          </AppButton>
          <span class="hint">已完成的用例会保留。</span>
        </template>
        <div v-else class="confirm">
          <p class="confirm-text">确认取消？运行会在当前用例结束后停止。</p>
          <div class="row">
            <AppButton
              variant="danger"
              size="sm"
              :loading="isBusy('cancel')"
              @click="cancelRun"
            >
              确认取消
            </AppButton>
            <AppButton variant="ghost" size="sm" @click="confirmCancel = false">
              继续运行
            </AppButton>
          </div>
        </div>
      </div>
    </footer>
  </section>
</template>

<style scoped>
.run-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.status-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.status-main {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.started {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.live {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--fs-12);
  color: var(--text-3);
}

.live-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
  animation: breathe 1.6s ease-in-out infinite;
}

@keyframes breathe {
  50% {
    opacity: 0.35;
  }
}

.progress-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.progress-label {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.progress-value {
  font-size: var(--fs-13);
  color: var(--text-2);
}

.progress {
  height: 4px;
  border-radius: 2px;
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  overflow: hidden;
}

.progress-fill {
  display: block;
  height: 100%;
  background: var(--accent-line);
  transition: width 240ms ease;
}

.current {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  padding: var(--space-3);
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
}

.current-label {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.current-title {
  font-size: var(--fs-13);
  flex: 1 1 auto;
  min-width: 0;
  word-break: break-word;
}

.live-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: 1px solid var(--line);
}

.live-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 7px 0;
  border-bottom: 1px solid var(--line);
}

.live-row:last-child {
  border-bottom: 0;
}

.live-text {
  font-size: var(--fs-12);
  color: var(--text-2);
  line-height: 1.5;
  min-width: 0;
}

.run-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding-top: var(--space-3);
  border-top: 1px solid var(--line);
}

.cancel-zone {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.confirm {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--failed-fill);
  border: 1px solid rgba(214, 141, 132, 0.25);
  border-radius: var(--r-control);
}

.confirm-text {
  font-size: var(--fs-12);
  color: var(--text);
}
</style>
