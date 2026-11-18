<script setup lang="ts">
import { computed, ref, watch } from "vue";

import {
  CASE_RESULT_LABEL,
  formatDateTime,
  methodLabel,
} from "@/api/labels";
import type {
  Analysis,
  IssueSummary,
  Plan,
  PlanCase,
  RunCase,
  RunDetail,
  Task,
} from "@/api/types";

const props = withDefaults(
  defineProps<{
    task: Task | null;
    plan: Plan | null;
    run: RunDetail | null;
    issues: IssueSummary[];
    selected: string[];
    readonly?: boolean;
    scopeDirty?: boolean;
    mode: "plan" | "running" | "result";
  }>(),
  { readonly: false, scopeDirty: false },
);

const emit = defineEmits<{
  toggle: [itemId: string];
  "record-manual": [caseId: string];
  "open-evidence": [ref: string];
  "open-issue": [issueId: string];
  analyze: [];
  "open-plan": [];
}>();

const expanded = ref<Set<string>>(new Set());

const allItems = computed(() =>
  (props.task?.acceptance_items ?? []).filter((item) => item.status === "active"),
);

const items = computed(() => {
  if (props.selected.length === 0) {
    return allItems.value;
  }
  const chosen = new Set(props.selected);
  return allItems.value.filter((item) => chosen.has(item.item_id));
});

const excluded = computed(() => {
  if (props.selected.length === 0) {
    return [];
  }
  const chosen = new Set(props.selected);
  return allItems.value.filter((item) => !chosen.has(item.item_id));
});

const showExcluded = ref(false);

const caseItemIds = (item: PlanCase): string[] => {
  const ids: string[] = [];
  if (item.acceptance_item_id) {
    ids.push(item.acceptance_item_id);
  }
  for (const id of item.acceptance_item_ids ?? []) {
    if (!ids.includes(id)) {
      ids.push(id);
    }
  }
  return ids;
};

const casesFor = (itemId: string): PlanCase[] =>
  (props.plan?.cases ?? []).filter((item) => caseItemIds(item).includes(itemId));

const runCase = (caseId: string): RunCase | undefined =>
  props.run?.run.cases.find((item) => item.case_id === caseId);

const outcomeFor = (itemId: string): string | undefined =>
  props.run?.item_outcomes?.find((item) => item.item_id === itemId)?.result;

const caseTone = (item: PlanCase): string => {
  const result = runCase(item.case_id)?.result;
  if (result) {
    return result === "passed"
      ? "pass"
      : result === "failed"
        ? "fail"
        : result === "blocked" || result === "error"
          ? "block"
          : "off";
  }
  return caseItemIds(item).some((id) => props.selected.includes(id))
    ? "planned"
    : "off";
};

const itemTone = (itemId: string): string => {
  const result = outcomeFor(itemId);
  if (result) {
    if (result === "passed") {
      return "pass";
    }
    if (result === "failed") {
      return "fail";
    }
    if (result === "blocked" || result === "error") {
      return "block";
    }
    return props.selected.includes(itemId) ? "wait" : "off";
  }
  return props.selected.includes(itemId) ? "sel" : "off";
};

const itemState = (itemId: string): string => {
  const result = outcomeFor(itemId);
  if (result) {
    return result === "passed"
      ? "通过"
      : result === "failed"
        ? "失败"
        : result === "blocked"
          ? "阻塞"
          : result === "error"
            ? "出错"
            : result === "manual_pending"
              ? "待人工"
              : props.mode === "running" && itemHasCurrent(itemId)
                ? "运行中"
                : props.mode === "running" && props.selected.includes(itemId)
                  ? "等待结果"
                  : "未运行";
  }
  if (props.mode === "plan") {
    return props.selected.includes(itemId) ? "已计划" : "未选";
  }
  return "未运行";
};

const caseState = (item: PlanCase): string => {
  const result = runCase(item.case_id)?.result;
  if (result) {
    if (result === "not_run" && item.case_id === currentCaseId.value) {
      return "运行中";
    }
    if (result === "not_run" && props.mode === "running") {
      return "等待结果";
    }
    return CASE_RESULT_LABEL[result];
  }
  return props.mode === "plan" ? "" : "待运行";
};

const caseToneClass = (tone: string): string =>
  tone === "pass" ? "pass" : tone === "fail" ? "fail" : tone === "block" ? "block" : "";

const lastAttempt = (caseId: string) => {
  const attempts = runCase(caseId)?.attempts ?? [];
  return attempts.length ? attempts[attempts.length - 1] : undefined;
};

const actualOf = (item: PlanCase): string => {
  const result = runCase(item.case_id);
  const last = lastAttempt(item.case_id);
  if (last?.message) {
    return last.message;
  }
  if (result?.result === "passed") {
    return "执行通过";
  }
  if (result?.result === "failed") {
    return "执行失败，详情见证据";
  }
  return "尚未执行";
};

