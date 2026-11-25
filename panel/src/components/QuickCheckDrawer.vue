<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { worstResult } from "@/domain/derive";
import { statusText } from "@/domain/labels";
import type { CaseResult, ProjectCheck, QuickCheck } from "@/domain/types";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";
import Icon from "./Icon.vue";
import StatusMark from "./StatusMark.vue";

const ui = useUiStore();
const workspace = useWorkspaceStore();

const confirmed = ref(false);
const starting = ref(false);
const elapsed = ref(0);
let timer: ReturnType<typeof setInterval> | null = null;

const project = computed(() => workspace.currentProject);
const projectId = computed(() => project.value?.id ?? "");
const latest = computed<QuickCheck | null>(
  () => workspace.quickCheck ?? project.value?.quickChecks[0] ?? null,
);
const running = computed(() => latest.value?.status === "running");
const hasResult = computed(() => latest.value?.status === "completed");
const cancelled = computed(() => latest.value?.status === "cancelled");
const executionError = computed(() => latest.value?.status === "execution_error");

const previewChecks = computed<ProjectCheck[]>(() => {
  const p = project.value;
  const now = new Date().toISOString();
  return [
    {
      key: "build",
      name: "构建检查",
      method: p?.config.l1?.build ? `已配置：${p.config.l1.build}` : "未配置，将自动探测",
      result: "not_run",
      detail: "",
      time: now,
    },
    {
      key: "entry",
      name: "启动入口",
      method: p?.config.l1?.entry ? `已配置：${p.config.l1.entry}` : "未配置，将自动探测",
      result: "not_run",
      detail: "",
      time: now,
    },
    {
      key: "smoke",
      name: "最小冒烟",
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

watch(running, (value) => {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  if (value) {
    elapsed.value = 0;
    timer = setInterval(() => {
      elapsed.value += 1;
    }, 1000);
  }
});

onBeforeUnmount(() => {
  if (timer) {
    clearInterval(timer);
  }
});

async function start(): Promise<void> {
  if (!projectId.value || !confirmed.value || starting.value || running.value) {
    return;
  }
  starting.value = true;
  try {
    const result = await workspace.startQuickCheck(projectId.value);
    confirmed.value = false;
    ui.showToast(
      result.status === "running" ? "快速检查已启动；结果将独立保存。" : "快速检查已完成。",
      "info",
    );
  } catch {
    // 错误已在 workspace store 统一提示。
  } finally {
    starting.value = false;
  }
}

function openNewFormalTest(): void {
  ui.closeQuickCheckDrawer();
  ui.openNewTaskDrawer();
}
</script>

<template>
  <div
    v-if="ui.quickCheckDrawerOpen"
    class="drawer-backdrop"
    role="presentation"
    @click.self="ui.closeQuickCheckDrawer()"
  >
    <aside
      class="drawer-panel"
      role="dialog"
      aria-modal="true"
      aria-label="快速检查"
    >
      <div class="drawer-head">
        <div>
          <h2 class="drawer-title">快速检查 (L1)</h2>
          <p class="drawer-sub">
            {{ project ? `${project.name} · 构建、启动与冒烟，不计入正式验收` : "请先选择项目" }}
          </p>
        </div>
        <button
          class="btn btn-ghost btn-sm"
          type="button"
          aria-label="关闭快速检查抽屉"
          @click="ui.closeQuickCheckDrawer()"
        >
          <Icon name="close" :size="15" />
        </button>
      </div>

      <div v-if="project" class="drawer-body">
        <div v-if="workspace.quickCheckError" class="notice is-warn" role="alert">
          <span>快速检查失败：{{ workspace.quickCheckError }}</span>
        </div>

        <div
          v-if="running"
          class="analysis-strip"
          role="status"
          aria-live="polite"
          aria-busy="true"
        >
          <span class="spinner" aria-hidden="true"></span>
          <span class="candidate-brief-main">
            <span class="analysis-strip-title">正在执行快速检查</span>
            <span class="analysis-strip-text">
              已运行 {{ elapsed }} 秒；检查完成前不会修改项目验收记录。
            </span>
          </span>
        </div>

        <section class="surface-strip" aria-live="polite">
          <div class="row-between">
            <div>
              <p class="page-kicker">
                {{ running ? "正在执行" : hasResult ? "L1 结果" : "检查清单" }}
              </p>
              <p v-if="hasResult" class="quick-verdict-result" :class="`is-${quickVerdict}`">
                {{ statusText(quickVerdict) }}
              </p>
              <p class="field-hint">
                {{
                  running
                    ? "完成前不写入结论。"
                    : cancelled
                      ? "上次快速检查已取消。"
                      : executionError
                        ? "上次检查执行出错，请检查配置。"
                        : hasResult
                          ? "本结果独立保存，不影响任何功能验收进度。"
                          : "勾选下方确认框即可在后台开始检查。"
                }}
              </p>
            </div>
            <span v-if="latest?.simulated" class="badge is-neutral">本地模拟</span>
          </div>

          <div class="check-list">
            <div v-for="check in checks" :key="check.key" class="check-row">
              <StatusMark
                :result="check.result === 'not_run' && running ? 'not_run' : check.result"
              />
              <span class="check-name">{{ check.name }}</span>
              <span class="check-method">{{ check.method }}</span>
              <span class="status-text quick-status" :class="`is-${check.result}`">
                {{
                  check.result === "not_run"
                    ? running
                      ? "等待"
                      : "未运行"
                    : statusText(check.result)
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

        <details v-if="historyChecks.length" class="details-tech">
          <summary>查看最近 {{ historyChecks.length }} 次快速检查记录</summary>
          <ul class="list-plain">
            <li v-for="entry in historyChecks" :key="entry.id">
              {{ new Date(entry.finishedAt || entry.startedAt).toLocaleString("zh-CN") }} ·
              {{
                entry.status === "completed"
                  ? statusText(worstResult(entry.checks.map((item) => item.result)))
                  : entry.status === "cancelled"
                    ? "已取消"
                    : entry.status === "execution_error"
                      ? "执行出错"
                      : "未完成"
              }}
            </li>
          </ul>
        </details>

        <div v-if="!running" class="stack-sm">
          <p class="field-hint">
            运行环境：{{
              missingEnvironment ? `有 ${missingEnvironment} 项未接入，只影响对应能力` : "就绪"
            }}
          </p>
          <label class="checkline" for="drawer-quick-confirm">
            <input id="drawer-quick-confirm" v-model="confirmed" type="checkbox" />
            <span>我已知晓并确认执行以上项目级基础检查。</span>
          </label>
        </div>
      </div>

      <div v-if="project" class="drawer-foot">
        <button class="btn btn-ghost btn-sm" type="button" @click="ui.closeQuickCheckDrawer()">
          关闭
        </button>
        <button
          v-if="hasResult"
          class="btn btn-sm"
          type="button"
          @click="openNewFormalTest"
        >
          去发起正式测试
        </button>
        <button
          v-if="!running"
          class="btn btn-primary btn-sm"
          type="button"
          :disabled="!confirmed || starting"
          :aria-busy="starting"
          @click="start"
        >
          {{ starting ? "正在启动…" : hasResult ? "再检查一次" : "确认并开始检查" }}
        </button>
      </div>
    </aside>
  </div>
</template>
