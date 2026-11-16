<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";

import {
  RUN_STATUS_BADGE,
  RUN_STATUS_LABEL,
  VERDICT_LABEL,
  VERDICT_TONE,
  formatDateTime,
} from "@/api/labels";
import AppButton from "@/components/AppButton.vue";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Icon from "@/components/Icon.vue";
import CoverageMeter from "@/components/CoverageMeter.vue";
import {
  addProjectByPath,
  go,
  isBusy,
  openHistory,
  openResult,
  session,
  startWizard,
} from "@/state/session";

const picking = ref(false);
const pathValue = ref("");
const nameValue = ref("");
const touched = ref(false);
const pathInput = ref<HTMLInputElement | null>(null);

const busy = computed(() => isBusy("projectAdd") || isBusy("project"));
const trimmedPath = computed(() => pathValue.value.trim());
const pathError = computed(() =>
  touched.value && !trimmedPath.value ? "请填写项目根目录的绝对路径" : "",
);

const lastRun = computed(() => session.lastRun);

onMounted(() => {
  // 编辑器只有一个工作区目录时会带入路径，用户仍然可以修改。
  if (!pathValue.value && session.prefillRoot) {
    pathValue.value = session.prefillRoot;
  }
});

watch(
  () => session.prefillRoot,
  (value) => {
    if (!pathValue.value && value) {
      pathValue.value = value;
    }
  },
);

function openPicker(): void {
  picking.value = true;
  touched.value = false;
  if (!pathValue.value && session.prefillRoot) {
    pathValue.value = session.prefillRoot;
  }
  void nextTick(() => pathInput.value?.focus());
}

async function submit(): Promise<void> {
  touched.value = true;
  if (!trimmedPath.value) {
    pathInput.value?.focus();
    return;
  }
  const ok = await addProjectByPath(trimmedPath.value, nameValue.value.trim() || undefined);
  if (ok) {
    picking.value = false;
    pathValue.value = "";
    nameValue.value = "";
  }
}
</script>

