<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import { api } from "@/api/commands";
import { describeError } from "@/api/errors";
import {
  ISSUE_DISPOSITION_LABEL,
  ISSUE_STATUS_LABEL,
  SEVERITY_LABEL,
  formatDateTime,
} from "@/api/labels";
import type { IssueStatus } from "@/api/types";
import {
  closeIssueDetail,
  loadHistory,
  loadIssues,
  openIssue,
  workspace,
} from "@/state/workspace";

const statusOrder: IssueStatus[] = ["pending", "in_progress", "retest", "closed"];
const filter = ref<IssueStatus | "all">("all");
const closeRunId = ref("");
const reason = ref("");
const disposition = ref("");
const busy = ref(false);
const error = ref("");
const note = ref("");
const caseTitles = ref<Record<string, string>>({});
const closeChecks = ref<Record<string, { ok: boolean; reason: string }>>({});
const checkingClose = ref(false);

const issues = computed(() =>
  workspace.issues
    .filter((item) => filter.value === "all" || item.status === filter.value)
    .sort((a, b) => {
      const order = statusOrder.indexOf(a.status) - statusOrder.indexOf(b.status);
      if (order !== 0) return order;
      return (b.last_at ?? "").localeCompare(a.last_at ?? "");
    }),
);

const grouped = computed(() =>
  statusOrder
    .map((status) => ({
      status,
      rows: issues.value.filter((item) => item.status === status),
    }))
    .filter((group) => group.rows.length > 0),
);

const selected = computed(() => workspace.issue);
const closeCandidates = computed(() =>
  workspace.history.filter((item) => item.status !== "running"),
);

async function resolveCaseTitles(issueId: string): Promise<void> {
  const current = workspace.issue;
  if (!workspace.project || !current || current.issue_id !== issueId) {
    return;
  }
  const runIds = [...new Set(current.occurrences.map((row) => row.run_id))];
  const titles: Record<string, string> = {};
  for (const runId of runIds) {
    try {
      const detail = await api.runStatus(workspace.project.project_id, runId);
      for (const planCase of detail.plan?.cases ?? []) {
        titles[planCase.case_id] = planCase.title || "检查项";
      }
    } catch {
      // 旧运行读不到计划快照时只显示通用名称。
    }
  }
  caseTitles.value = titles;
}

watch(
  () => workspace.issue?.issue_id,
  (issueId) => {
    caseTitles.value = {};
    disposition.value = workspace.issue?.disposition ?? "";
    reason.value = workspace.issue?.reason ?? "";
    closeChecks.value = {};
    closeRunId.value = "";
    if (issueId) {
      void resolveCaseTitles(issueId);
      void checkCloseCandidates(issueId);
    }
  },
);

async function checkCloseCandidates(issueId: string): Promise<void> {
  const current = workspace.issue;
  if (!workspace.project || !current || current.issue_id !== issueId || current.status !== "retest") {
    closeChecks.value = {};
    return;
  }
  checkingClose.value = true;
  const occurrenceRuns = new Set(current.occurrences.map((row) => row.run_id));
  const lastCaseId = current.occurrences[current.occurrences.length - 1]?.case_id ?? "";
  const candidates = closeCandidates.value.slice(0, 12);
  const checks: Record<string, { ok: boolean; reason: string }> = {};
  await Promise.all(
    candidates.map(async (candidate) => {
      const reasons: string[] = [];
      if (occurrenceRuns.has(candidate.run_id)) {
        reasons.push("不能复用问题发生时的运行");
      }
      if (["running", "interrupted", "execution_error"].includes(candidate.status)) {
        reasons.push("运行未正常结束");
      }
      try {
        const detail = await api.runStatus(workspace.project!.project_id, candidate.run_id);
        const runCase = detail.run.cases.find((item) => item.case_id === lastCaseId);
        if (!runCase) {
          reasons.push("这次运行不包含该用例");
        } else {
          if (runCase.result !== "passed") {
            reasons.push("该用例没有通过");
          }
          if (runCase.evidence_refs.length === 0) {
            reasons.push("该用例没有证据");
          }
        }
      } catch {
        reasons.push("读不到这次运行");
      }
      checks[candidate.run_id] = { ok: reasons.length === 0, reason: reasons.join("；") };
    }),
  );
  closeChecks.value = checks;
  checkingClose.value = false;
}

function startIssue(id: string): void {
  void openIssue(id);
}

async function update(
  status: string | null,
  extra: { disposition?: string | null; reason?: string } = {},
): Promise<void> {
  if (!workspace.project || !selected.value) {
    return;
  }
  busy.value = true;
  error.value = "";
  note.value = "";
  try {
    await api.updateIssue(workspace.project.project_id, selected.value.issue_id, {
      ...(status ? { status } : {}),
      ...extra,
    });
    await openIssue(selected.value.issue_id);
    await loadIssues();
  } catch (failure) {
    const friendly = describeError(failure);
    error.value = `${friendly.title}。${friendly.next}`;
  } finally {
    busy.value = false;
  }
}

