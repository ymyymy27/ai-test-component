<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { api } from "@/api/commands";
import { describeError, type FriendlyError } from "@/api/errors";
import { openHostPath } from "@/api/host";
import type { EvidenceItem } from "@/api/types";
import AppButton from "@/components/AppButton.vue";
import AppDrawer from "@/components/AppDrawer.vue";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import SkeletonBlock from "@/components/SkeletonBlock.vue";
import { closeDrawers, session } from "@/state/session";

const items = ref<EvidenceItem[]>([]);
const loading = ref(false);
const loadError = ref<FriendlyError | null>(null);
const actionNote = ref("");
const copied = ref("");

const run = computed(() => session.result?.run ?? null);

function filename(item: EvidenceItem): string {
  if (item.name) {
    return item.name;
  }
  const parts = item.ref.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || item.ref;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
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

async function load(): Promise<void> {
  actionNote.value = "";
  copied.value = "";
  loadError.value = null;
  if (!session.project || !run.value) {
    items.value = [];
    return;
  }
  loading.value = true;
  try {
    const data = await api.listEvidence(session.project.project_id, run.value.run_id);
    items.value = data.evidence ?? [];
  } catch (error) {
    items.value = [];
    loadError.value = describeError(error);
  } finally {
    loading.value = false;
  }
}

async function openItem(item: EvidenceItem): Promise<void> {
  actionNote.value = "";
  if (!item.exists || !item.path) {
    actionNote.value = "这个证据文件已经缺失。下一步：核对运行目录是否被移动或清理。";
    return;
  }
  try {
    await openHostPath(item.path);
    actionNote.value = `已请求用系统程序打开：${item.ref}`;
  } catch (error) {
    const friendly = describeError(error);
    actionNote.value = `${friendly.title}。${friendly.next}`;
  }
}

async function copyItem(item: EvidenceItem): Promise<void> {
  copied.value = "";
  actionNote.value = "";
  try {
    if (!navigator.clipboard?.writeText) {
      throw new Error("no-clipboard");
    }
    await navigator.clipboard.writeText(item.ref);
    copied.value = item.ref;
  } catch {
    actionNote.value = "编辑器没有提供剪贴板写入权限。下一步：手动选中引用后复制。";
  }
}

watch(
  () => session.evidenceOpen,
  (open) => {
    if (open) {
      void load();
    }
  },
);

watch(
  () => session.activeRunId,
  () => {
    if (session.evidenceOpen) {
      void load();
    }
  },
);
</script>

<template>
  <AppDrawer
    :open="session.evidenceOpen"
    title="证据"
    :note="run ? `运行 ${run.run_id.slice(-6)} 登记的证据文件。` : '打开一次运行后查看证据。'"
    @close="closeDrawers"
  >
    <p v-if="actionNote" class="action-note" role="status">{{ actionNote }}</p>
    <p v-if="copied" class="hint">已复制引用：<span class="mono">{{ copied }}</span></p>

    <div class="drawer-actions">
      <span class="hint">只列出本次运行登记的证据，不推断文件内容。</span>
      <AppButton size="sm" variant="ghost" :loading="loading" @click="load">
        <Icon name="refresh" :size="14" />
        刷新
      </AppButton>
    </div>

    <SkeletonBlock v-if="loading && !items.length" :lines="4" label="正在读取证据清单" />

    <div v-else-if="loadError" class="error-note" role="alert">
      <p>{{ loadError.title }}</p>
      <p class="hint">下一步：{{ loadError.next }}</p>
    </div>

    <p v-else-if="!items.length" class="empty-note">
      这次运行没有证据文件。没有登记证据不等于通过，请结合用例明细核对。
    </p>

    <ul v-else class="evidence-list" data-testid="evidence-list">
      <li v-for="item in items" :key="item.ref" class="evidence-row">
        <div class="name-block">
          <span class="name truncate">{{ filename(item) }}</span>
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

.action-note,
.error-note {
  margin-bottom: var(--space-3);
  padding: 8px 10px;
  border-radius: var(--r-control);
  font-size: var(--fs-12);
  line-height: 1.55;
}

.action-note {
  background: var(--incomplete-fill);
  border: 1px solid var(--incomplete-border);
  color: var(--text-2);
}

.error-note {
  background: var(--failed-fill);
  border: 1px solid var(--failed-border);
  color: var(--text-2);
}

.empty-note {
  padding: var(--space-4) 0;
  color: var(--text-3);
  font-size: var(--fs-12);
  line-height: 1.6;
}

.evidence-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.evidence-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas:
    "name size"
    "name badge"
    "actions actions";
  gap: 4px var(--space-3);
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--line);
  align-items: center;
}

.evidence-row:last-child {
  border-bottom: 0;
}

.name-block {
  grid-area: name;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.name {
  font-size: var(--fs-13);
  font-weight: 500;
}

.ref {
  font-size: var(--fs-12);
  color: var(--text-3);
  overflow-wrap: anywhere;
}

.missing {
  color: var(--incomplete);
  font-size: var(--fs-12);
}

.size {
  grid-area: size;
  color: var(--text-3);
  font-size: var(--fs-12);
  text-align: right;
}

.evidence-row :deep(.badge) {
  grid-area: badge;
  justify-self: end;
}

.evidence-row .row {
  grid-area: actions;
  justify-content: flex-end;
  padding-top: 2px;
}
</style>
