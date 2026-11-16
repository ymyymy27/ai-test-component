<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api } from "@/api/commands";
import { describeError, type FriendlyError } from "@/api/errors";
import { openHostPath } from "@/api/host";
import type { EvidenceItem } from "@/api/types";
import AppButton from "@/components/AppButton.vue";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Icon from "@/components/Icon.vue";
import SkeletonBlock from "@/components/SkeletonBlock.vue";
import { go, openHistory, openResult, session } from "@/state/session";

const items = ref<EvidenceItem[]>([]);
const loading = ref(false);
const loadError = ref<FriendlyError | null>(null);
const actionError = ref("");
const opened = ref("");
const copied = ref("");

const run = computed(() => session.result?.run ?? null);
const runTitle = computed(() =>
  run.value ? `运行 ${run.value.run_id.slice(-6)} 的证据` : "证据",
);

onMounted(() => {
  void loadEvidence();
});

async function ensureRun(): Promise<boolean> {
  if (!session.project) {
    return false;
  }
  if (session.result?.run) {
    return true;
  }
  if (!session.activeRunId) {
    return false;
  }
  await openResult(session.activeRunId, { keepView: true });
  return Boolean(session.result?.run);
}

async function loadEvidence(): Promise<void> {
  loadError.value = null;
  actionError.value = "";
  if (!session.project) {
    items.value = [];
    return;
  }
  if (!(await ensureRun())) {
    items.value = [];
    return;
  }
  const current = session.result?.run;
  if (!current) {
    items.value = [];
    return;
  }
  loading.value = true;
  try {
    const data = await api.listEvidence(session.project.project_id, current.run_id);
    items.value = data.evidence ?? [];
  } catch (error) {
    items.value = [];
    loadError.value = describeError(error);
  } finally {
    loading.value = false;
  }
}

function returnToResult(): void {
  const runId = session.activeRunId || session.result?.run.run_id || "";
  if (runId) {
    void openResult(runId);
  } else {
    void openHistory();
  }
}

function formatBytes(bytes: number): string {  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function filename(item: EvidenceItem): string {
  if (item.name) {
    return item.name;
  }
  const parts = item.ref.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || item.ref;
}

async function openItem(item: EvidenceItem): Promise<void> {
  actionError.value = "";
  opened.value = "";
  if (!item.exists || !item.path) {
    actionError.value = "这个证据文件已经缺失。下一步：核对运行目录是否被移动或清理。";
    return;
  }
  try {
    await openHostPath(item.path);
    opened.value = item.ref;
  } catch (error) {
    actionError.value = describeError(error).title + "。" + describeError(error).next;
  }
}

async function copyItem(item: EvidenceItem): Promise<void> {
  actionError.value = "";
  copied.value = "";
  try {
    if (!navigator.clipboard?.writeText) {
      throw new Error("no-clipboard");
    }
    await navigator.clipboard.writeText(item.ref);
    copied.value = item.ref;
  } catch {
    actionError.value = "编辑器没有提供剪贴板写入权限。下一步：手动选中引用后复制。";
  }
}
</script>

<template>
  <div class="stack" data-testid="evidence-view">
    <div class="head">
      <div class="head-left">
        <button type="button" class="back" @click="returnToResult()">
          <Icon name="arrow-left" :size="15" />
          返回结果
        </button>
        <button type="button" class="back" @click="openHistory">历史记录</button>
      </div>
      <AppButton size="sm" variant="ghost" :loading="loading" @click="loadEvidence">
        <Icon name="refresh" :size="14" />
        刷新
      </AppButton>
    </div>

    <template v-if="!session.project">
      <EmptyState title="先选择一个项目" icon="folder">
        <template #body>
          证据属于具体项目的某一次运行。回到项目页选择项目后，再从运行结果进入证据页。
        </template>
        <template #actions>
          <AppButton variant="primary" @click="go('project')">回到项目</AppButton>
        </template>
      </EmptyState>
    </template>

    <template v-else-if="!run && !loading">
      <EmptyState title="还没有运行记录" icon="history">
        <template #body>
          证据按运行保存。先完成一次运行，结果页会列出本次运行用到的证据文件。
        </template>
        <template #actions>
          <AppButton variant="secondary" @click="openHistory">查看历史</AppButton>
        </template>
      </EmptyState>
    </template>

    <template v-else>
      <header class="head-copy">
        <h1>证据</h1>
        <p class="muted">{{ runTitle }}。只列出本次运行登记的证据，不推断文件内容。</p>
      </header>

      <SkeletonBlock v-if="loading && !items.length" :lines="4" label="正在读取证据清单" />

      <ErrorNotice
        v-if="loadError"
        :error="loadError"
        retry-label="重新读取"
        :busy="loading"
        @retry="loadEvidence"
      />

      <p v-if="actionError" class="action-error" role="alert">{{ actionError }}</p>
      <p v-if="copied" class="hint">已复制引用：<span class="mono">{{ copied }}</span></p>
      <p v-if="opened" class="hint">已请求用系统程序打开：<span class="mono">{{ opened }}</span></p>

      <EmptyState v-if="!loading && !items.length" title="这次运行没有证据文件" icon="file">
        <template #body>
          没有登记证据不等于通过。查看结果页的用例明细，确认是否缺少应有的证据。
        </template>
        <template #actions>
          <AppButton variant="secondary" @click="returnToResult()">返回结果</AppButton>
        </template>
      </EmptyState>

      <ul v-else class="evidence-list">
        <li v-for="item in items" :key="item.ref" class="evidence-row">
          <div class="name-block">
            <span class="name">{{ filename(item) }}</span>
            <span class="ref mono">{{ item.ref }}</span>
            <span v-if="!item.exists" class="missing">
              {{ item.missing_reason || "文件缺失" }}
            </span>
          </div>
          <span class="size numeric">{{ formatBytes(item.bytes) }}</span>
          <Badge :tone="item.exists ? 'passed' : 'failed'">
            {{ item.exists ? "存在" : "缺失" }}
          </Badge>
          <div class="row">
            <AppButton size="sm" variant="ghost" :disabled="!item.exists" @click="openItem(item)">
              打开
            </AppButton>
            <AppButton size="sm" variant="ghost" @click="copyItem(item)">复制引用</AppButton>
          </div>
        </li>
      </ul>
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

.action-error {
  padding: 8px 10px;
  border: 1px solid rgba(207, 122, 122, 0.35);
  background: var(--failed-fill);
  border-radius: var(--r-control);
  color: var(--text-2);
  font-size: var(--fs-12);
  line-height: 1.6;
}

.evidence-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  overflow: hidden;
}

.evidence-row {
  display: grid;
  grid-template-columns: 1fr auto auto auto;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--line);
}

.evidence-row:last-child {
  border-bottom: 0;
}

.name-block {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.name {
  font-size: var(--fs-13);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ref {
  font-size: var(--fs-12);
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.missing {
  color: var(--incomplete);
  font-size: var(--fs-12);
}

.size {
  color: var(--text-3);
  font-size: var(--fs-12);
  white-space: nowrap;
}

@media (max-width: 560px) {
  .evidence-row {
    grid-template-columns: 1fr auto;
  }

  .evidence-row .row {
    grid-column: 1 / -1;
  }
}
</style>