const evidenceOf = (item: PlanCase): string[] =>
  runCase(item.case_id)?.evidence_refs ?? [];

const issueOf = (item: PlanCase): IssueSummary | undefined =>
  props.issues.find((issue) => issue.last_case_id === item.case_id);

const manualCase = (item: PlanCase): boolean => {
  if (item.layer !== "L3") {
    return false;
  }
  const method = (item.verify_method ?? "").trim();
  if (!method || method.toLowerCase().startsWith("manual")) {
    return true;
  }
  const [prefix, ...rest] = method.split(":");
  const body = rest.join(":").trim();
  const automatable =
    Boolean(body) || !["command", "script", "http", "playwright"].includes(prefix);
  return !automatable;
};

const currentCaseId = computed(() =>
  props.mode === "running" ? (props.run?.run.current_case_id ?? "") : "",
);

const itemHasCurrent = (itemId: string): boolean =>
  Boolean(currentCaseId.value) &&
  casesFor(itemId).some((item) => item.case_id === currentCaseId.value);

const firstFailedCase = computed(() => {
  for (const item of items.value) {
    for (const planCase of casesFor(item.item_id)) {
      if (runCase(planCase.case_id)?.result === "failed") {
        return planCase.case_id;
      }
    }
  }
  return "";
});

const analysis = computed<Analysis | null>(() => {
  const entries = props.run?.run.analysis ?? [];
  return entries.length ? entries[entries.length - 1] : null;
});

const analysisReason = computed(() => {
  const reason = analysis.value?.reason;
  if (reason === "no_failures") return "本次没有失败，不需要分析";
  if (reason === "model_missing") return "模型未配置，未生成分析";
  if (reason === "model_invalid") return "模型输出不合格，整条不展示";
  if (reason === "model_error") return "模型调用失败，未生成分析";
  return "还没有生成分析";
});

function toggle(itemId: string): void {
  if (expanded.value.has(itemId)) {
    expanded.value.delete(itemId);
  } else {
    expanded.value.add(itemId);
  }
  expanded.value = new Set(expanded.value);
}

function onMark(itemId: string): void {
  if (props.mode === "plan" && !props.readonly) {
    emit("toggle", itemId);
  } else {
    toggle(itemId);
  }
}

function focus(itemId: string): void {
  expanded.value.add(itemId);
  expanded.value = new Set(expanded.value);
  const element = document.querySelector<HTMLElement>(`[data-item-id="${itemId}"]`);
  element?.scrollIntoView({ block: "nearest" });
}

defineExpose({ focus });

watch(
  () => [props.run?.run.run_id, props.plan?.plan_id, props.mode],
  () => {
    const firstFail = firstFailedCase.value;
    if (firstFail) {
      for (const item of items.value) {
        if (casesFor(item.item_id).some((row) => row.case_id === firstFail)) {
          expanded.value.add(item.item_id);
        }
      }
    } else if (props.mode === "running" && currentCaseId.value) {
      for (const item of items.value) {
        if (casesFor(item.item_id).some((row) => row.case_id === currentCaseId.value)) {
          expanded.value.add(item.item_id);
        }
      }
    } else if (props.plan && !props.run) {
      const first = items.value.find((item) => props.selected.includes(item.item_id));
      if (first) {
        expanded.value.add(first.item_id);
      }
    }
    expanded.value = new Set(expanded.value);
  },
  { immediate: true },
);
</script>

