<script setup lang="ts">
import { ref } from "vue";

import { api } from "@/api/commands";
import { describeError } from "@/api/errors";
import { ISSUE_STATUS_LABEL, SEVERITY_LABEL } from "@/api/labels";
import type { IssueStatus, IssueSummary } from "@/api/types";
import AppButton from "@/components/AppButton.vue";
import Badge from "@/components/Badge.vue";
import { session } from "@/state/session";

const props = defineProps<{
  issues: IssueSummary[];
  runId: string;
  canClose: boolean;
}>();

const EDITABLE_STATUSES: IssueStatus[] = ["pending", "in_progress", "retest"];

const saving = ref("");
const closing = ref("");
const notice = ref("");
const actionError = ref("");

function statusTone(status: IssueStatus): "neutral" | "incomplete" | "accent" {
  if (status === "closed") {
    return "neutral";
  }
  return status === "retest" ? "incomplete" : "accent";
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
  if (!props.canClose || !props.runId) {
    actionError.value = "关闭问题需要一次已完成的运行证据。下一步：先完成一次回归运行。";
    return;
  }
  closing.value = issue.issue_id;
  actionError.value = "";
  notice.value = "";
  try {
    await api.closeIssue(session.project.project_id, issue.issue_id, props.runId);
    issue.status = "closed";
    notice.value = "问题已关闭，关闭依据是本次运行的证据。";
  } catch (error) {
    const friendly = describeError(error);
    actionError.value = `${friendly.title}。${friendly.next}`;
  } finally {
    closing.value = "";
  }
}
</script>

<template>
  <div class="issue-block" data-testid="issue-inline">
    <p v-if="notice" class="notice-line" role="status">{{ notice }}</p>
    <p v-if="actionError" class="error-line" role="alert">{{ actionError }}</p>

    <ul v-if="issues.length" class="issue-list">
      <li v-for="issue in issues" :key="issue.issue_id" class="issue-row">
        <div class="issue-main">
          <span class="issue-title">{{ issue.title }}</span>
          <span class="issue-meta">
            <Badge :tone="statusTone(issue.status)">{{ ISSUE_STATUS_LABEL[issue.status] }}</Badge>
            <Badge tone="neutral">{{ SEVERITY_LABEL[issue.severity] }}</Badge>
            <span class="quiet numeric">出现 {{ issue.occurrences }} 次</span>
          </span>
        </div>
        <div class="issue-controls">
          <label class="status-label">
            <span class="sr-only">更新「{{ issue.title }}」的状态</span>
            <select
              :value="issue.status === 'closed' ? 'closed' : issue.status"
              :disabled="issue.status === 'closed' || saving === issue.issue_id"
              @change="updateStatus(issue, ($event.target as HTMLSelectElement).value)"
            >
              <option v-if="issue.status === 'closed'" value="closed" disabled>
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
            :title="canClose ? '引用本次运行证据关闭问题' : '先完成一次运行'"
            @click="closeIssue(issue)"
          >
            关闭问题
          </AppButton>
        </div>
      </li>
    </ul>

    <p v-else class="empty-line">
      还没有登记问题。运行失败时会按失败签名自动生成，跨运行累积。
    </p>

    <p v-if="issues.length && !canClose" class="hint">
      关闭按钮需要一次已完成的运行作为证据；运行中或没有运行时会保持不可用。
    </p>
  </div>
</template>

<style scoped>
.issue-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.notice-line,
.error-line {
  padding: 7px 10px;
  border-radius: var(--r-control);
  font-size: var(--fs-12);
  line-height: 1.55;
}

.notice-line {
  background: var(--passed-fill);
  border: 1px solid rgba(134, 179, 148, 0.3);
}

.error-line {
  background: var(--failed-fill);
  border: 1px solid rgba(214, 141, 132, 0.3);
  color: var(--text-2);
}

.issue-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 0;
  border-top: 1px solid var(--line);
  border-radius: 0;
  background: none;
}

.issue-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 10px var(--space-4);
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

.issue-title {
  font-size: var(--fs-13);
  font-weight: 500;
  overflow-wrap: anywhere;
}

.issue-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  font-size: var(--fs-12);
}

.issue-id {
  max-width: 16ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.issue-controls {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.status-label select {
  min-height: 28px;
  padding: 3px 8px;
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

.empty-line {
  padding: var(--space-4);
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text-3);
  font-size: var(--fs-12);
}

@media (max-width: 560px) {
  .issue-row {
    padding: var(--space-3);
  }
}
</style>
