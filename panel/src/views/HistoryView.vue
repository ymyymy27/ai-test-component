<script setup lang="ts">
import { computed } from "vue";

import {
  RUN_STATUS_BADGE,
  RUN_STATUS_LABEL,
  formatFullDateTime,
} from "@/api/labels";
import AppButton from "@/components/AppButton.vue";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import SkeletonBlock from "@/components/SkeletonBlock.vue";
import {
  go,
  isBusy,
  openResult,
  refreshHistory,
  session,
  startWizard,
} from "@/state/session";

const runs = computed(() => session.history);

function openRun(runId: string, status: string): void {
  session.activeRunId = runId;
  if (status === "running") {
    go("run");
    return;
  }
  void openResult(runId);
}
</script>

<template>
  <div class="stack" data-testid="history-view">
    <div class="head">
      <button type="button" class="back" @click="go('project')">
        <Icon name="arrow-left" :size="15" />
        返回项目
      </button>
      <AppButton size="sm" variant="ghost" :loading="isBusy('history')" @click="refreshHistory">
        <Icon name="refresh" :size="14" />
        刷新
      </AppButton>
    </div>

    <header class="head-copy">
      <h1>历史记录</h1>
      <p class="muted">按开始时间倒序。报告在结果页生成与导出。</p>
    </header>

    <SkeletonBlock v-if="isBusy('history') && !runs.length" :lines="4" label="正在读取历史" />

    <EmptyState v-else-if="!runs.length" title="还没有运行记录" icon="history">
      <template #body>完成一次测试后，这里会保留每次运行的状态、范围与结果。</template>
      <template #actions>
        <AppButton variant="primary" @click="startWizard">新建测试</AppButton>
      </template>
    </EmptyState>

    <ul v-else class="run-list">
      <li v-for="run in runs" :key="run.run_id">
        <button type="button" class="run" @click="openRun(run.run_id, run.status)">
          <span class="run-main">
            <span class="run-time">{{ formatFullDateTime(run.started_at) }}</span>
            <span class="run-note">
              {{
                run.status === "running"
                  ? "运行仍在进行，打开可查看进度"
                  : "打开可查看结论、证据与报告操作"
              }}
            </span>
          </span>
          <Badge :tone="RUN_STATUS_BADGE[run.status]">{{ RUN_STATUS_LABEL[run.status] }}</Badge>
          <span class="chev" aria-hidden="true"><Icon name="chevron-right" :size="14" /></span>
        </button>
      </li>
    </ul>
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

.head-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.head-copy p {
  font-size: var(--fs-12);
}

.run-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  overflow: hidden;
}

.run-list li {
  border-bottom: 1px solid var(--line);
}

.run-list li:last-child {
  border-bottom: 0;
}

.run {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-3) var(--space-4);
  background: none;
  border: 0;
  text-align: left;
  color: var(--text);
}

.run:hover {
  background: var(--surface-raised);
}

.run-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1 1 auto;
  min-width: 0;
}

.run-time {
  font-size: var(--fs-13);
  font-variant-numeric: tabular-nums;
}

.run-note {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.chev {
  color: var(--text-3);
}

@media (max-width: 480px) {
  .run {
    padding: var(--space-3);
  }
}
</style>
