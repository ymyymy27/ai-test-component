<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from "vue";

import AppButton from "@/components/AppButton.vue";
import AdjustDrawer from "@/components/AdjustDrawer.vue";
import EmptyState from "@/components/EmptyState.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import EvidenceDrawer from "@/components/EvidenceDrawer.vue";
import HistoryDrawer from "@/components/HistoryDrawer.vue";
import Icon from "@/components/Icon.vue";
import ProjectDrawer from "@/components/ProjectDrawer.vue";
import ProposalPanel from "@/components/ProposalPanel.vue";
import ResultPanel from "@/components/ResultPanel.vue";
import RunPanel from "@/components/RunPanel.vue";
import TargetMenu from "@/components/TargetMenu.vue";
import {
  addProjectByPath,
  dismissBootError,
  isBusy,
  openAdjustDrawer,
  openHistoryDrawer,
  openProjectDrawer,
  refreshRun,
  session,
} from "@/state/session";

const picking = ref(false);
const pathValue = ref("");
const touched = ref(false);
const pathInput = ref<HTMLInputElement | null>(null);
const targetsOpen = ref(false);

let timer: number | undefined;

const proposal = computed(() => session.proposal);

const orderTitle = computed(() => {
  if (session.phase === "proposal") {
    if (session.proposal.goal) {
      return session.proposal.goal;
    }
    return session.proposal.suggestLoading ? "正在准备测试目标" : "这张测试单还没有目标";
  }
  return session.task?.title || session.proposal.goal || "测试结果";
});

const orderNote = computed(() => {
  if (session.phase !== "proposal") {
    return "";
  }
  return session.proposal.basis
    ? `依据：${session.proposal.basis}`
    : session.proposal.suggestLoading
      ? "正在从近期改动、未关闭问题与历史任务里挑一个目标"
      : "";
});

function openPicker(): void {
  picking.value = true;
  touched.value = false;
  if (!pathValue.value && session.prefillRoot) {
    pathValue.value = session.prefillRoot;
  }
  void nextTick(() => pathInput.value?.focus());
}

async function submitProject(): Promise<void> {
  touched.value = true;
  const root = pathValue.value.trim();
  if (!root) {
    pathInput.value?.focus();
    return;
  }
  const created = await addProjectByPath(root);
  if (created) {
    picking.value = false;
    pathValue.value = "";
  }
}

function stopPolling(): void {
  if (timer !== undefined) {
    window.clearTimeout(timer);
    timer = undefined;
  }
}

function schedulePolling(): void {
  stopPolling();
  if (session.phase !== "running" || !session.ready) {
    return;
  }
  timer = window.setTimeout(async () => {
    await refreshRun();
    schedulePolling();
  }, 2000);
}

watch(() => session.phase, schedulePolling, { immediate: true });

onUnmounted(stopPolling);
</script>

