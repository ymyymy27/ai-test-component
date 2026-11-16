<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";

import { api } from "@/api/commands";
import { describeError, type FriendlyError } from "@/api/errors";
import {
  CASE_RESULT_LABEL,
  RUN_STATUS_BADGE,
  RUN_STATUS_LABEL,
  formatDateTime,
} from "@/api/labels";
import type { Plan, PlanCase, RunDetail, Task } from "@/api/types";
import AppButton from "@/components/AppButton.vue";
import Badge from "@/components/Badge.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Icon from "@/components/Icon.vue";
import SkeletonBlock from "@/components/SkeletonBlock.vue";
import { go, openResult, session } from "@/state/session";

const detail = ref<RunDetail | null>(null);
const plan = ref<Plan | null>(session.plan);
const task = ref<Task | null>(null);
const loading = ref(true);
const error = ref<FriendlyError | null>(null);
const confirmCancel = ref(false);
const cancelling = ref(false);
const cancelError = ref<FriendlyError | null>(null);
let timer: number | undefined;

const running = computed(() => detail.value?.run.status === "running");
const total = computed(() => detail.value?.run.cases.length ?? 0);
const processed = computed(
  () => detail.value?.run.cases.filter((item) => item.result !== "not_run").length ?? 0,
);
const progress = computed(() =>
  total.value > 0 ? Math.round((processed.value / total.value) * 100) : 0,
);

const planCases = computed(() => {
  const map = new Map<string, PlanCase>();
  for (const planCase of plan.value?.cases ?? []) {
    map.set(planCase.case_id, planCase);
  }
  return map;
});

const itemTexts = computed(() => {
  const map = new Map<string, string>();
  for (const item of task.value?.acceptance_items ?? []) {
    map.set(item.item_id, item.text);
  }
  return map;
});

const currentCase = computed(() => {
  const next = detail.value?.run.cases.find((item) => item.result === "not_run");
  if (!next) {
    return null;
  }
  return {
    runCase: next,
    planCase: planCases.value.get(next.case_id),
  };
});

const finished = computed(() => {
  const status = detail.value?.run.status;
  return Boolean(status && status !== "running");
});

