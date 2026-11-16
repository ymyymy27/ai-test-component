<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api } from "@/api/commands";
import { describeError, type FriendlyError } from "@/api/errors";
import { ISSUE_STATUS_LABEL, SEVERITY_LABEL, formatDateTime } from "@/api/labels";
import type { IssueStatus, IssueSummary } from "@/api/types";
import AppButton from "@/components/AppButton.vue";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Icon from "@/components/Icon.vue";
import SkeletonBlock from "@/components/SkeletonBlock.vue";
import { go, openHistory, openResult, session } from "@/state/session";

const EDITABLE_STATUSES: IssueStatus[] = ["pending", "in_progress", "retest"];

const issues = ref<IssueSummary[]>([]);
const loading = ref(false);
const loadError = ref<FriendlyError | null>(null);
const actionError = ref("");
const saving = ref("");
const closing = ref("");
const notice = ref("");

const run = computed(() => session.result?.run ?? null);
const canClose = computed(() => Boolean(run.value && run.value.status !== "running"));

onMounted(() => {
  void loadIssues();
});

function returnToResult(): void {
  const runId = session.activeRunId || session.result?.run.run_id || "";
  if (runId) {
    void openResult(runId);
  } else {
    void openHistory();
  }
}

async function loadIssues(): Promise<void> {
  loadError.value = null;
  if (!session.project) {
    issues.value = [];
    return;
  }
  loading.value = true;
  try {
    const data = await api.listIssues(session.project.project_id);
    issues.value = data.issues ?? [];
    session.issues = issues.value;
  } catch (error) {
    issues.value = [];
    loadError.value = describeError(error);
  } finally {
    loading.value = false;
  }
}

async function updateStatus(issue: IssueSummary, status: string): Promise<void> {
  if (!session.project || status === issue.status) {
    return;
  }
  saving.value = issue.issue_id;
  actionError.value = "";
  notice.value = "";
  try {
    await api.updateIssue(session.project.project_id, issue.issue_id, { status });
    issue.status = status as IssueStatus;
    notice.value = `状态已更新为“${ISSUE_STATUS_LABEL[issue.status]}”`;
  } catch (error) {
    const friendly = describeError(error);
    actionError.value = `${friendly.title}。${friendly.next}`;
  } finally {
    saving.value = "";
  }
}

async function closeIssue(issue: IssueSummary): Promise<void> {
  if (!session.project) {
    return;
  }
  if (!run.value || run.value.status === "running") {
    actionError.value = "关闭问题需要一次已完成的运行证据。下一步：先完成一次回归运行，再回到这里关闭。";
    return;
  }
  closing.value = issue.issue_id;
  actionError.value = "";
  notice.value = "";
  try {
    await api.closeIssue(session.project.project_id, issue.issue_id, run.value.run_id);
    issue.status = "closed";
    notice.value = "问题已关闭，关闭依据是当前运行的证据。";
  } catch (error) {
    const friendly = describeError(error);
    actionError.value = `${friendly.title}。${friendly.next}`;
  } finally {
    closing.value = "";
  }
}
</script>