<template>
  <div class="ledger">
    <div
      v-for="(item, index) in items"
      :key="item.item_id"
      class="item"
      :data-item-id="item.item_id"
    >
      <div class="itemhead" @click="toggle(item.item_id)">
        <span class="no">{{ String(index + 1).padStart(2, "0") }}</span>
        <button
          v-if="mode === 'plan' && !readonly"
          type="button"
          class="mark"
          :class="itemTone(item.item_id)"
          :aria-label="selected.includes(item.item_id) ? '从本次范围移除' : '加入本次范围'"
          @click.stop="onMark(item.item_id)"
        />
        <span
          v-else-if="itemTone(item.item_id) !== 'off'"
          class="mark"
          :class="itemTone(item.item_id)"
          aria-hidden="true"
        />
        <span v-else class="mark spacer" aria-hidden="true" />
        <span class="text">{{ item.text }}</span>
        <span class="state" :class="itemTone(item.item_id)">{{ itemState(item.item_id) }}</span>
      </div>

      <div v-if="expanded.has(item.item_id)" class="cases">
        <p v-if="casesFor(item.item_id).length === 0" class="empty">
          <template v-if="scopeDirty && selected.includes(item.item_id)">
            范围已改；生成新计划后才会为这个验收项生成用例。
          </template>
          <template v-else>
            还没有用例。
            <button type="button" class="ui-link" @click="emit('open-plan')">去计划里补上</button>
          </template>
        </p>
        <article
          v-for="planCase in casesFor(item.item_id)"
          :key="planCase.case_id"
          class="case"
          :class="{ current: mode === 'running' && currentCaseId === planCase.case_id }"
        >
          <div class="casehead">
            <span class="casetitle">{{ planCase.title || "检查项" }}</span>
            <span class="casestate" :class="caseToneClass(caseTone(planCase))">
              {{ caseState(planCase) }}
              <template v-if="lastAttempt(planCase.case_id)?.finished_at">
                · {{ formatDateTime(lastAttempt(planCase.case_id)?.finished_at) }}
              </template>
            </span>
          </div>

          <div class="facts">
            <div class="fact">
              <span class="ui-label">预期</span>
              <span>{{ planCase.expected }}</span>
            </div>
            <div class="fact">
              <span class="ui-label">{{ run ? "实际" : "前置条件" }}</span>
              <span v-if="run">{{ actualOf(planCase) }}</span>
              <span v-else>{{ planCase.preconditions?.join("；") || "未填写" }}</span>
            </div>
          </div>

          <div v-if="!run" class="planfacts">
            <span>方式：{{ methodLabel(planCase.verify_method) }}</span>
            <span v-if="planCase.expectation_source">依据：{{ planCase.expectation_source }}</span>
            <span v-if="planCase.steps?.length">步骤：{{ planCase.steps.join(" → ") }}</span>
          </div>

          <div v-if="evidenceOf(planCase).length" class="evidence">
            <span class="ui-label">证据</span>
            <button
              v-for="ref in evidenceOf(planCase)"
              :key="ref"
              type="button"
              class="evidence-ref"
              @click="emit('open-evidence', ref)"
            >
              {{ ref.split("/").pop() }}
            </button>
          </div>

          <div v-if="issueOf(planCase)" class="issue">
            已记问题
            <button
              type="button"
              class="ui-link"
              @click="emit('open-issue', issueOf(planCase)!.issue_id)"
            >
              {{ issueOf(planCase)!.title }}
            </button>
            · {{ issueOf(planCase)!.status === "closed" ? "已关闭" : "待确认" }}
            <span v-if="issueOf(planCase)!.unstable"> · 疑似不稳定</span>
          </div>

          <div
            v-if="run && analysis && planCase.case_id === firstFailedCase"
            class="analysis"
          >
            <div class="analysis-head">
              <span>解释 · 不改变结论</span>
              <button
                v-if="analysis.status !== 'generated'"
                type="button"
                class="ui-link"
                @click="emit('analyze')"
              >
                重新分析
              </button>
            </div>
            <template v-if="analysis.status === 'generated'">
              <p>{{ analysis.observed || "未记录观察" }}</p>
              <p v-if="analysis.candidate_causes?.length" class="analysis-sub">
                可能原因：{{ analysis.candidate_causes.map((item) => item.cause).join("；") }}
              </p>
              <p v-if="analysis.unverified?.length" class="analysis-sub">
                尚未验证：{{ analysis.unverified.join("；") }}
              </p>
              <p v-if="analysis.next_steps?.length" class="analysis-sub">
                下一步：{{ analysis.next_steps.join("；") }}
              </p>
            </template>
            <p v-else class="analysis-sub">{{ analysisReason }}</p>
          </div>

          <div v-if="run && !readonly && manualCase(planCase) && run.run.status !== 'running'" class="case-actions">
            <button type="button" class="ui-btn small" @click="emit('record-manual', planCase.case_id)">
              记录结果
            </button>
          </div>
        </article>
      </div>
    </div>

    <p v-if="items.length === 0" class="empty">
      还没有验收项。
      <button type="button" class="ui-link" @click="emit('open-plan')">去任务里补上</button>
    </p>
    <div v-if="excluded.length" class="excluded">
      <button type="button" class="ui-link" @click="showExcluded = !showExcluded">
        未纳入本次 {{ excluded.length }} 项{{ showExcluded ? "，收起" : "" }}
      </button>
      <p class="ui-help">这些验收项不计入本次结论；需要覆盖时调整范围并生成新计划。</p>
      <div v-if="showExcluded" class="excluded-list">
        <div v-for="item in excluded" :key="item.item_id" class="excluded-row">
          <span class="text">{{ item.text }}</span>
          <span class="state off">未纳入</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ledger {
  border-top: 1px solid var(--line-strong);
}

.item {
  border-bottom: 1px solid var(--line);
  padding: 9px 0;
}

