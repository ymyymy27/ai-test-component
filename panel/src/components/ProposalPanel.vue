<script setup lang="ts">
import { computed } from "vue";

import { VERDICT_LABEL, formatDateTime } from "@/api/labels";
import AppButton from "@/components/AppButton.vue";
import ErrorNotice from "@/components/ErrorNotice.vue";
import Icon from "@/components/Icon.vue";
import SkeletonBlock from "@/components/SkeletonBlock.vue";
import {
  applicableCount,
  chooseCustomTarget,
  chooseTarget,
  loadProposal,
  openAdjustDrawer,
  openResult,
  prepareTest,
  selectedCount,
  session,
  startTest,
  toggleProposalItem,
} from "@/state/session";

const proposal = computed(() => session.proposal);
const smoke = computed(() => session.task?.intent === "smoke");
const smokeNoItems = computed(() => smoke.value && applicableCount() === 0);
const prepared = computed(() => Boolean(proposal.value.previewPlan));
const confirmation = computed(() => {
  const plan = proposal.value.previewPlan;
  if (!plan) {
    return null;
  }
  const cases = plan.cases ?? [];
  const layers = { L1: 0, L2: 0, L3: 0 };
  let scripts = 0;
  let commands = 0;
  let sideEffects = 0;
  for (const item of cases) {
    layers[item.layer] = (layers[item.layer] ?? 0) + 1;
    const method = item.verify_method ?? "";
    if (item.script_name || method.startsWith("script:")) {
      scripts += 1;
    }
    if (method.startsWith("command:") || method.startsWith("pytest:") || method.startsWith("sql:")) {
      commands += 1;
    }
    if (method.startsWith("http:")) {
      try {
        const requests = JSON.parse(method.slice(5));
        if (Array.isArray(requests)) {
          sideEffects += requests.filter(
            (request) => String(request?.method ?? "GET").toUpperCase() !== "GET",
          ).length;
        }
      } catch {
        // 非法 http 方法由 CLI 拒绝，这里只统计能解析的请求。
      }
    }
  }
  return {
    cases: cases.length,
    layers,
    scripts,
    commands,
    sideEffects,
    selected: plan.scope.acceptance_item_ids.length,
    applicable: plan.applicable_count,
    python: session.doctor?.python?.version ?? "未检测",
    modelSource: session.doctor?.model?.source ?? "unknown",
    github: session.doctor?.github?.ok ? "可用" : "不可用或未登录",
  };
});
const lastRun = computed(() => session.lastRun);
const lastHistory = computed(() => session.history[0]);
const lastRunLabel = computed(() => {
  const run = lastRun.value;
  if (!run) {
    return "";
  }
  if (run.status === "running") {
    return "上次还在运行";
  }
  return run.outcome ? `上次 ${VERDICT_LABEL[run.outcome]}` : "上次已结束";
});

function openLastRun(): void {
  if (lastHistory.value) {
    void openResult(lastHistory.value.run_id);
  }
}

const skipped = computed(() => applicableCount() - selectedCount());

function retryExtract(): void {
  const index = session.proposal.index;
  if (index >= 0 || index === -2) {
    void chooseTarget(index);
    return;
  }
  void chooseCustomTarget(session.proposal.goal);
}
</script>

