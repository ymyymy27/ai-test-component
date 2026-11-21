<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import ErrorState from "@/components/ErrorState.vue";
import IssueStatusDialog from "@/components/IssueStatusDialog.vue";
import PageHeader from "@/components/PageHeader.vue";
import { issueCloseGate } from "@/domain/derive";
import { DISPOSITION_LABEL, ISSUE_STATUS_LABEL, statusText } from "@/domain/labels";
import type { Issue, IssueDisposition, IssueStatus, Run, Task } from "@/domain/types";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();
const projectId = computed(() => String(route.params.projectId ?? ""));
const issueId = computed(() => String(route.params.issueId ?? ""));
const project = computed(() => workspace.currentProject);
const issue = computed<Issue | null>(() => workspace.issueById(issueId.value));
const task = computed<Task | null>(
  () => project.value?.tasks.find((entry) => entry.id === issue.value?.taskId) ?? null,
);
const plan = computed(
  () => task.value?.plans.find((entry) => entry.id === issue.value?.planId) ?? null,
);
const run = computed<Run | null>(
  () => task.value?.runs.find((entry) => entry.id === issue.value?.runId) ?? null,
);
const gate = computed(() =>
  project.value && issue.value ? issueCloseGate(project.value, issue.value) : null,
);
const verificationRun = computed<Run | null>(() => {
  if (!issue.value?.verificationRunId) {
    return null;
  }
  return task.value?.runs.find((entry) => entry.id === issue.value?.verificationRunId) ?? null;
});
const issueItemNo = computed(
  () =>
    issue.value?.itemNo ||
    task.value?.acceptanceItems.find((item) => item.id === issue.value?.itemId)?.no ||
    "—",
);
const issueItemText = computed(
  () =>
    issue.value?.itemText ||
    task.value?.acceptanceItems.find((item) => item.id === issue.value?.itemId)?.text ||
    "",
);
const occurrenceCount = computed(
  () => issue.value?.occurrences.filter((entry) => entry.result !== "passed").length ?? 0,
);
const lastOccurrenceAt = computed(() => {
  const times =
    issue.value?.occurrences.map((entry) => new Date(entry.time).getTime()).filter(Number.isFinite) ??
    [];
  return times.length ? new Date(Math.max(...times)).toISOString() : issue.value?.updatedAt ?? "";
});
const statusOpen = ref(false);
const statusError = ref("");
const closing = ref(false);
const rerunBusy = computed(
  () =>
    Boolean(issue.value?.taskId) &&
    workspace.busyKey(`rerun:${issue.value?.taskId}`),
);

function runLabel(runId: string): string {
  if (!runId) {
    return "未绑定运行";
  }
  const found = task.value?.runs.find((entry) => entry.id === runId);
  return found ? new Date(found.startedAt).toLocaleString("zh-CN") : "运行已归档";
}

function openResult(target: Run | null): void {
  if (!target) {
    return;
  }
  void router.push(
    `/projects/${projectId.value}/tasks/${target.taskId}/result?run=${target.id}`,
  );
}

async function saveStatus(input: {
  status: IssueStatus;
  reason: string;
  disposition: IssueDisposition | null;
}): Promise<void> {
  if (!issue.value) {
    return;
  }
  try {
    await workspace.updateIssue(projectId.value, issue.value.id, input);
    statusOpen.value = false;
    ui.showToast("问题状态已更新。", "success");
  } catch (error) {
    statusError.value = error instanceof Error ? error.message : "状态更新失败，请重试。";
  }
}

function openStatus(): void {
  statusError.value = "";
  statusOpen.value = true;
}

async function closeIssue(): Promise<void> {
  const currentGate = gate.value;
  if (!issue.value || !currentGate?.ok) {
    return;
  }
  const reason = await ui.askConfirm({
    title: "关闭问题",
    body: `将绑定运行 ${runLabel(currentGate.run.id)} 的通过证据关闭问题。写一句关闭理由。`,
    confirmLabel: "关闭问题",
    requireText: true,
    textLabel: "关闭理由",
    placeholder: "例如：删除标记修复后复测通过。",
  });
  if (reason === null) {
    return;
  }
  closing.value = true;
  try {
    await workspace.closeIssue(projectId.value, issue.value.id, currentGate.run.id, reason);
    ui.showToast("问题已关闭，绑定这次通过的运行。", "success");
  } catch {
    // store 已提示。
  } finally {
    closing.value = false;
  }
}