.itemhead {
  display: grid;
  grid-template-columns: 30px 20px minmax(0, 1fr) auto;
  gap: 8px;
  align-items: start;
  cursor: pointer;
}

.no {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  line-height: 17px;
}

.mark {
  position: relative;
  width: 11px;
  height: 11px;
  margin-top: 3px;
  padding: 0;
  border: 1px solid var(--line-strong);
  border-radius: 0;
  background: transparent;
}

.excluded {
  margin-top: 10px;
  padding-top: 7px;
  border-top: 1px solid var(--line);
}

.excluded-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 4px 0;
  border-bottom: 1px solid var(--line);
  font-size: var(--fs-11);
  color: var(--text-3);
}

.excluded-row .state {
  flex: none;
}

.mark.spacer {
  border-color: transparent;
}

.mark.wait {
  border-color: var(--accent);
}

.mark.sel {
  border-color: var(--accent);
  background: var(--accent);
}

.mark.pass {
  border-color: var(--passed);
}

.mark.pass::after {
  content: "";
  display: block;
  width: 5px;
  height: 2px;
  margin: 2px 0 0 2px;
  border-left: 1.5px solid var(--passed);
  border-bottom: 1.5px solid var(--passed);
  transform: rotate(-45deg);
}

.mark.fail {
  border-color: var(--failed);
  background: var(--failed);
}

.mark.fail::after,
.mark.fail::before {
  content: "";
  position: absolute;
  width: 7px;
  height: 1px;
  margin: 4px 0 0 1px;
  background: var(--bg);
}

.mark.fail::after {
  transform: rotate(45deg);
}

.mark.fail::before {
  transform: rotate(-45deg);
}

.mark.block {
  border-color: var(--incomplete);
  background: repeating-linear-gradient(
    45deg,
    var(--incomplete) 0 1px,
    transparent 1px 3px
  );
}

.mark.off {
  border-color: var(--line-strong);
}

.text {
  min-width: 0;
  color: var(--text);
  font-size: var(--fs-13);
  line-height: 1.45;
}

.state {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  line-height: 17px;
  white-space: nowrap;
}

.state.pass {
  color: var(--passed);
}

.state.fail {
  color: var(--failed);
}

.state.block {
  color: var(--incomplete);
}

.cases {
  margin: 7px 0 0 58px;
  border-left: 1px solid var(--line-strong);
  padding-left: 12px;
}

.case {
  padding: 5px 0 9px;
  border-bottom: 1px solid var(--line);
}

.case:last-child {
  border-bottom: 0;
  padding-bottom: 3px;
}

.case.current {
  border-left: 2px solid var(--accent);
  margin-left: -14px;
  padding-left: 12px;
  background: var(--surface-sunken);
}

.casehead {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.casetitle {
  font-size: var(--fs-12);
  font-weight: 600;
  line-height: 1.45;
}

.casestate {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
  white-space: nowrap;
}

.casestate.pass {
  color: var(--passed);
}

.casestate.fail {
  color: var(--failed);
}

.casestate.block {
  color: var(--incomplete);
}

.facts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 5px;
  color: var(--text-2);
  font-size: var(--fs-11);
  line-height: 1.5;
}

.fact span:last-child {
  display: block;
}

.planfacts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin-top: 5px;
  color: var(--text-3);
  font-size: var(--fs-10);
  line-height: 1.5;
}

.evidence {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-top: 6px;
}

.evidence-ref {
  padding: 2px 6px;
  border: 1px solid var(--line-strong);
  background: var(--surface-sunken);
  color: var(--text-2);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
}

.evidence-ref:hover {
  border-color: var(--accent);
  color: var(--text);
}

.issue {
  margin-top: 5px;
  color: var(--text-3);
  font-size: var(--fs-10);
  line-height: 1.5;
}

.issue .ui-link {
  font-size: inherit;
}

.analysis {
  margin-top: 7px;
  border-top: 1px dashed var(--line-strong);
  padding-top: 6px;
  color: var(--text-2);
  font-size: var(--fs-11);
  line-height: 1.55;
}

.analysis-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 3px;
  color: var(--text-3);
  font-size: var(--fs-10);
}

.analysis-sub {
  color: var(--text-3);
}

.case-actions {
  margin-top: 6px;
}

.empty {
  padding: 10px 0;
  color: var(--text-3);
  font-size: var(--fs-11);
}

.empty .ui-link {
  font-size: inherit;
}

@media (max-width: 520px) {
  .itemhead {
    grid-template-columns: 24px 18px minmax(0, 1fr) auto;
    gap: 6px;
  }

  .cases {
    margin-left: 48px;
    padding-left: 9px;
  }

  .facts {
    grid-template-columns: 1fr;
    gap: 6px;
  }
}
</style>