async function close(): Promise<void> {
  if (!workspace.project || !selected.value || !closeRunId.value) {
    error.value = "先选一次验证运行。";
    return;
  }
  busy.value = true;
  error.value = "";
  note.value = "";
  try {
    await api.closeIssue(
      workspace.project.project_id,
      selected.value.issue_id,
      closeRunId.value,
    );
    await openIssue(selected.value.issue_id);
    await loadIssues();
    note.value = "问题已关闭。";
  } catch (failure) {
    const friendly = describeError(failure);
    error.value = `${friendly.title}。${friendly.next}`;
  } finally {
    busy.value = false;
  }
}

async function saveDisposition(): Promise<void> {
  if (!selected.value) {
    return;
  }
  await update(null, {
    disposition: disposition.value || null,
    reason: reason.value,
  });
}

async function reopen(): Promise<void> {
  if (!reason.value.trim()) {
    error.value = "重开问题要写理由。";
    return;
  }
  await update("in_progress", { reason: reason.value });
}

onMounted(() => {
  void loadIssues();
  void loadHistory();
});
</script>

<template>
  <div class="issues-page">
    <header class="page-head">
      <div>
        <h1>问题</h1>
        <p>相同失败原因合并在一起；关闭需要一次新运行的实际通过证据。</p>
      </div>
      <button type="button" class="ui-btn" @click="loadIssues">刷新</button>
    </header>

    <div v-if="!selected" class="filters">
      <button
        type="button"
        class="filter"
        :class="{ on: filter === 'all' }"
        @click="filter = 'all'"
      >
        全部 {{ workspace.issues.length }}
      </button>
      <button
        v-for="status in statusOrder"
        :key="status"
        type="button"
        class="filter"
        :class="{ on: filter === status }"
        @click="filter = status"
      >
        {{ ISSUE_STATUS_LABEL[status] }}
        {{ workspace.issues.filter((item) => item.status === status).length }}
      </button>
    </div>

    <template v-if="!selected">
      <p v-if="issues.length === 0" class="ui-help empty">还没有问题。</p>
      <div v-for="group in grouped" :key="group.status" class="group">
        <div class="group-head">
          <span>{{ ISSUE_STATUS_LABEL[group.status] }}</span>
          <span>{{ group.rows.length }}</span>
        </div>
        <button
          v-for="item in group.rows"
          :key="item.issue_id"
          type="button"
          class="issue-row"
          @click="startIssue(item.issue_id)"
        >
          <span class="issue-main">
            <span class="title">
              <span class="severity">{{ SEVERITY_LABEL[item.severity] }}</span>
              {{ item.title }}
            </span>
            <span class="meta">
              {{ item.occurrences }} 次发生
              <template v-if="item.last_at"> · 最近 {{ formatDateTime(item.last_at) }}</template>
              <template v-if="item.last_result"> · 上次{{ item.last_result === "passed" ? "通过" : "失败" }}</template>
              <template v-if="item.unstable"> · 疑似不稳定</template>
            </span>
          </span>
          <span class="issue-state">
            {{ ISSUE_STATUS_LABEL[item.status] }}
            <template v-if="item.status === 'closed' && item.last_result === 'failed'">
              · 需重开
            </template>
          </span>
        </button>
      </div>
    </template>

    <div v-else class="detail">
      <button type="button" class="ui-link" @click="closeIssueDetail">← 返回问题</button>
      <div class="detail-head">
        <h2>{{ selected.title }}</h2>
        <span class="issue-state">
          {{ ISSUE_STATUS_LABEL[selected.status] }} · {{ SEVERITY_LABEL[selected.severity] }}
        </span>
      </div>
      <p class="meta">
        {{ selected.occurrences.length }} 次发生
        <template v-if="selected.last_result"> · 上次{{ selected.last_result === "passed" ? "通过" : "失败" }}</template>
        <template v-if="selected.unstable"> · 疑似不稳定</template>
      </p>

      <section class="section">
        <h3>失败原因</h3>
        <p class="reason-title">{{ selected.title }}</p>
        <p v-if="selected.expected" class="ui-help">预期：{{ selected.expected }}</p>
        <p v-if="selected.actual" class="ui-help">实际：{{ selected.actual }}</p>
      </section>

      <section class="section">
        <h3>发生记录</h3>
        <div v-for="row in selected.occurrences" :key="`${row.run_id}-${row.at}`" class="occurrence">
          <span>{{ formatDateTime(row.at) }}</span>
          <span>{{ caseTitles[row.case_id] || "检查项" }}</span>
          <span class="ui-help">{{ row.evidence_refs.length }} 个证据</span>
        </div>
      </section>

      <section class="section">
        <h3>处理结论</h3>
        <select v-model="disposition" class="ui-select">
          <option value="">未填写</option>
          <option v-for="(label, value) in ISSUE_DISPOSITION_LABEL" :key="value" :value="value">
            {{ label }}
          </option>
        </select>
        <textarea v-model="reason" class="ui-textarea" placeholder="写理由；非缺陷、重复、延期必须写" />
        <button type="button" class="ui-btn" :disabled="busy" @click="saveDisposition">
          保存处理结论
        </button>
      </section>

      <section class="section">
        <h3>下一步</h3>
        <div class="actions">
          <button
            v-if="selected.status === 'pending'"
            type="button"
            class="ui-btn primary"
            :disabled="busy"
            @click="update('in_progress')"
          >
            开始处理
          </button>
          <button
            v-if="selected.status === 'in_progress'"
            type="button"
            class="ui-btn primary"
            :disabled="busy"
            @click="update('retest')"
          >
            提交待复测
          </button>
          <button
            v-if="selected.status === 'in_progress'"
            type="button"
            class="ui-btn"
            :disabled="busy"
            @click="update('pending')"
          >
            退回待确认
          </button>
          <button
            v-if="selected.status === 'retest'"
            type="button"
            class="ui-btn"
            :disabled="busy"
            @click="update('in_progress')"
          >
            继续处理
          </button>
          <button
            v-if="selected.status === 'closed'"
            type="button"
            class="ui-btn"
            :disabled="busy"
            @click="reopen"
          >
            重开问题
          </button>
        </div>

        <div v-if="selected.status === 'retest'" class="close-box">
          <p class="ui-help">
            关闭需要一个新运行：包含最近发生记录的用例、用例通过、且有证据。不能复用问题发生时的运行。
          </p>
          <p v-if="checkingClose" class="ui-help">正在核对候选运行…</p>
          <select v-model="closeRunId" class="ui-select">
            <option value="">选择验证运行</option>
            <option
              v-for="run in closeCandidates"
              :key="run.run_id"
              :value="run.run_id"
              :disabled="closeChecks[run.run_id] && !closeChecks[run.run_id].ok"
            >
              {{ formatDateTime(run.started_at) }} ·
              {{ run.outcome === "passed" ? "通过" : run.outcome === "failed" ? "没通过" : "未完成" }}
              · 计划 {{ run.plan_no ?? "—" }}
              {{ closeChecks[run.run_id] && !closeChecks[run.run_id].ok ? " · 不满足关闭条件" : "" }}
            </option>
          </select>
          <p v-if="closeRunId && closeChecks[closeRunId] && !closeChecks[closeRunId].ok" class="ui-error">
            {{ closeChecks[closeRunId].reason }}
          </p>
          <button
            type="button"
            class="ui-btn primary"
            :disabled="busy || checkingClose || !closeRunId || (closeChecks[closeRunId] ? !closeChecks[closeRunId].ok : false)"
            @click="close"
          >
            关闭问题
          </button>
        </div>
      </section>

      <p v-if="error" class="ui-error">{{ error }}</p>
      <p v-if="note" class="ui-help">{{ note }}</p>
    </div>
  </div>