<template>
  <section class="proposal" data-testid="proposal-panel">
    <SkeletonBlock
      v-if="proposal.suggestLoading || (proposal.itemsLoading && proposal.items.length === 0)"
      :lines="4"
      :label="proposal.suggestLoading ? '正在生成目标候选' : '正在从目标里提取验收标准'"
    />

    <template v-else>
      <ErrorNotice
        v-if="proposal.suggestError"
        :error="proposal.suggestError"
        retry-label="重新获取"
        @retry="loadProposal"
      />

      <section v-if="lastRun && lastHistory" class="last-run">
        <span class="last-run-copy">
          <strong>{{ lastRunLabel }}</strong>
          <span>
            {{ formatDateTime(lastRun.startedAt) }} ·
            {{ lastRun.applicable > 0 ? `${lastRun.selected} / ${lastRun.applicable} 项` : "项目检查" }}
          </span>
        </span>
        <button type="button" class="text-action" @click="openLastRun">查看</button>
      </section>

      <section class="criteria-block">
        <div class="section-title">
          <h2>{{ smokeNoItems ? "项目检查" : "检查项" }}</h2>
          <span class="section-note" data-testid="proposal-scope-value">
            <template v-if="smokeNoItems">
              {{ confirmation ? `${confirmation.cases} 项快速检查` : "项目检查" }}
            </template>
            <template v-else>
              已选 {{ selectedCount() }} / {{ applicableCount() }}
              <button type="button" class="text-action edit" data-testid="open-adjust" @click="openAdjustDrawer">
                编辑
              </button>
            </template>
          </span>
        </div>

        <p v-if="skipped > 0 && !smokeNoItems" class="hint scope-note">
          {{ skipped }} 项未选，不计入结论
        </p>

        <ErrorNotice
          v-if="proposal.itemsError"
          :error="proposal.itemsError"
          retry-label="重新提取"
          :busy="proposal.itemsLoading"
          @retry="retryExtract"
        />

        <ul v-if="proposal.items.length" class="criteria" data-testid="proposal-criteria">
          <li
            v-for="item in proposal.items"
            :key="item.id"
            class="criteria-row"
            :class="{ off: !item.observable, excluded: item.observable && !item.enabled }"
          >
            <label v-if="item.observable" class="criteria-label">
              <input
                type="checkbox"
                class="criteria-check"
                :checked="item.enabled"
                :aria-label="`纳入：${item.text}`"
                @change="toggleProposalItem(item)"
              />
              <span class="criteria-text">{{ item.text }}</span>
            </label>
            <span v-else class="criteria-text off-text">{{ item.text }}</span>
            <span v-if="!item.observable" class="criteria-note">暂时不能验证</span>
            <span
              v-else-if="item.source && item.source !== '任务描述'"
              class="criteria-note"
            >{{ item.source }}</span>
          </li>
        </ul>

        <p v-else-if="!proposal.itemsLoading && !smokeNoItems" class="hint empty-note">
          没有可检查的条目。可以编辑范围补一条，或换个目标。
        </p>
      </section>

      <section v-if="confirmation" class="confirm-card" data-testid="run-confirmation">
        <h2>执行前确认</h2>
        <ul>
          <li>
            范围：已选 {{ confirmation.selected }} / {{ confirmation.applicable }}，
            未选 {{ Math.max(0, confirmation.applicable - confirmation.selected) }} 项不计入结论。
          </li>
          <li>
            用例：{{ confirmation.cases }} 条（L1 {{ confirmation.layers.L1 }}，
            L2 {{ confirmation.layers.L2 }}，L3 {{ confirmation.layers.L3 }}）。
          </li>
          <li>环境：Python {{ confirmation.python }}；GitHub {{ confirmation.github }}。</li>
          <li>凭据：只从环境变量或用户配置读取，工作空间不保存密钥。</li>
          <li>
            副作用：{{ confirmation.sideEffects }} 个非 GET 请求，
            {{ confirmation.scripts }} 个生成脚本，{{ confirmation.commands }} 个命令。
          </li>
          <li>依赖安装：未声明；预计耗时：未估算。缺依赖或环境不可用时标阻塞。</li>
        </ul>
      </section>

      <p class="proposal-notes">
        {{ smokeNoItems ? "会导入入口、发现测试并运行最小测试模块" : "会写入测试数据，不改源码" }}
        <span aria-hidden="true">·</span> 关闭面板不中断
      </p>

      <ErrorNotice
        v-if="proposal.startError"
        :error="proposal.startError"
        :show-retry="false"
        @retry="() => {}"
      />

      <footer class="sheet-foot">
        <p class="hint foot-note">
          {{
            smokeNoItems
              ? "只读项目文件"
              : selectedCount() === 0
                ? "至少选择 1 项"
                : `${selectedCount()} 项检查`
          }}
        </p>
        <AppButton
          variant="primary"
          :disabled="selectedCount() === 0 && !smokeNoItems"
          :loading="proposal.starting"
          data-testid="start-test"
          @click="prepared ? startTest() : prepareTest()"
        >
          <Icon name="play" :size="13" />
          {{ prepared ? "确认并开始" : smokeNoItems ? "检查并开始" : "开始测试" }}
        </AppButton>
      </footer>
    </template>
  </section>
</template>

<style scoped>
.proposal {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.last-run {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--line);
}

.last-run-copy {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.last-run-copy strong {
  font-size: var(--fs-12);
  font-weight: 500;
}

.last-run-copy span {
  font-size: var(--fs-12);
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}

.text-action {
  flex: none;
  padding: 0;
  background: none;
  border: 0;
  color: var(--accent);
  font-size: var(--fs-12);
  cursor: pointer;
}

.text-action:hover {
  text-decoration: underline;
}

.text-action.edit {
  margin-left: 8px;
  color: var(--text-3);
  font-size: var(--fs-11);
}

.text-action.edit:hover {
  color: var(--accent);
}

.criteria-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.section-title {
  margin-bottom: 0;
}

.scope-note {
  margin-top: -4px;
}

.criteria {
  list-style: none;
  margin: var(--space-2) 0 0;
  padding: 0;
  border-top: 1px solid var(--line);
}

.criteria-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  min-height: 34px;
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
}

.criteria-row:hover {
  background: var(--surface-raised);
}

.criteria-label {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  flex: 1 1 auto;
  min-width: 0;
  cursor: pointer;
}

.criteria-check {
  margin-top: 3px;
  flex: none;
}

.criteria-text {
  font-size: var(--fs-13);
  line-height: 1.5;
  color: var(--text);
  overflow-wrap: anywhere;
}

.criteria-row.excluded .criteria-text,
.off-text {
  color: var(--text-3);
}

.criteria-note {
  flex: 0 0 auto;
  padding-top: 2px;
  font-size: var(--fs-12);
  color: var(--text-3);
  max-width: 14ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-note {
  padding: var(--space-3) 0;
}

.proposal-notes {
  font-size: var(--fs-12);
  color: var(--text-3);
  line-height: 1.5;
}

.proposal-notes span {
  margin: 0 4px;
}

.sheet-foot {
  position: sticky;
  bottom: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding: var(--space-3) 0;
  background: var(--bg);
  border-top: 1px solid var(--line);
}

.foot-note {
  flex: 1 1 220px;
  max-width: 42ch;
}

@media (max-width: 520px) {
  .last-run {
    align-items: flex-start;
  }

  .criteria-note {
    display: none;
  }
}
</style>
