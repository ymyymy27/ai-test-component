<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";

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
  openHistoryDrawer,
  openProjectDrawer,
  refreshRun,
  session,
  startTest,
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
    return session.proposal.suggestLoading ? "正在准备测试目标" : "还没有测试目标";
  }
  return session.task?.title || session.proposal.goal || "测试结果";
});

function openPicker(): void {
  picking.value = true;
  touched.value = false;
  if (!pathValue.value && session.prefillRoot) {
    pathValue.value = session.prefillRoot;
  }
  void nextTick(() => pathInput.value?.focus());
}

async function useCurrentFolder(): Promise<void> {
  if (session.prefillRoot) {
    await addProjectByPath(session.prefillRoot);
  }
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
  if (!session.ready) {
    return;
  }
  const running = session.phase === "running";
  if (!running && !session.activeRunId) {
    return;
  }
  timer = window.setTimeout(async () => {
    await refreshRun();
    schedulePolling();
  }, running ? 2000 : 10000);
}

watch(() => session.phase, schedulePolling, { immediate: true });

// 相位或回放运行切换时回到测试单顶部，避免落在上一次运行的滚动位置。
watch([() => session.phase, () => session.activeRunId], async () => {
  await nextTick();
  const toTop = (): void => {
    try {
      window.scrollTo({ top: 0, behavior: "auto" });
    } catch {
      // jsdom 不实现滚动；浏览器中才有效。
    }
  };
  toTop();
  window.requestAnimationFrame(toTop);
  window.setTimeout(toTop, 80);
});

onUnmounted(stopPolling);

function onKeydown(event: KeyboardEvent): void {
  if (!(event.ctrlKey || event.metaKey) || event.key !== "Enter") {
    return;
  }
  if (session.phase !== "proposal" || !session.ready) {
    return;
  }
  const target = event.target as HTMLElement | null;
  if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
    return;
  }
  event.preventDefault();
  void startTest();
}

onMounted(() => window.addEventListener("keydown", onKeydown));
onUnmounted(() => window.removeEventListener("keydown", onKeydown));
</script>

<template>
  <div class="session">
    <section v-if="session.phase === 'no-project'" class="welcome" data-testid="no-project">
      <EmptyState title="先选一个要测的项目文件夹" icon="folder">
        <template #body>
          选择项目根目录。测试只读取代码，记录保存在本机。
        </template>
        <template #actions>
          <AppButton
            v-if="session.prefillRoot"
            variant="primary"
            :disabled="isBusy('projectAdd')"
            data-testid="use-current-folder"
            @click="useCurrentFolder"
          >
            <Icon name="folder" :size="14" />
            使用当前文件夹
          </AppButton>
          <AppButton
            :variant="session.prefillRoot ? 'secondary' : 'primary'"
            :disabled="isBusy('projectAdd')"
            data-testid="select-project"
            @click="openPicker"
          >
            选择其他文件夹
          </AppButton>
          <p v-if="session.prefillRoot" class="current-path mono">{{ session.prefillRoot }}</p>
        </template>
      </EmptyState>

      <form v-if="picking" class="card picker" @submit.prevent="submitProject">
        <div class="section-title">
          <h2>粘贴项目路径</h2>
          <span class="section-note">在资源管理器的地址栏复制完整路径</span>
        </div>
        <label class="field">
          <span class="label">项目文件夹路径</span>
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
            路径要存在且可写；同一个目录再加一次会直接切到已有项目。
          </span>
        </label>
        <div class="row">
          <AppButton html-type="submit" variant="primary" :loading="isBusy('projectAdd')">
            用这个目录
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
          <div class="title-row">
            <h1>{{ orderTitle }}</h1>
            <button
              v-if="session.phase === 'proposal'"
              type="button"
              class="target-btn"
              aria-label="换一个目标"
              title="换一个目标"
              data-testid="switch-target"
              @click="targetsOpen = true"
            >
              <Icon name="chevron-down" :size="14" />
            </button>
          </div>
        </div>
        <div class="order-nav">
          <button type="button" class="nav-btn" @click="openProjectDrawer">
            <Icon name="layers" :size="14" />
            项目
          </button>
          <button type="button" class="nav-btn" @click="openHistoryDrawer">
            <Icon name="history" :size="14" />
            历史
          </button>
        </div>
      </header>

      <p v-if="session.phase === 'proposal' && proposal.itemsLoading" class="hint">
        正在更新验收项…
      </p>

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
  width: 100%;
  max-width: var(--layout-max);
  margin: 0 auto;
  padding: var(--space-4) var(--space-4) var(--space-6);
}

@media (max-width: 520px) {
  .session {
    padding: var(--space-4) var(--space-3);
  }
}

.current-path {
  flex-basis: 100%;
  margin-top: 2px;
  font-size: var(--fs-12);
  color: var(--text-3);
  overflow-wrap: anywhere;
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
  box-shadow: 0 0 0 2px var(--accent-glow);
}

.order {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: 0;
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

.title-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.target-btn {
  flex: none;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  background: none;
  border: 1px solid transparent;
  border-radius: var(--r-control);
  color: var(--text-3);
}

.target-btn:hover {
  background: var(--surface-raised);
  border-color: var(--line);
  color: var(--text);
}

.order-head h1 {
  font-size: var(--fs-16);
  line-height: 1.4;
  font-weight: 600;
  overflow-wrap: anywhere;
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
    padding: var(--space-4);
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
