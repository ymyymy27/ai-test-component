<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import StatusMark from "@/components/StatusMark.vue";
import { worstResult } from "@/domain/derive";
import { statusText } from "@/domain/labels";
import type { CaseResult, ProjectCheck, QuickCheck } from "@/domain/types";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();
const projectId = computed(() => String(route.params.projectId ?? ""));
const project = computed(() => workspace.currentProject);
const confirmed = ref(false);
const starting = ref(false);
const current = computed<QuickCheck | null>(() => workspace.quickCheck);
const latest = computed<QuickCheck | null>(
  () => current.value ?? project.value?.quickChecks[0] ?? null,
);
const running = computed(() => latest.value?.status === "running");
const hasResult = computed(() => latest.value?.status === "completed");
const cancelled = computed(() => latest.value?.status === "cancelled");
const elapsed = ref(0);
const statusRef = ref<HTMLElement | null>(null);
const resultRef = ref<HTMLElement | null>(null);
let timer: ReturnType<typeof setInterval> | null = null;

const previewChecks = computed<ProjectCheck[]>(() => {
  const p = project.value;
  const now = new Date().toISOString();
  return [
    {
      key: "build",
      name: "构建",
      method: p?.config.l1?.build ? `已配置：${p.config.l1.build}` : "未配置，将自动检测",
      result: "not_run",
      detail: "",
      time: now,
    },
    {
      key: "entry",
      name: "启动入口",
      method: p?.config.l1?.entry ? `已配置：${p.config.l1.entry}` : "未配置，将自动检测",
      result: "not_run",
      detail: "",
      time: now,
    },
    {
      key: "smoke",
      name: "最小测试",
      method: p?.config.l1?.smokeCommand
        ? `已配置：${p.config.l1.smokeCommand}`
        : "未配置，将运行已有最小测试",
      result: "not_run",
      detail: "",
      time: now,
    },
  ];
});
const checks = computed(() => latest.value?.checks ?? previewChecks.value);
const quickVerdict = computed<CaseResult>(() => {
  if (!checks.value.length) {
    return "not_run";
  }
  return worstResult(checks.value.map((check) => check.result));
});
const historyChecks = computed(() => project.value?.quickChecks.slice(1, 4) ?? []);
const missingEnvironment = computed(
  () => workspace.environment.filter((check) => !check.ok).length,
);

watch(
  running,
  (value) => {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    if (value) {
      elapsed.value = 0;
      timer = setInterval(() => {
        elapsed.value += 1;
      }, 1000);
      void nextTick(() => statusRef.value?.focus());
    } else {
      void nextTick(() => resultRef.value?.focus());
    }
  },
);

onBeforeUnmount(() => {
  if (timer) {
    clearInterval(timer);
  }
});

async function start(): Promise<void> {
  if (!confirmed.value || starting.value || running.value) {
    return;
  }
  starting.value = true;
  try {
    await workspace.startQuickCheck(projectId.value);
    confirmed.value = false;
    ui.showToast("快速检查已开始；结果单独记录。", "info");
  } catch {
    // store 已提示。
  } finally {
    starting.value = false;
  }
}
</script>

