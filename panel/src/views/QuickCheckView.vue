<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { statusText } from "@/domain/labels";
import StatusMark from "@/components/StatusMark.vue";
import type { ProjectCheck, QuickCheck } from "@/domain/types";
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
const previewChecks = computed<ProjectCheck[]>(() => {
  const p = project.value;
  const now = new Date().toISOString();
  return [
    {
      key: "build",
      name: "构建",
      method: p?.config.l1?.build || "自动检测构建命令",
      result: "not_run",
      detail: "等待检查",
      time: now,
    },
    {
      key: "entry",
      name: "启动入口",
      method: p?.config.l1?.entry || "自动检测启动入口",
      result: "not_run",
      detail: "等待检查",
      time: now,
    },
    {
      key: "smoke",
      name: "最小测试",
      method: p?.config.l1?.smokeCommand || "运行已有最小测试",
      result: "not_run",
      detail: "等待检查",
      time: now,
    },
  ];
});
const checks = computed(() => latest.value?.checks ?? previewChecks.value);

async function start(): Promise<void> {
  if (!confirmed.value || starting.value || running.value) {
    return;
  }
  starting.value = true;
  try {
    await workspace.startQuickCheck(projectId.value);
    confirmed.value = false;
    ui.showToast("快速检查已开始；不进入验收项覆盖。", "info");
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

    <div v-if="running" class="analysis-strip" role="status" aria-live="polite" aria-busy="true">
      <span class="spinner" aria-hidden="true"></span>
      <span class="candidate-brief-main">
        <span class="analysis-strip-title">正在执行快速检查</span>
        <span class="analysis-strip-text"
          >只检查构建、启动入口和最小测试；不会创建任务或验收项。</span
        >
      </span>
    </div>

    <section class="surface-strip quick-result">
      <div class="row-between">
        <div>
          <p class="page-kicker">{{ running ? "正在执行" : hasResult ? "L1 结果" : "检查项" }}</p>
          <p class="page-subtitle">
            {{
              running
                ? "检查完成前不会写入结果。"
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
          <span class="status-text" :class="`is-${check.result}`">
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

    <p class="doc-note">
      快速检查只验证构建、启动入口和最小测试，不选择业务功能，不生成验收项，也不把结果算作验收通过。
      结果单独保存在项目记录里，不进入历史运行列表。
    </p>

    <section v-if="!running" class="section">
      <label class="checkline" for="quick-confirm">
        <input id="quick-confirm" v-model="confirmed" type="checkbox" />
        <span>我已确认执行构建、启动入口和最小测试；快速检查仍是一次正式运行。</span>
      </label>
      <div class="form-actions">
        <button
          class="btn btn-primary"
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
        <button class="btn" type="button" @click="router.push(`/projects/${projectId}/tasks/new`)">
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

    <div v-if="hasResult" class="notice">
      <span>快速检查完成；结果只对项目级检查成立，不代表任何验收项通过。</span>
    </div>
  </div>
</template>