<template>
  <div class="session">
    <section v-if="session.phase === 'no-project'" class="welcome" data-testid="no-project">
      <EmptyState title="先选择一个项目文件夹" icon="folder">
        <template #body>
          面板只读取你选择的目录，用它登记项目、提取验收标准并保存测试记录；数据保存在本机用户数据工作空间，不写进被测源码。
        </template>
        <template #actions>
          <AppButton
            variant="primary"
            :disabled="isBusy('projectAdd')"
            data-testid="select-project"
            @click="openPicker"
          >
            <Icon name="folder" :size="14" />
            选择项目文件夹
          </AppButton>
        </template>
      </EmptyState>

      <form v-if="picking" class="card picker" @submit.prevent="submitProject">
        <div class="section-title">
          <h2>登记项目</h2>
          <span class="section-note">面板无法弹出系统目录选择，请粘贴绝对路径</span>
        </div>
        <label class="field">
          <span class="label">项目根目录</span>
          <input
            id="project-root"
            ref="pathInput"
            v-model="pathValue"
            class="input mono"
            type="text"
            placeholder="例如 E:\work\order-service"
            spellcheck="false"
            autocomplete="off"
            :aria-invalid="touched && !pathValue.trim()"
            aria-describedby="project-root-hint"
            @blur="touched = true"
          />
          <span v-if="touched && !pathValue.trim()" class="error-text">
            请填写项目根目录的绝对路径
          </span>
          <span v-else id="project-root-hint" class="hint">
            目录必须存在且可写；同一个目录重复登记会直接切换到已有项目。
          </span>
        </label>
        <div class="row">
          <AppButton html-type="submit" variant="primary" :loading="isBusy('projectAdd')">
            添加此目录
          </AppButton>
          <AppButton variant="ghost" @click="picking = false; pathValue = ''">取消</AppButton>
        </div>
      </form>

      <ErrorNotice
        v-if="session.bootError"
        :error="session.bootError"
        :show-retry="false"
        @retry="() => {}"
      />
    </section>

    <template v-else>
      <div v-if="session.bootError" class="boot-error">
        <ErrorNotice
          :error="session.bootError"
          :show-retry="false"
          @retry="() => {}"
        />
        <button
          type="button"
          class="boot-error-close"
          aria-label="关闭错误提示"
          @click="dismissBootError"
        >
          <Icon name="close" :size="13" />
        </button>
      </div>

      <article class="order" :data-phase="session.phase" data-testid="test-order">
      <header class="order-head">
        <div class="order-head-main">
          <h1>{{ orderTitle }}</h1>
          <p v-if="orderNote" class="order-note">{{ orderNote }}</p>
        </div>
        <div class="order-nav">
          <button type="button" class="nav-btn" @click="openProjectDrawer">
            <Icon name="layers" :size="14" />
            项目详情
          </button>
          <button type="button" class="nav-btn" @click="openHistoryDrawer">
            <Icon name="history" :size="14" />
            历史
          </button>
        </div>
      </header>

      <div v-if="session.phase === 'proposal'" class="order-tools">
        <AppButton variant="secondary" size="sm" data-testid="switch-target" @click="targetsOpen = true">
          换一个目标
          <Icon name="chevron-down" :size="13" />
        </AppButton>
        <AppButton variant="ghost" size="sm" data-testid="open-adjust" @click="openAdjustDrawer">
          调整
        </AppButton>
        <span v-if="proposal.itemsLoading" class="hint">正在更新验收标准…</span>
      </div>

      <ProposalPanel v-if="session.phase === 'proposal'" />
      <RunPanel v-else-if="session.phase === 'running'" />
      <ResultPanel v-else-if="session.phase === 'result'" />
      </article>
    </template>

    <TargetMenu :open="targetsOpen" @close="targetsOpen = false" />
    <HistoryDrawer />
    <AdjustDrawer />
    <ProjectDrawer />
    <EvidenceDrawer />
  </div>
</template>

<style scoped>
.session {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.welcome {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.boot-error {
  position: relative;
}

.boot-error :deep(.notice) {
  padding-right: 38px;
}

.boot-error-close {
  position: absolute;
  top: 7px;
  right: 7px;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  background: none;
  border: 1px solid transparent;
  border-radius: 4px;
  color: var(--text-3);
}

.boot-error-close:hover {
  color: var(--text);
  background: var(--surface-raised);
  border-color: var(--line);
}

.picker {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.label {
  font-size: var(--fs-12);
  color: var(--text-2);
  font-weight: 500;
}

.input {
  width: 100%;
  min-height: 34px;
  padding: 6px 10px;
  background: var(--surface-sunken);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-13);
}

.input::placeholder {
  color: var(--text-disabled);
}

.input:focus {
  border-color: var(--accent-line);
  outline: none;
  box-shadow: 0 0 0 2px rgba(130, 175, 205, 0.18);
}

.order {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
}

.order-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--line);
}

.order-head-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.order-head h1 {
  font-size: var(--fs-20);
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.order-note {
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.55;
  max-width: 62ch;
}

.order-tools {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.order-nav {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex: none;
}

.nav-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 8px;
  background: none;
  border: 1px solid transparent;
  border-radius: var(--r-control);
  color: var(--text-3);
  font-size: var(--fs-12);
}

.nav-btn:hover {
  color: var(--text);
  background: var(--surface-raised);
  border-color: var(--line);
}

@media (max-width: 520px) {
  .order {
    padding: var(--space-3);
  }

  .order-head {
    flex-wrap: wrap;
  }

  .order-nav {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