const statusNote = computed(() => {
  switch (detail.value?.run.status) {
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

async function load(): Promise<void> {
  if (!session.project || !session.activeRunId) {
    error.value = {
      title: "没有正在查看的运行",
      detail: "",
      next: "回到项目页或历史记录重新打开一次运行",
    };
    loading.value = false;
    return;
  }
  try {
    const data = await api.runStatus(session.project.project_id, session.activeRunId);
    detail.value = data;
    session.result = data;
    session.activePlanId = data.run.plan_id;
    error.value = null;
    if (!plan.value || plan.value.plan_id !== data.run.plan_id) {
      try {
        const result = await api.showPlan(session.project.project_id, data.run.plan_id);
        plan.value = result.plan;
        session.plan = result.plan;
        if (!task.value) {
          const taskData = await api.showTask(session.project.project_id, result.plan.task_id);
          task.value = taskData.task;
          session.activeTaskId = result.plan.task_id;
          session.task = taskData.task;
        }
      } catch {
        plan.value = null;
      }
    }
  } catch (caught) {
    error.value = describeError(caught);
  } finally {
    loading.value = false;
    schedule();
  }
}

function schedule(): void {
  if (timer !== undefined) {
    window.clearTimeout(timer);
    timer = undefined;
  }
  if (running.value) {
    timer = window.setTimeout(() => {
      void load();
    }, 2000);
  }
}

async function cancelRun(): Promise<void> {
  if (!session.project || !session.activeRunId) {
    return;
  }
  cancelling.value = true;
  cancelError.value = null;
  try {
    await api.cancelRun(session.project.project_id, session.activeRunId);
    confirmCancel.value = false;
    await load();
  } catch (caught) {
    cancelError.value = describeError(caught);
  } finally {
    cancelling.value = false;
  }
}

function viewResult(): void {
  if (session.activeRunId) {
    void openResult(session.activeRunId);
  }
}

onMounted(() => {
  void load();
});

onUnmounted(() => {
  if (timer !== undefined) {
    window.clearTimeout(timer);
  }
});
</script>

<template>
  <div class="stack" data-testid="run-view">
    <div class="head">
      <button type="button" class="back" @click="go('project')">
        <Icon name="arrow-left" :size="15" />
        返回项目
      </button>
      <AppButton size="sm" variant="ghost" :loading="loading" @click="load">
        <Icon name="refresh" :size="14" />
        刷新
      </AppButton>
    </div>

    <ErrorNotice v-if="error" :error="error" retry-label="重新读取" :busy="loading" @retry="load" />

    <SkeletonBlock v-else-if="loading && !detail" :lines="4" label="正在读取运行状态" />

    <template v-else-if="detail">
      <section class="status card">
        <div class="status-top">
          <div class="status-main">
            <Badge :tone="RUN_STATUS_BADGE[detail.run.status]">
              {{ RUN_STATUS_LABEL[detail.run.status] }}
            </Badge>
            <span class="started">开始于 {{ formatDateTime(detail.run.started_at) }}</span>
          </div>
          <span v-if="running" class="live">
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
        <p class="status-note">{{ statusNote }}</p>

        <div v-if="currentCase" class="current">
          <span class="current-label">当前用例</span>
          <span class="current-title">
            {{ currentCase.planCase?.title || currentCase.planCase?.expected || "正在执行" }}
          </span>
          <Badge v-if="currentCase.planCase" tone="neutral">
            {{ currentCase.planCase.layer }}
          </Badge>
        </div>
        <div v-else-if="running" class="current">
          <span class="current-label">当前用例</span>
          <span class="current-title">正在收尾，等待下一次状态更新</span>
        </div>

        <div v-if="finished" class="finish-actions">
          <AppButton variant="primary" @click="viewResult">查看结果</AppButton>
          <AppButton variant="ghost" @click="go('history')">打开历史</AppButton>
        </div>

        <div v-if="running" class="cancel-zone">
          <template v-if="!confirmCancel">
            <AppButton variant="danger" size="sm" @click="confirmCancel = true">
              取消这次运行
            </AppButton>
            <span class="hint">已完成的用例会保留，未完成部分不计入通过。</span>
          </template>
          <div v-else class="confirm">
            <p class="confirm-text">确认取消？运行会在当前用例结束后停止。</p>
            <div class="row">
              <AppButton variant="danger" size="sm" :loading="cancelling" @click="cancelRun">
                确认取消
              </AppButton>
              <AppButton variant="ghost" size="sm" @click="confirmCancel = false">
                继续运行
              </AppButton>
            </div>
          </div>
          <ErrorNotice
            v-if="cancelError"
            :error="cancelError"
            :show-retry="false"
            @retry="() => {}"
          />
        </div>
      </section>

      <section class="cases card card-flush">
        <div class="cases-head">
          <h2>用例</h2>
          <span class="section-note">展开查看执行输出与证据</span>
        </div>
        <ul class="case-list">
          <li v-for="(runCase, index) in detail.run.cases" :key="runCase.case_id">
            <details class="case">
              <summary class="case-row">
                <span
                  class="result-dot"
                  :class="`result-${runCase.result}`"
                  aria-hidden="true"
                ></span>
                <span class="case-title">
                  {{
                    planCases.get(runCase.case_id)?.title ||
                    planCases.get(runCase.case_id)?.expected ||
                    `用例 ${index + 1}`
                  }}
                </span>
                <Badge v-if="planCases.get(runCase.case_id)" tone="neutral">
                  {{ planCases.get(runCase.case_id)?.layer }}
                </Badge>
                <span class="case-result" :class="`tone-${runCase.result}`">
                  {{ CASE_RESULT_LABEL[runCase.result] }}
                </span>
              </summary>
              <div class="case-body">
                <p v-if="planCases.get(runCase.case_id)?.expected" class="expected">
                  预期：{{ planCases.get(runCase.case_id)?.expected }}
                </p>
                <p
                  v-if="
                    planCases.get(runCase.case_id)?.acceptance_item_id &&
                    itemTexts.get(planCases.get(runCase.case_id)?.acceptance_item_id ?? '')
                  "
                  class="hint"
                >
                  验收标准：
                  {{
                    itemTexts.get(
                      planCases.get(runCase.case_id)?.acceptance_item_id ?? "",
                    )
                  }}
                </p>
                <pre
                  v-if="runCase.attempts.some((attempt) => attempt.message)"
                  class="output"
                >{{
                  runCase.attempts
                    .map((attempt) => attempt.message)
                    .filter(Boolean)
                    .join("\n\n")
                }}</pre>
                <ul
                  v-if="runCase.evidence_refs.length || runCase.attempts.some((a) => a.evidence_refs?.length)"
                  class="evidence"
                >
                  <li
                    v-for="ref in [
                      ...new Set([
                        ...runCase.evidence_refs,
                        ...runCase.attempts.flatMap((a) => a.evidence_refs ?? []),
                      ]),
                    ]"
                    :key="ref"
                    class="mono"
                  >
                    {{ ref }}
                  </li>
                </ul>
                <p
                  v-if="
                    !runCase.attempts.some((attempt) => attempt.message) &&
                    !runCase.evidence_refs.length
                  "
                  class="hint"
                >
                  还没有执行输出或证据。
                </p>
              </div>
            </details>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 4px 2px;
  background: none;
  border: 0;
  color: var(--text-2);
  font-size: var(--fs-12);
  border-radius: var(--r-control);
}

.back:hover {
  color: var(--text);
  background: var(--surface-raised);
}

.status {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.status-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
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

.status-note {
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.5;
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

.finish-actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.cancel-zone {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  align-items: flex-start;
  padding-top: var(--space-2);
  border-top: 1px solid var(--line);
}

.confirm {
  width: 100%;
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

.cases {
  padding: 0;
}

.cases-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--line);
}

.cases-head h2 {
  font-size: var(--fs-14);
}

.case-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.case {
  border-bottom: 1px solid var(--line);
}

.case:last-child {
  border-bottom: 0;
}

.case-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 10px var(--space-4);
  cursor: pointer;
  list-style: none;
}

.case-row::-webkit-details-marker {
  display: none;
}

.case-row:hover {
  background: var(--surface-raised);
}

.result-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex: none;
  background: var(--neutral);
}

.result-passed {
  background: var(--passed);
}

.result-failed {
  background: var(--failed);
}

.result-blocked,
.result-error,
.result-manual_pending {
  background: var(--incomplete);
}

.result-not_run {
  background: var(--text-disabled);
}

.case-title {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--fs-13);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.case-result {
  font-size: var(--fs-12);
  color: var(--text-3);
  flex: none;
}

.case-result.tone-passed {
  color: var(--passed);
}

.case-result.tone-failed {
  color: var(--failed);
}

.case-result.tone-blocked,
.case-result.tone-error,
.case-result.tone-manual_pending {
  color: var(--incomplete);
}

.case-body {
  padding: 0 var(--space-4) var(--space-3) 30px;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.expected {
  font-size: var(--fs-12);
  color: var(--text-2);
  line-height: 1.6;
}

.evidence {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.evidence li {
  color: var(--text-3);
  word-break: break-all;
}

@media (max-width: 520px) {
  .case-row {
    padding: 10px var(--space-3);
  }

  .case-body {
    padding: 0 var(--space-3) var(--space-3) 24px;
  }
}
</style>
