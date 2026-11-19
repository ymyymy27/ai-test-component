<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import ErrorState from "@/components/ErrorState.vue";
import IssueStatusDialog from "@/components/IssueStatusDialog.vue";
import PageHeader from "@/components/PageHeader.vue";
import { issueCloseGate } from "@/domain/derive";
import { DISPOSITION_LABEL, ISSUE_STATUS_LABEL, SEVERITY_LABEL, statusText } from "@/domain/labels";
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
  () => task.value?.acceptanceItems.find((item) => item.id === issue.value?.itemId)?.no ?? "—",
);
const severityText = computed(() => (issue.value ? SEVERITY_LABEL[issue.value.severity] : ""));
const statusOpen = ref(false);
const closing = ref(false);

function reportFor(target: Run | null): string | null {
  if (!target || target.reportIds.length === 0) {
    return null;
  }
  return target.reportIds[target.reportIds.length - 1] ?? null;
}

function runLabel(runId: string): string {
  const found = task.value?.runs.find((entry) => entry.id === runId);
  return found ? new Date(found.startedAt).toLocaleString("zh-CN") : "运行已归档";
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
  } catch {
    // store 已提示。
  }
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
  if (!issue.value || !issue.value.itemId) {
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

function openReport(target: Run | null): void {
  const reportId = reportFor(target);
  if (reportId) {
    void router.push(`/projects/${projectId.value}/reports/${reportId}`);
  }
}
</script>

<template>
  <div v-if="issue" class="page">
    <PageHeader
      kicker="问题详情"
      :title="issue.title"
      :subtitle="`${issue.severity} · ${ISSUE_STATUS_LABEL[issue.status]} · ${issue.source === 'manual' ? '人工登记' : '自动归并'} · 发生 ${issue.occurrences.length} 次 · 最近 ${new Date(issue.updatedAt).toLocaleString('zh-CN')}`"
    >
      <template #actions>
        <button
          class="btn btn-sm"
          type="button"
          @click="router.push(`/projects/${projectId}/issues`)"
        >
          全部问题
        </button>
        <span v-if="issue.unstable" class="badge is-warn">疑似不稳定</span>
        <button class="btn btn-sm" type="button" @click="statusOpen = true">更新状态</button>
        <button
          v-if="issue.status === 'retest'"
          class="btn btn-sm btn-primary"
          type="button"
          :disabled="!gate?.ok || closing"
          :aria-busy="closing"
          :title="gate?.ok ? undefined : gate?.reason"
          @click="closeIssue"
        >
          {{ closing ? "正在关闭…" : "关闭问题" }}
        </button>
      </template>
    </PageHeader>

    <section class="section">
      <dl class="info-grid binding-grid">
        <div class="info-cell">
          <dt>项目</dt>
          <dd>{{ project?.name }}</dd>
        </div>
        <div class="info-cell">
          <dt>任务</dt>
          <dd>{{ task?.title ?? "任务已归档" }}</dd>
        </div>
        <div class="info-cell">
          <dt>计划</dt>
          <dd>{{ plan ? `计划 ${plan.no} · 第 ${plan.revision} 版` : "计划已归档" }}</dd>
        </div>
        <div class="info-cell">
          <dt>运行</dt>
          <dd>
            <button v-if="reportFor(run)" class="link-btn" type="button" @click="openReport(run)">
              {{ runLabel(issue.runId) }}
            </button>
            <template v-else>{{ runLabel(issue.runId) }}</template>
          </dd>
        </div>
        <div class="info-cell">
          <dt>验收项</dt>
          <dd>{{ issueItemNo }}</dd>
        </div>
        <div class="info-cell">
          <dt>严重度</dt>
          <dd>{{ severityText }}</dd>
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
      <div v-if="issue.occurrences.length" class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th scope="col">时间</th>
              <th scope="col">运行</th>
              <th scope="col">结果</th>
              <th scope="col">说明</th>
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
        <button class="btn" type="button" @click="rerun">去重跑这一项</button>
      </div>
    </section>
    <section v-else-if="issue.status !== 'closed'" class="section">
      <p class="doc-note">问题进入“待复测”后，才能绑定新运行关闭；当前可以先更新状态。</p>
      <button class="btn btn-sm" type="button" @click="rerun">去重跑这一项</button>
    </section>
    <section v-else class="section">
      <p class="doc-note">问题已关闭。如果同一问题再次出现，更新状态并写重开原因。</p>
    </section>

    <IssueStatusDialog
      v-model="statusOpen"
      :issue="issue"
      :busy="workspace.busyKey(`issue:${issue.id}`)"
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
