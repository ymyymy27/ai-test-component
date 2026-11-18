<script setup lang="ts">
import { onMounted } from "vue";

import {
  RUN_STATUS_LABEL,
  VERDICT_LABEL,
  formatDateTime,
  formatDuration,
} from "@/api/labels";
import type { RunSummary } from "@/api/types";
import { loadHistory, openHistoryRun, workspace } from "@/state/workspace";

function titleOf(run: RunSummary): string {
  return run.task_title || "未命名任务";
}

function planLabel(run: RunSummary): string {
  if (!run.plan_no) {
    return "计划";
  }
  return run.plan_revision && run.plan_revision > 1
    ? `计划 ${run.plan_no} · 第 ${run.plan_revision} 版`
    : `计划 ${run.plan_no}`;
}

function resultLabel(run: RunSummary): string {
  if (run.outcome) {
    return VERDICT_LABEL[run.outcome];
  }
  return RUN_STATUS_LABEL[run.status];
}

function resultTone(run: RunSummary): string {
  if (run.outcome) {
    return run.outcome;
  }
  return run.status === "running" ? "running" : "neutral";
}

function countsOf(run: RunSummary): string {
  const counts = run.counts ?? {};
  const parts: string[] = [];
  if (counts.passed) {
    parts.push(`通过 ${counts.passed}`);
  }
  if (counts.failed) {
    parts.push(`失败 ${counts.failed}`);
  }
  if (counts.incomplete) {
    parts.push(`未完成 ${counts.incomplete}`);
  }
  return parts.join(" · ");
}

onMounted(() => {
  void loadHistory();
});
</script>

<template>
  <div class="history-page">
    <header class="page-head">
      <div>
        <h1>历史</h1>
        <p>整个项目最近的运行；点开是只读台账，计划与任务使用当时的快照。</p>
      </div>
      <button type="button" class="ui-btn" @click="loadHistory">刷新</button>
    </header>

    <p v-if="workspace.history.length === 0" class="ui-help empty">还没有运行记录。</p>

    <div v-else class="list">
      <button
        v-for="run in workspace.history"
        :key="run.run_id"
        type="button"
        class="row"
        @click="openHistoryRun(run.run_id)"
      >
        <span class="time">{{ formatDateTime(run.started_at) }}</span>
        <span class="main">
          <span class="title">{{ titleOf(run) }}</span>
          <span class="meta">
            {{ planLabel(run) }}
            <template v-if="run.scope"> · 本次 {{ run.scope.selected }} / 共 {{ run.scope.applicable }} 项</template>
            <template v-if="run.task_snapshot === false"> · 无任务快照</template>
          </span>
        </span>
        <span class="result" :class="resultTone(run)">
          {{ resultLabel(run) }}
          <span v-if="countsOf(run)" class="counts">{{ countsOf(run) }}</span>
        </span>
        <span class="duration">{{ formatDuration(run.elapsed_seconds) }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.history-page {
  max-width: var(--layout-max);
  margin: 0 auto;
  padding: var(--space-4);
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line-strong);
}

.page-head h1 {
  font-size: var(--fs-20);
}

.page-head p {
  margin-top: 3px;
  color: var(--text-3);
  font-size: var(--fs-11);
  line-height: 1.5;
}

.empty {
  padding: var(--space-5) 0;
}

.list {
  margin-top: 6px;
}

.row {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr) 116px 62px;
  gap: 10px;
  align-items: start;
  width: 100%;
  padding: 8px 0;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: transparent;
  text-align: left;
}

.row:hover {
  background: var(--surface-raised);
}

.time {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  line-height: 1.5;
}

.main {
  min-width: 0;
}

.title {
  display: block;
  color: var(--text);
  font-size: var(--fs-12);
  line-height: 1.45;
}

.meta {
  display: block;
  margin-top: 2px;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  line-height: 1.45;
}

.result {
  color: var(--text-2);
  font-size: var(--fs-11);
  line-height: 1.45;
  text-align: right;
}

.result.passed {
  color: var(--passed);
}

.result.failed {
  color: var(--failed);
}

.result.incomplete {
  color: var(--incomplete);
}

.result.running {
  color: var(--accent);
}

.counts {
  display: block;
  margin-top: 2px;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-9, 9px);
}

.duration {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  line-height: 1.5;
  text-align: right;
}

@media (max-width: 620px) {
  .row {
    grid-template-columns: 76px minmax(0, 1fr) 84px;
  }

  .duration {
    display: none;
  }
}

@media (max-width: 520px) {
  .history-page {
    padding: var(--space-3);
  }

  .row {
    grid-template-columns: 68px minmax(0, 1fr);
  }

  .result {
    grid-column: 2;
    text-align: left;
  }
}
</style>
