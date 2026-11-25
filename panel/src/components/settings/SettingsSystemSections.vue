<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";

import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

withDefaults(
  defineProps<{
    activeSection: string;
    boundaryOpen?: boolean;
  }>(),
  {
    boundaryOpen: false,
  },
);

const emit = defineEmits<{
  toggleBoundary: [open: boolean];
}>();

function onBoundaryToggle(event: Event): void {
  const target = event.target;
  if (target instanceof HTMLDetailsElement) {
    emit("toggleBoundary", target.open);
  }
}

const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();
const backupBusy = ref(false);
const restoreBusy = ref(false);
const clearBusy = ref(false);
const restoreInput = ref<HTMLInputElement | null>(null);

async function createBackup(): Promise<void> {
  backupBusy.value = true;
  try {
    await workspace.backup();
  } catch {
    // store 已提示。
  } finally {
    backupBusy.value = false;
  }
}

function openRestore(): void {
  restoreInput.value?.click();
}

async function onRestoreFile(event: Event): Promise<void> {
  const target = event.target;
  if (!(target instanceof HTMLInputElement) || !target.files?.[0]) {
    return;
  }
  const file = target.files[0];
  const answer = await ui.askConfirm({
    title: "从备份恢复？",
    body: "恢复会替换当前本机保存的项目、任务、问题与报告。不会影响被测仓库。",
    confirmLabel: "恢复备份",
    tone: "danger",
  });
  if (answer === null) {
    target.value = "";
    return;
  }
  restoreBusy.value = true;
  try {
    const data = await file.arrayBuffer();
    await workspace.restoreBackup(data);
    if (workspace.currentProjectId) {
      await router.push(`/projects/${workspace.currentProjectId}/overview`);
    }
  } catch {
    // store 已提示。
  } finally {
    restoreBusy.value = false;
    target.value = "";
  }
}

async function clearData(): Promise<void> {
  const answer = await ui.askConfirm({
    title: "清空本地面板数据？",
    body: "会删除本机保存的项目、任务、问题与报告；这个动作不能撤销，不影响被测仓库。",
    confirmLabel: "清空本地数据",
    tone: "danger",
    requireText: true,
    textLabel: "输入“清空”",
    placeholder: "清空",
  });
  if (answer === null) {
    return;
  }
  if (answer !== "清空") {
    ui.showToast("确认文字不匹配，未执行清空。", "error");
    return;
  }
  clearBusy.value = true;
  try {
    await workspace.clearData();
    await router.push("/setup");
  } finally {
    clearBusy.value = false;
  }
}
</script>

<template>
  <section
    v-show="activeSection === 'settings-appearance'"
    class="settings-section"
    id="settings-appearance"
    role="tabpanel"
    aria-labelledby="settings-tab-settings-appearance"
  >
    <div class="settings-head">
      <div>
        <h2 class="settings-title">外观</h2>
        <p class="settings-desc">明暗共用同一结构，默认跟随系统。</p>
      </div>
    </div>
    <div class="theme-group" role="group" aria-label="外观主题">
      <button
        class="filter-btn"
        :class="{ 'is-active': ui.theme === 'auto' }"
        type="button"
        :aria-pressed="ui.theme === 'auto'"
        @click="ui.setTheme('auto')"
      >
        跟随系统
      </button>
      <button
        class="filter-btn"
        :class="{ 'is-active': ui.theme === 'light' }"
        type="button"
        :aria-pressed="ui.theme === 'light'"
        @click="ui.setTheme('light')"
      >
        浅色
      </button>
      <button
        class="filter-btn"
        :class="{ 'is-active': ui.theme === 'dark' }"
        type="button"
        :aria-pressed="ui.theme === 'dark'"
        @click="ui.setTheme('dark')"
      >
        深色
      </button>
    </div>
  </section>

  <section
    v-show="activeSection === 'settings-backup'"
    class="settings-section"
    id="settings-backup"
    role="tabpanel"
    aria-labelledby="settings-tab-settings-backup"
  >
    <div class="settings-head">
      <div>
        <h2 class="settings-title">备份</h2>
        <p class="settings-desc">
          {{
            workspace.capabilities.browserBackup
              ? "ZIP 备份包含业务记录，不包含证据正文；恢复会替换当前本地数据。"
              : "创建备份会写入宿主工作空间；恢复与清理由 CLI 完成。"
          }}
        </p>
      </div>
    </div>
    <div class="backup-row">
      <button
        class="btn btn-sm"
        type="button"
        :disabled="backupBusy"
        :aria-busy="backupBusy"
        @click="createBackup"
      >
        {{ backupBusy ? "正在创建…" : "创建备份" }}
      </button>
      <template v-if="workspace.capabilities.browserBackup">
        <button
          class="btn btn-sm"
          type="button"
          :disabled="restoreBusy"
          :aria-busy="restoreBusy"
          @click="openRestore"
        >
          {{ restoreBusy ? "正在恢复…" : "从备份恢复" }}
        </button>
        <input
          ref="restoreInput"
          class="visually-hidden"
          type="file"
          accept=".zip,application/zip,application/json,.json"
          @change="onRestoreFile"
        />
      </template>
      <span class="backup-meta">
        {{ workspace.capabilities.browserBackup ? "本地 ZIP" : "宿主工作空间" }}
      </span>
    </div>
    <template v-if="workspace.capabilities.browserBackup">
      <h3 class="settings-subtitle">危险操作</h3>
      <div class="backup-row">
        <button
          class="btn btn-sm btn-danger"
          type="button"
          :disabled="clearBusy"
          :aria-busy="clearBusy"
          @click="clearData"
        >
          {{ clearBusy ? "正在清空…" : "清空本地数据" }}
        </button>
        <span class="backup-meta">删除本机保存的项目、任务、问题与报告；不影响被测仓库。</span>
      </div>
    </template>
  </section>

  <details
    id="settings-boundary"
    class="settings-boundary-card"
    :open="boundaryOpen"
    @toggle="onBoundaryToggle"
  >
    <summary>
      <span>数据边界与安全约束 · 不改动被测仓库</span>
      <small>只读规则说明（可展开）</small>
    </summary>
    <div class="boundary-list">
      <div class="boundary-row">
        <span>项目隔离</span>
        <p>测试、问题、历史、报告只属于当前项目；项目列表只汇总，不混合明细。</p>
      </div>
      <div class="boundary-row">
        <span>本机存储</span>
        <p>
          {{
            workspace.mode === "host"
              ? "项目记录、证据元数据与报告由宿主 CLI 写入本机工作空间，不写进被测仓库。"
              : "项目记录、证据元数据与报告留在浏览器本地存储，不写进被测仓库。"
          }}
        </p>
      </div>
      <div class="boundary-row">
        <span>运行锁</span>
        <p>同一项目同时只运行一个任务；运行中不能切换任务、新建任务或做快速检查。</p>
      </div>
      <div class="boundary-row">
        <span>凭据</span>
        <p>配置拒绝 password/token/secret 等字段；凭据不进入报告或导出。</p>
      </div>
      <div class="boundary-row">
        <span>清理</span>
        <p>面板只提供清空本地数据；按运行清理测试数据仍由 CLI 执行。</p>
      </div>
    </div>
  </details>
</template>