</template>

<style scoped>
.issues-page {
  padding: var(--space-4);
  max-width: var(--layout-max);
  margin: 0 auto;
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

.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line);
}

.filter {
  padding: 4px 0 3px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--text-3);
  font-size: var(--fs-10);
}

.filter.on {
  border-bottom-color: var(--accent);
  color: var(--text);
}

.group {
  margin-top: 10px;
}

.group-head {
  display: flex;
  justify-content: space-between;
  padding: 5px 0;
  color: var(--text-3);
  font-size: var(--fs-10);
  letter-spacing: 0.04em;
}

.issue-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  width: 100%;
  padding: 8px 0;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: transparent;
  text-align: left;
}

.issue-row:hover {
  background: var(--surface-raised);
}

.issue-main {
  min-width: 0;
}

.title {
  display: block;
  color: var(--text);
  font-size: var(--fs-12);
  line-height: 1.45;
}

.severity {
  display: inline-block;
  margin-right: 4px;
  padding: 0 3px;
  border: 1px solid var(--line-strong);
  color: var(--text-2);
  font-family: var(--font-mono);
  font-size: var(--fs-9, 9px);
}

.meta {
  display: block;
  margin-top: 2px;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  line-height: 1.5;
}

.issue-state {
  flex: none;
  color: var(--text-3);
  font-size: var(--fs-10);
  line-height: 1.5;
  text-align: right;
  white-space: nowrap;
}

.empty {
  padding: var(--space-5) 0;
}

.detail {
  padding-top: 10px;
}

.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  margin-top: 8px;
}

.detail-head h2 {
  font-size: var(--fs-16);
  line-height: 1.4;
}

.section {
  padding: 10px 0;
  border-top: 1px solid var(--line);
}

.section h3 {
  margin: 0 0 5px;
  font-size: var(--fs-11);
  font-weight: 600;
}

.reason-title {
  margin: 0 0 4px;
  color: var(--text-2);
  font-size: var(--fs-12);
  line-height: 1.5;
}

.occurrence {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr) auto;
  gap: 8px;
  padding: 4px 0;
  border-bottom: 1px solid var(--line);
  color: var(--text-2);
  font-size: var(--fs-10);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.close-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--line-strong);
}

.section .ui-select,
.section .ui-textarea {
  margin-bottom: 6px;
}

@media (max-width: 520px) {
  .issues-page {
    padding: var(--space-3);
  }

  .occurrence {
    grid-template-columns: 1fr;
  }
}
</style>