<template>
  <div v-if="!project" class="page">
    <ErrorState
      title="项目不可用"
      text="回到项目概览重新选择项目。"
      retry-label="回到概览"
      @retry="router.push(`/projects/${projectId}/overview`)"
    />
  </div>
  <div v-else class="page">
    <PageHeader
      :title="'快速检查'"
      :subtitle="`${project.name} · 项目级检查 · 不进入验收项覆盖`"
    />

    <div
      v-if="running"
      ref="statusRef"
      class="analysis-strip"
      role="status"
      aria-live="polite"
      aria-busy="true"
      tabindex="-1"
    >
      <span class="spinner" aria-hidden="true"></span>
      <span class="candidate-brief-main">
        <span class="analysis-strip-title">正在执行快速检查</span>
        <span class="analysis-strip-text">
          已运行 {{ elapsed }} 秒；只检查构建、启动入口和最小测试。
        </span>
      </span>
    </div>

    <section ref="resultRef" class="surface-strip quick-result" tabindex="-1">
      <div class="row-between">
        <div>
          <p class="page-kicker">{{ running ? "正在执行" : hasResult ? "L1 结果" : "检查项" }}</p>
          <p v-if="hasResult" class="quick-verdict-result" :class="`is-${quickVerdict}`">
            {{ statusText(quickVerdict) }}
          </p>
          <p class="page-subtitle">
            {{
              running
                ? "检查完成前不会写入结果。"
                : cancelled
                  ? "本次快速检查已取消，未形成结论。"
                  : hasResult
                    ? "结果单独保留，不进入验收项覆盖。"
                    : "确认后开始。"
            }}
          </p>
        </div>
        <span v-if="latest?.simulated" class="badge is-neutral">本地模拟</span>
      </div>
      <div class="check-list">
        <div v-for="check in checks" :key="check.key" class="check-row">
          <StatusMark :result="check.result === 'not_run' && running ? 'not_run' : check.result" />
          <span class="check-name">{{ check.name }}</span>
          <span class="check-method">{{ check.method }}</span>
          <span class="status-text quick-status" :class="`is-${check.result}`">
            {{
              check.result === "not_run" ? (running ? "等待" : "未运行") : statusText(check.result)
            }}
            <template v-if="check.detail"> · {{ check.detail }}</template>
          </span>
        </div>
      </div>
      <p v-if="latest?.finishedAt" class="doc-note">
        完成于 {{ new Date(latest.finishedAt).toLocaleString("zh-CN") }} · 由
        {{ latest.confirmedBy }} 确认
      </p>
    </section>

    <details v-if="historyChecks.length" class="quick-history">
      <summary>最近 {{ historyChecks.length }} 次快速检查</summary>
      <ul class="list-plain">
        <li v-for="entry in historyChecks" :key="entry.id">
          {{ new Date(entry.finishedAt || entry.startedAt).toLocaleString("zh-CN") }} ·
          {{
            entry.status === "completed"
              ? statusText(worstResult(entry.checks.map((item) => item.result)))
              : entry.status === "cancelled"
                ? "已取消"
                : "未完成"
          }}
        </li>
      </ul>
    </details>

    <section v-if="!running" class="section">
      <ul class="list-plain quick-commands">
        <li v-for="check in previewChecks" :key="check.key">
          {{ check.name }}：{{ check.method }}
        </li>
      </ul>
      <p class="field-hint">
        环境：{{ missingEnvironment ? `缺失 ${missingEnvironment} 项，只影响对应能力` : "已检查" }} ·
        确认人：本机用户
      </p>
      <label class="checkline" for="quick-confirm">
        <input id="quick-confirm" v-model="confirmed" type="checkbox" />
        <span>我已确认执行以上项目级检查。</span>
      </label>
      <div class="form-actions">
        <button
          class="btn"
          :class="{ 'btn-primary': !hasResult }"
          type="button"
          :disabled="!confirmed || starting"
          :aria-busy="starting"
          @click="start"
        >
          {{ starting ? "正在启动…" : hasResult ? "再检查一次" : "开始快速检查" }}
        </button>
        <button
          class="btn btn-ghost"
          type="button"
          @click="router.push(`/projects/${projectId}/overview`)"
        >
          返回项目
        </button>
        <button
          class="btn"
          :class="{ 'btn-primary': hasResult }"
          type="button"
          @click="router.push(`/projects/${projectId}/tasks/new`)"
        >
          开始正式测试
        </button>
      </div>
    </section>

    <div v-else class="form-actions">
      <button
        class="btn btn-ghost"
        type="button"
        @click="router.push(`/projects/${projectId}/overview`)"
      >
        返回项目
      </button>
    </div>

    <div v-if="hasResult" class="notice" role="status" aria-live="polite">
      <span>快速检查完成；结果只对项目级检查成立，不代表任何验收项通过。</span>
    </div>
  </div>
</template>