async function rerun(): Promise<void> {
  if (!issue.value || rerunBusy.value) {
    return;
  }
  if (!issue.value.itemId) {
    ui.showToast("这个问题没有绑定验收项，不能按验收项重跑。", "info");
    return;
  }
  try {
    await workspace.prepareRerun(projectId.value, issue.value.taskId, [issue.value.itemId]);
    await router.push(`/projects/${projectId.value}/tasks/${issue.value.taskId}/plan`);
  } catch {
    // store 已提示。
  }
}
</script>

<template>
  <div v-if="issue" class="page">
    <PageHeader
      :title="issue.title"
      :subtitle="`${issue.severity} · ${ISSUE_STATUS_LABEL[issue.status]} · ${issue.source === 'manual' ? '人工登记' : '自动归并'} · 发生 ${occurrenceCount} 次 · 最近 ${new Date(lastOccurrenceAt).toLocaleString('zh-CN')}`"
    >
      <template #actions>
        <span class="badge" :class="issue.status === 'closed' ? 'is-neutral' : 'is-warn'">
          {{ ISSUE_STATUS_LABEL[issue.status] }}
        </span>
        <span v-if="issue.unstable" class="badge is-warn">疑似不稳定</span>
        <button
          class="btn btn-sm"
          type="button"
          @click="router.push(`/projects/${projectId}/issues`)"
        >
          全部问题
        </button>
        <button class="btn btn-sm" type="button" @click="openStatus">更新状态</button>
      </template>
    </PageHeader>

    <section class="section">
      <dl class="info-grid binding-grid">
        <div class="info-cell">
          <dt>任务</dt>
          <dd>
            <button
              v-if="task"
              class="link-btn"
              type="button"
              @click="router.push(`/projects/${projectId}/tasks/${task.id}/details`)"
            >
              {{ task.title }}
            </button>
            <template v-else>任务已归档</template>
          </dd>
        </div>
        <div class="info-cell">
          <dt>计划</dt>
          <dd>
            <button
              v-if="plan"
              class="link-btn"
              type="button"
              @click="router.push(`/projects/${projectId}/tasks/${task?.id}/plan`)"
            >
              计划 {{ plan.no }} · 第 {{ plan.revision }} 版
            </button>
            <template v-else>计划已归档</template>
          </dd>
        </div>
        <div class="info-cell">
          <dt>运行</dt>
          <dd>
            <button v-if="run" class="link-btn" type="button" @click="openResult(run)">
              {{ runLabel(issue.runId) }}
            </button>
            <template v-else>{{ runLabel(issue.runId) }}</template>
          </dd>
        </div>
        <div class="info-cell">
          <dt>验收项</dt>
          <dd>
            {{ issueItemNo }}
            <span v-if="issueItemText" class="muted">· {{ issueItemText }}</span>
          </dd>
        </div>
      </dl>
    </section>

    <section class="section">
      <h2 class="section-title-lg">预期与实际</h2>
      <div class="case-compare">
        <div class="case-col">
          <span class="case-label">预期</span>
          <span class="case-value">{{ issue.expected }}</span>
        </div>
        <div class="case-col">
          <span class="case-label">实际</span>
          <span class="case-value is-bad">{{ issue.actual }}</span>
        </div>
      </div>
      <h2 class="section-title-lg related-title">复现步骤</h2>
      <ol class="list-plain">
        <li v-for="(step, index) in issue.steps" :key="index">
          {{ String(index + 1).padStart(2, "0") }} {{ step }}
        </li>
      </ol>
    </section>

    <section class="section">
      <h2 class="section-title-lg">发生记录</h2>
      <div v-if="issue.occurrences.length" class="table-wrap" tabindex="0" role="region" aria-label="问题发生记录表">
        <table class="table">
          <thead>
            <tr>
              <th scope="col">时间</th>
              <th scope="col">运行</th>
              <th scope="col">结果</th>
              <th scope="col">说明</th>
              <th scope="col">证据</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(entry, index) in issue.occurrences" :key="index">
              <td class="cell-num" data-label="时间">
                {{ new Date(entry.time).toLocaleString("zh-CN") }}
              </td>
              <td data-label="运行">{{ runLabel(entry.runId) }}</td>
              <td data-label="结果">{{ statusText(entry.result) }}</td>
              <td data-label="说明">{{ entry.note }}</td>
              <td data-label="证据">
                {{ entry.evidenceRefs.length ? entry.evidenceRefs.join("、") : "—" }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="issue.occurrences.length === 0" class="doc-note">
        还没有发生记录；人工登记的问题可以直接进入处理。
      </p>
    </section>

    <section class="section">
      <h2 class="section-title-lg">处置</h2>
      <p class="doc-p">
        {{ issue.disposition ? DISPOSITION_LABEL[issue.disposition] : "尚未处置" }}
        <template v-if="issue.reason"> · 理由：{{ issue.reason }}</template>
        <template v-if="issue.fixVersion"> · 版本：{{ issue.fixVersion }}</template>
      </p>
      <p v-if="verificationRun" class="doc-note">
        关闭依据：{{ runLabel(verificationRun.id) }} 的通过运行。
      </p>
    </section>

    <section class="section">
      <h2 class="section-title-lg">证据</h2>
      <div v-if="issue.evidenceRefs.length" class="evidence-row">
        <span v-for="name in issue.evidenceRefs" :key="name" class="evidence-chip">
          {{ name }}
        </span>
      </div>
      <p v-else class="doc-note">未附证据。</p>
    </section>

    <section v-if="issue.status === 'retest'" class="section">
      <h2 class="section-title-lg">关闭问题</h2>
      <p class="doc-p">
        关闭必须绑定一次新运行：运行要包含该用例、有效结果为通过，并且有证据；问题发生时的运行不能复用。
      </p>
      <div class="doc-note" :class="{ 'field-error': !gate?.ok }">
        {{ gate?.reason }}
      </div>
      <div class="form-actions">
        <button
          class="btn btn-primary"
          type="button"
          :disabled="!gate?.ok || closing"
          :aria-busy="closing"
          @click="closeIssue"
        >
          关闭问题
        </button>
        <button
          class="btn"
          type="button"
          :disabled="!issue.itemId || rerunBusy"
          :aria-busy="rerunBusy"
          :title="issue.itemId ? undefined : '未绑定验收项，无法按项重跑'"
          @click="rerun"
        >
          {{ rerunBusy ? "正在准备重跑…" : issue.itemId ? "去重跑这一项" : "未绑定验收项，无法按项重跑" }}
        </button>
      </div>
    </section>
    <section v-else-if="issue.status !== 'closed'" class="section">
      <p class="doc-note">问题进入“待复测”后，才能绑定新运行关闭；当前可以先更新状态。</p>
      <button
        class="btn btn-sm"
        type="button"
        :disabled="!issue.itemId || rerunBusy"
        :aria-busy="rerunBusy"
        :title="issue.itemId ? undefined : '未绑定验收项，无法按项重跑'"
        @click="rerun"
      >
        {{ rerunBusy ? "正在准备重跑…" : issue.itemId ? "去重跑这一项" : "未绑定验收项，无法按项重跑" }}
      </button>
    </section>
    <section v-else class="section">
      <h2 class="section-title-lg">重新打开</h2>
      <p class="doc-note">问题已关闭。如果同一问题再次出现，重新打开并写原因。</p>
      <button class="btn btn-sm" type="button" @click="openStatus">重新打开问题</button>
    </section>

    <IssueStatusDialog
      v-model="statusOpen"
      :issue="issue"
      :busy="workspace.busyKey(`issue:${issue.id}`)"
      :server-error="statusError"
      @save="saveStatus"
    />
  </div>
  <div v-else class="page">
    <ErrorState
      title="问题不存在"
      text="这个问题可能已被移除；回到问题列表重新选择。"
      retry-label="回到问题列表"
      @retry="router.push(`/projects/${projectId}/issues`)"
    />
  </div>
</template>
