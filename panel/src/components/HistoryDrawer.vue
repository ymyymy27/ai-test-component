<script setup lang="ts">
import { computed } from "vue";

import {
  RUN_STATUS_BADGE,
  RUN_STATUS_LABEL,
  formatFullDateTime,
} from "@/api/labels";
import AppButton from "@/components/AppButton.vue";
import AppDrawer from "@/components/AppDrawer.vue";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import SkeletonBlock from "@/components/SkeletonBlock.vue";
import {
  closeDrawers,
  isBusy,
  openResult,
  refreshHistory,
  session,
} from "@/state/session";

const runs = computed(() => session.history);

async function open(runId: string): Promise<void> {
  const opened = await openResult(runId);
  if (opened) {
    closeDrawers();
  }
}
</script>

<template>
  <AppDrawer
    :open="session.historyOpen"
    title="历史"
    note="点一次运行，就在当前测试单里回放它的结论、覆盖与证据。"
    @close="closeDrawers"
  >
    <div class="drawer-actions">
      <span class="hint">按开始时间倒序，保留每次运行的范围与状态。</span>
      <AppButton
        size="sm"
        variant="ghost"
        :loading="isBusy('history')"
        @click="refreshHistory"
      >
        <Icon name="refresh" :size="14" />
        刷新
      </AppButton>
    </div>

    <SkeletonBlock
      v-if="isBusy('history') && !runs.length"
      :lines="4"
      label="正在读取历史"
    />

    <p v-else-if="!runs.length" class="empty-note">
      还没有运行记录。完成一次测试后，这里会保留每次运行的状态、范围与结果。
    </p>

    <ol v-else class="timeline" data-testid="history-list">
      <li v-for="run in runs" :key="run.run_id" class="timeline-item">
        <span class="node" aria-hidden="true"></span>
        <button
          type="button"
          class="run"
          :class="{ current: run.run_id === session.activeRunId }"
          @click="open(run.run_id)"
        >
          <span class="run-main">
            <span class="run-time numeric">{{ formatFullDateTime(run.started_at) }}</span>
            <span class="run-note">
              {{
                run.run_id === session.activeRunId
                  ? "正在这张测试单里显示"
                  : run.status === "running"
                    ? "运行仍在进行，打开可查看进度"
                    : "打开可回放结论、证据与报告操作"
              }}
            </span>
          </span>
          <Badge :tone="RUN_STATUS_BADGE[run.status]">{{ RUN_STATUS_LABEL[run.status] }}</Badge>
          <span class="chev" aria-hidden="true"><Icon name="chevron-right" :size="14" /></span>
        </button>
      </li>
    </ol>
  </AppDrawer>
</template>

<style scoped>
.drawer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--line);
}

.empty-note {
  padding: var(--space-4) 0;
  color: var(--text-3);
  font-size: var(--fs-12);
  line-height: 1.6;
}

.timeline {
  list-style: none;
  margin: 0;
  padding: var(--space-3) 0 0;
  display: flex;
  flex-direction: column;
}

.timeline-item {
  position: relative;
  padding-left: 18px;
}

.timeline-item::before {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: 3px;
  width: 1px;
  background: var(--line);
}

.timeline-item:first-child::before {
  top: 14px;
}

.timeline-item:last-child::before {
  bottom: calc(100% - 14px);
}

.node {
  position: absolute;
  top: 12px;
  left: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--line-strong);
  border: 1px solid var(--surface);
}

.run {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: 8px var(--space-2);
  background: none;
  border: 0;
  border-radius: var(--r-control);
  text-align: left;
  color: var(--text);
}

.run:hover {
  background: var(--surface-raised);
}

.run.current {
  background: var(--accent-fill);
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
}

.run-note {
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.45;
}

.chev {
  color: var(--text-3);
}
</style>
