<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api } from "@/api/commands";
import { describeError } from "@/api/errors";
import { openHostPath } from "@/api/host";
import type { EvidenceItem } from "@/api/types";
import { workspace } from "@/state/workspace";

const props = defineProps<{ focusRef?: string }>();
const emit = defineEmits<{ close: [] }>();

const items = ref<EvidenceItem[]>([]);
const loading = ref(false);
const note = ref("");
const addOpen = ref(false);
const addPath = ref("");
const addNote = ref("");
const addBusy = ref(false);
const addError = ref("");

const runId = computed(() => workspace.run?.run.run_id ?? "");

function nameOf(item: EvidenceItem): string {
  return item.name || item.ref.replace(/\\/g, "/").split("/").pop() || item.ref;
}

function bytesOf(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return "0 B";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

async function load(): Promise<void> {
  if (!workspace.project || !runId.value) {
    return;
  }
  loading.value = true;
  note.value = "";
  try {
    const data = await api.listEvidence(workspace.project.project_id, runId.value);
    items.value = data.evidence ?? [];
  } catch (error) {
    const friendly = describeError(error);
    note.value = `${friendly.title}。${friendly.next}`;
  } finally {
    loading.value = false;
  }
}

async function openItem(item: EvidenceItem): Promise<void> {
  if (!item.exists || !item.path) {
    note.value = "这个证据文件已经不在了。检查运行目录是否被移动或清理。";
    return;
  }
  try {
    await openHostPath(item.path);
    note.value = `已请求用系统程序打开：${item.ref}`;
  } catch (error) {
    const friendly = describeError(error);
    note.value = `${friendly.title}。${friendly.next}`;
  }
}

async function submitEvidence(): Promise<void> {
  if (!workspace.project || !runId.value) {
    return;
  }
  const path = addPath.value.trim();
  if (!path) {
    addError.value = "先填写证据文件的完整路径。";
    return;
  }
  addBusy.value = true;
  addError.value = "";
  try {
    await api.addEvidence(workspace.project.project_id, runId.value, path, addNote.value.trim());
    addPath.value = "";
    addNote.value = "";
    addOpen.value = false;
    note.value = "证据已登记到这次运行。";
    await load();
  } catch (error) {
    const friendly = describeError(error);
    addError.value = `${friendly.title}。${friendly.next}`;
  } finally {
    addBusy.value = false;
  }
}

onMounted(() => {
  void load();
});
</script>

<template>
  <div class="ui-sheet" role="dialog" aria-modal="true" aria-labelledby="evidence-title">
    <header class="ui-sheet-head">
      <strong id="evidence-title">证据</strong>
      <button type="button" class="ui-link" @click="emit('close')">关闭</button>
    </header>
    <div class="ui-sheet-body evidence-body">
      <p class="ui-help">
        证据引用和文件是否还在以本地事实为准；缺失或截断会直接标出。
      </p>
      <p v-if="note" class="ui-help note" role="status">{{ note }}</p>
      <p v-if="loading" class="ui-help">正在读取证据…</p>
      <p v-else-if="items.length === 0" class="ui-help">这次运行还没有登记证据。</p>
      <ul v-else class="list">
        <li
          v-for="item in items"
          :key="item.ref"
          class="row"
          :class="{ focus: item.ref === props.focusRef, missing: !item.exists }"
        >
          <button type="button" class="name" @click="openItem(item)">
            {{ nameOf(item) }}
          </button>
          <span class="meta">
            {{ bytesOf(item.bytes) }}
            <template v-if="item.truncated"> · 已截断</template>
            <template v-if="item.media_type"> · {{ item.media_type }}</template>
            <template v-if="!item.exists"> · 文件已缺失</template>
          </span>
          <span v-if="item.sha256" class="sha">{{ item.sha256.slice(0, 12) }}</span>
        </li>
      </ul>

      <div class="add">
        <button v-if="!addOpen" type="button" class="ui-link" @click="addOpen = true">
          补充证据
        </button>
        <template v-else>
          <label class="field">
            <span class="ui-label">文件路径</span>
            <input v-model="addPath" class="ui-input" placeholder="例如 E:\logs\payment.log" />
          </label>
          <label class="field">
            <span class="ui-label">说明（可选）</span>
            <input v-model="addNote" class="ui-input" placeholder="这份证据证明了什么" />
          </label>
          <div class="add-actions">
            <button type="button" class="ui-link" @click="addOpen = false">取消</button>
            <button type="button" class="ui-btn" :disabled="addBusy" @click="submitEvidence">
              {{ addBusy ? "登记中…" : "登记证据" }}
            </button>
          </div>
        </template>
        <p v-if="addError" class="ui-error">{{ addError }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.evidence-body {
  padding-top: var(--space-4);
}

.note {
  color: var(--text-2);
}

.list {
  margin: var(--space-3) 0 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid var(--line-strong);
}

.row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line);
}

.row.focus {
  border-left: 2px solid var(--accent);
  padding-left: 8px;
}

.row.missing {
  color: var(--failed);
}

.name {
  padding: 0;
  border: 0;
  background: none;
  color: var(--text);
  font-family: var(--font-mono);
  font-size: var(--fs-11);
  text-align: left;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.meta {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  text-align: right;
}

.add {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--line);
}

.add .field {
  display: block;
  margin-bottom: 7px;
}

.add-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.sha {
  grid-column: 1 / -1;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-9, 9px);
}
</style>
