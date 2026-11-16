<script setup lang="ts">
import { ISSUE_STATUS_LABEL, SEVERITY_LABEL } from "@/api/labels";
import type { IssueSummary } from "@/api/types";
import Badge from "./Badge.vue";

defineProps<{ issue: IssueSummary }>();

const statusTone = (status: IssueSummary["status"]) => {
  if (status === "closed") return "neutral";
  if (status === "retest") return "incomplete";
  return "accent";
};
</script>

<template>
  <li class="issue">
    <span class="severity">{{ SEVERITY_LABEL[issue.severity] }}</span>
    <span class="title">{{ issue.title }}</span>
    <span class="meta">
      <Badge :tone="statusTone(issue.status)">{{ ISSUE_STATUS_LABEL[issue.status] }}</Badge>
      <span class="count numeric">出现 {{ issue.occurrences }} 次</span>
    </span>
  </li>
</template>

<style scoped>
.issue {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--line);
}

.issue:last-child {
  border-bottom: 0;
}

.severity {
  font-size: var(--fs-12);
  color: var(--text-3);
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  padding: 0 5px;
  white-space: nowrap;
}

.title {
  font-size: var(--fs-13);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.count {
  font-size: var(--fs-12);
  color: var(--text-3);
  white-space: nowrap;
}

@media (max-width: 520px) {
  .issue {
    grid-template-columns: auto 1fr;
    row-gap: 6px;
  }

  .meta {
    grid-column: 2;
    justify-content: flex-start;
  }
}
</style>