<template>
  <div class="stack">
    <ErrorNotice
      v-if="session.bootError"
      :error="session.bootError"
      :show-retry="false"
      @retry="() => {}"
    />

    <form v-if="picking" class="card stack" @submit.prevent="submit">
      <div class="section-title">
        <h2>登记项目</h2>
        <span class="section-note">面板无法直接弹出系统目录选择，请粘贴绝对路径</span>
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
          :aria-invalid="Boolean(pathError)"
          aria-describedby="project-root-hint"
          @blur="touched = true"
        />
        <span v-if="pathError" class="error-text">{{ pathError }}</span>
        <span v-else id="project-root-hint" class="hint">
          目录必须存在且可写；同一个目录重复登记会直接切换到已有项目。
        </span>
      </label>
      <label class="field">
        <span class="label">项目名称（可选）</span>
        <input
          v-model="nameValue"
          class="input"
          type="text"
          placeholder="默认使用目录名"
          autocomplete="off"
        />
      </label>
      <div class="row">
        <AppButton html-type="submit" variant="primary" :loading="isBusy('projectAdd')">
          添加此目录
        </AppButton>
        <AppButton variant="ghost" @click="picking = false">取消</AppButton>
      </div>
    </form>


    <!-- 无项目：只保留选择目录与说明 -->
    <template v-if="!session.project">
      <EmptyState title="先选择一个项目文件夹" icon="folder">
        <template #body>
          面板只读取你选择的目录，用它登记项目、扫描模块并保存测试记录。数据保存在本机的用户数据工作空间，不写进被测源码。
        </template>
        <template #actions>
          <AppButton variant="primary" :disabled="busy" @click="openPicker">
            选择项目文件夹
          </AppButton>
        </template>
      </EmptyState>


      <div class="footnote">
        <Icon name="info" :size="14" />
        <span>需要先设置用户数据工作空间与 Python 解释器，才能登记项目。</span>
        <button type="button" class="link" @click="go('settings')">打开设置</button>
      </div>
    </template>

    <!-- 有项目 -->
    <template v-else>
      <div class="project-head">
        <div class="head-copy">
          <h1>{{ session.project.name }}</h1>
          <p class="root mono" :title="session.project.root">{{ session.project.root }}</p>
        </div>
        <AppButton variant="ghost" size="sm" @click="openPicker">
          <Icon name="plus" :size="14" />
          登记其他目录
        </AppButton>
      </div>

      <div class="scan" role="list" aria-label="项目扫描结果">
        <div class="scan-item" role="listitem">
          <span class="scan-label">模块</span>
          <span class="scan-value numeric">{{ session.project.modules.length }}</span>
          <div v-if="session.project.modules.length" class="chips">
            <span
              v-for="module in session.project.modules"
              :key="module.module_id"
              class="chip"
              :title="module.responsibility || module.status || ''"
            >
              {{ module.name }}
            </span>
          </div>
          <span v-else class="scan-note">还没有登记模块</span>
        </div>
        <div class="scan-item" role="listitem">
          <span class="scan-label">入口</span>
          <span class="scan-value">运行前检查</span>
          <span class="scan-note">由源码可运行性检查确认构建与入口，面板不单独保存入口路径。</span>
        </div>
        <div class="scan-item" role="listitem">
          <span class="scan-label">最近运行</span>
          <span v-if="lastRun" class="scan-value">{{ formatDateTime(lastRun.startedAt) }}</span>
          <span v-else class="scan-value">还没有</span>
          <span v-if="lastRun" class="scan-note">
            {{ RUN_STATUS_LABEL[lastRun.status] }}，覆盖
            <span class="numeric">{{ lastRun.selected }}/{{ lastRun.applicable }}</span>
          </span>
          <span v-else class="scan-note">第一次运行后这里会显示最近一次状态。</span>
        </div>
      </div>

      <section class="last card">
        <div class="section-title">
          <h2>上次结论</h2>
          <span v-if="lastRun" class="section-note">
            {{ formatDateTime(lastRun.startedAt) }}
          </span>
        </div>
        <template v-if="lastRun">
          <div class="last-row">
            <div class="last-verdict">
              <Badge
                v-if="lastRun.outcome"
                :tone="VERDICT_TONE[lastRun.outcome]"
              >
                {{ VERDICT_LABEL[lastRun.outcome] }}
              </Badge>
              <Badge v-else tone="running">结论未生成</Badge>
              <span class="status-prefix">运行</span>
              <Badge :tone="RUN_STATUS_BADGE[lastRun.status]">
                {{ RUN_STATUS_LABEL[lastRun.status] }}
              </Badge>
            </div>
            <CoverageMeter
              :selected="lastRun.selected"
              :applicable="lastRun.applicable"
              size="sm"
            />
            <AppButton size="sm" variant="secondary" @click="openResult(session.history[0].run_id)">
              查看结果
            </AppButton>
          </div>
          <p class="hint">
            结论只对最近一次运行所选的范围成立；未选验收项不计入。
          </p>
        </template>
        <div v-else class="last-empty">
          <p class="muted">这个项目还没有运行记录。</p>
          <p class="hint">从“新建测试”开始：选目标、确认验收标准、选定范围，再执行。</p>
        </div>
      </section>

      <div class="actions">
        <AppButton variant="primary" @click="startWizard">
          <Icon name="play" :size="14" />
          新建测试
        </AppButton>
        <AppButton variant="secondary" @click="openHistory">
          <Icon name="history" :size="14" />
          查看历史
        </AppButton>
      </div>
    </template>
  </div>
</template>

<style scoped>
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

.footnote {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--fs-12);
  color: var(--text-3);
  flex-wrap: wrap;
}

.link {
  background: none;
  border: 0;
  padding: 0;
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 3px;
}

.project-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}

.head-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.root {
  color: var(--text-3);
  font-size: var(--fs-12);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scan {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  background: var(--surface);
  overflow: hidden;
}

.scan-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: var(--space-4);
  min-width: 0;
}

.scan-item + .scan-item {
  border-left: 1px solid var(--line);
}

.scan-label {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.scan-value {
  font-size: var(--fs-16);
  font-weight: 600;
}

.scan-note {
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.5;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.chip {
  font-size: var(--fs-12);
  color: var(--text-2);
  background: var(--surface-raised);
  border: 1px solid var(--line);
  border-radius: var(--r-chip);
  padding: 0 6px;
  white-space: nowrap;
}

.last-row {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: var(--space-4);
}

.last-verdict {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.status-prefix {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.last-empty {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

@media (max-width: 560px) {
  .scan {
    grid-template-columns: 1fr;
  }

  .scan-item + .scan-item {
    border-left: 0;
    border-top: 1px solid var(--line);
  }

  .last-row {
    grid-template-columns: 1fr;
    align-items: start;
    gap: var(--space-3);
  }

  .last-row > :last-child {
    justify-self: start;
  }
}
</style>
