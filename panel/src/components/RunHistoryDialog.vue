<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";

import { durationText } from "@/domain/ids";
import { RUN_STATUS_LABEL, VERDICT_LABEL, statusText } from "@/domain/labels";
import type { Report, Run, Task } from "@/domain/types";
import StatusMark from "./StatusMark.vue";

const props = defineProps<{
  run: Run | null;
  task: Task | null;
  report: Report | null;
}>();

const emit = defineEmits<{
  openReport: [reportId: string];
  openResult: [];
}>();

const open = defineModel<boolean>({ default: false });
const dialogRef = ref<HTMLDialogElement | null>(null);
const cases = computed(() => props.run?.cases ?? []);

watch(open, async (value) => {
  const dialog = dialogRef.value;
  if (!dialog) {
    return;
  }
  if (value) {
    await nextTick();
    if (!dialog.open) {
      dialog.showModal();
    }
  } else if (dialog.open) {
    dialog.close();
  }
});

// 从 /history?run= 首次进入时父组件在挂载前已把 open 设为 true，
// watch 不是 immediate，需要在挂载后补一次 showModal。
onMounted(async () => {
  if (!open.value) {
    return;
  }
  await nextTick();
  const dialog = dialogRef.value;
  if (dialog && !dialog.open) {
    dialog.showModal();
  }
});

function close(): void {
  open.value = false;
}

function openReport(): void {
  if (props.report) {
    emit("openReport", props.report.id);
  }
}

function onDialogClick(event: MouseEvent): void {
  if (event.target === dialogRef.value) {
    close();
  }
}
</script>

<template>
  <dialog
    ref="dialogRef"
    class="dialog"
    aria-labelledby="run-dialog-title"
    @cancel.prevent="close"
    @close="open = false"
    @click="onDialogClick"
  >
    <div v-if="run" class="dialog-form">
      <header class="dialog-head">
        <div>
          <h2 id="run-dialog-title" class="dialog-title">
            {{ run.taskTitle || task?.title || "任务已归档" }}
          </h2>
          <p class="dialog-text">
            {{ RUN_STATUS_LABEL[run.status] }} · {{ VERDICT_LABEL[run.verdict] }} ·
            {{ new Date(run.startedAt).toLocaleString("zh-CN") }} ·
            {{ durationText(run.elapsedMs / 1000) }}
          </p>
        </div>
        <button class="icon-btn" type="button" aria-label="关闭" @click="close">
          <svg
            viewBox="0 0 16 16"
            width="15"
            height="15"
            fill="none"
            stroke="currentColor"
            stroke-width="1.5"
          >
            <path d="M4 4l8 8M12 4l-8 8" stroke-linecap="round" />
          </svg>
        </button>
      </header>
      <div v-if="run.status === 'interrupted'" class="notice is-old">
        <span>运行已中断，已完成结果保留；不提供恢复或续跑，也没有生成报告。</span>
      </div>
      <dl class="info-grid">
        <div class="info-cell">
          <dt>本次范围</dt>
          <dd>{{ run.counts.selected }} / {{ run.counts.applicable }} 项</dd>
        </div>
        <div class="info-cell">
          <dt>通过 / 失败 / 未完成</dt>
          <dd>{{ run.counts.passed }} / {{ run.counts.failed }} / {{ run.counts.incomplete }}</dd>
        </div>
        <div class="info-cell">
          <dt>确认人</dt>
          <dd>{{ run.confirmedBy || "—" }}</dd>
        </div>
        <div class="info-cell">
          <dt>报告</dt>
          <dd>{{ report ? `第 ${report.version} 版` : "没有报告" }}</dd>
        </div>
      </dl>
      <div class="table-wrap" tabindex="0" role="region" aria-label="运行验收项明细表">
        <table class="table">
          <thead>
            <tr>
              <th scope="col">验收项</th>
              <th scope="col">结果</th>
              <th scope="col">用例</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in run.items" :key="item.itemId">
              <td class="cell-main" data-label="验收项">{{ item.no }} {{ item.text }}</td>
              <td data-label="结果">
                <StatusMark :result="item.outcome" /> {{ statusText(item.outcome) }}
              </td>
              <td data-label="用例">
                {{
                  cases
                    .filter((testCase) =>
                      [testCase.itemId, ...(testCase.caseItemIds ?? [])]
                        .filter(Boolean)
                        .includes(item.itemId),
                    )
                    .map((testCase) => testCase.title)
                    .join("；") || "—"
                }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="doc-note">
        这是只读快照：文字来自运行启动时的任务、计划与用例快照，不跟随当前任务变化。
      </p>      <footer class="dialog-actions">
        <button class="btn" type="button" @click="close">关闭</button>
        <button class="btn" type="button" @click="emit('openResult')">查看运行结果</button>
        <button v-if="report" class="btn btn-primary" type="button" @click="openReport">
          查看报告
        </button>
      </footer>
    </div>
  </dialog>
</template>