<template>
  <div class="stack" data-testid="issues-view">
    <div class="head">
      <div class="head-left">
        <button type="button" class="back" @click="returnToResult()">
          <Icon name="arrow-left" :size="15" />
          返回结果
        </button>
        <button type="button" class="back" @click="openHistory">历史记录</button>
      </div>
      <AppButton size="sm" variant="ghost" :loading="loading" @click="loadIssues">
        <Icon name="refresh" :size="14" />
        刷新
      </AppButton>
    </div>

    <template v-if="!session.project">
      <EmptyState title="先选择一个项目" icon="folder">
        <template #body>问题按项目登记。回到项目页选择项目后再查看。</template>
        <template #actions>
          <AppButton variant="primary" @click="go('project')">回到项目</AppButton>
        </template>
      </EmptyState>
    </template>

    <template v-else>
      <header class="head-copy">
        <h1>问题处置</h1>
        <p class="muted">
          状态更新只改处置记录，不改变历史结论。关闭问题必须引用一次已完成的运行作为新证据。
        </p>
      </header>

      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <p v-if="actionError" class="action-error" role="alert">{{ actionError }}</p>
      <p v-if="run" class="hint">
        当前证据运行：<span class="mono">{{ run.run_id }}</span>
        （{{ run.status === "running" ? "运行中，不能用于关闭" : formatDateTime(run.started_at) }}）
      </p>
      <p v-else class="hint">还没有打开运行记录；关闭问题前先完成一次回归运行。</p>

      <SkeletonBlock v-if="loading && !issues.length" :lines="4" label="正在读取问题清单" />

      <ErrorNotice
        v-if="loadError"
        :error="loadError"
        retry-label="重新读取"
        :busy="loading"
        @retry="loadIssues"
      />

      <EmptyState v-if="!loading && !issues.length" title="还没有登记问题" icon="info">
        <template #body>
          运行失败时会按失败签名自动生成问题；也可以在运行结果页手动登记。现在没有待处置的问题。
        </template>
        <template #actions>
          <AppButton variant="secondary" @click="returnToResult()">返回结果</AppButton>
        </template>
      </EmptyState>

      <ul v-else class="issue-list">
        <li v-for="issue in issues" :key="issue.issue_id" class="issue-row">
          <div class="issue-main">
            <div class="title-line">
              <span class="title">{{ issue.title }}</span>
              <Badge
                :tone="issue.status === 'closed' ? 'neutral' : issue.status === 'retest' ? 'incomplete' : 'accent'"
              >
                {{ ISSUE_STATUS_LABEL[issue.status] }}
              </Badge>
              <Badge tone="neutral">{{ SEVERITY_LABEL[issue.severity] }}</Badge>
            </div>
            <span class="meta hint">
              <span class="mono">{{ issue.issue_id }}</span>
              · 出现 {{ issue.occurrences }} 次
            </span>
          </div>

          <div class="controls">
            <label class="status-label">
              <span class="sr-only">更新状态</span>
              <select
                :value="issue.status === 'closed' ? 'closed' : issue.status"
                :disabled="issue.status === 'closed' || saving === issue.issue_id"
                @change="updateStatus(issue, ($event.target as HTMLSelectElement).value)"
              >
                <option
                  v-if="issue.status === 'closed'"
                  value="closed"
                  disabled
                >
                  {{ ISSUE_STATUS_LABEL.closed }}
                </option>
                <option v-for="status in EDITABLE_STATUSES" :key="status" :value="status">
                  {{ ISSUE_STATUS_LABEL[status] }}
                </option>
              </select>
            </label>
            <AppButton
              size="sm"
              variant="secondary"
              :disabled="!canClose || issue.status === 'closed'"
              :loading="closing === issue.issue_id"
              :title="canClose ? '引用当前运行证据关闭问题' : '先完成一次运行'"
              @click="closeIssue(issue)"
            >
              关闭问题
            </AppButton>
          </div>
        </li>
      </ul>

      <p v-if="!canClose && issues.length" class="hint">
        关闭按钮需要一次已完成的运行作为证据；运行中或没有运行时会保持不可用。
      </p>
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

.head-left {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
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
  line-height: 1.6;
  max-width: 68ch;
}

.notice {
  padding: 8px 10px;
  border: 1px solid rgba(134, 179, 148, 0.3);
  background: var(--passed-fill);
  border-radius: var(--r-control);
  font-size: var(--fs-12);
}

.action-error {
  padding: 8px 10px;
  border: 1px solid rgba(207, 122, 122, 0.35);
  background: var(--failed-fill);
  border-radius: var(--r-control);
  color: var(--text-2);
  font-size: var(--fs-12);
  line-height: 1.6;
}

.issue-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  overflow: hidden;
}

.issue-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}

.issue-row:last-child {
  border-bottom: 0;
}

.issue-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1 1 240px;
}

.title-line {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.title {
  font-size: var(--fs-13);
  font-weight: 500;
  overflow-wrap: anywhere;
}

.meta {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.controls {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.status-label select {
  min-height: 30px;
  padding: 4px 8px;
  background: var(--surface-raised);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-12);
}

.status-label select:disabled {
  color: var(--text-3);
  cursor: not-allowed;
}
</style>
