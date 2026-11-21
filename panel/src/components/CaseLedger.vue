<script setup lang="ts">
import { computed, nextTick, type ComponentPublicInstance } from "vue";

import { displayItemOutcome, effectiveCaseResult, worstResult } from "@/domain/derive";
import type { CaseResult, Run, RunCase, RunItem } from "@/domain/types";
import CaseDetail from "./CaseDetail.vue";
import Icon from "./Icon.vue";
import StatusMark from "./StatusMark.vue";
import StatusText from "./StatusText.vue";

const props = withDefaults(
  defineProps<{
    run: Run;
    items: RunItem[];
    filter?: "all" | "problems" | "unfinished";
    allowManual?: boolean;
    manualBusyCaseId?: string;
    running?: boolean;
  }>(),
  { filter: "all", allowManual: false, manualBusyCaseId: "", running: false },
);

const emit = defineEmits<{ manual: [caseId: string]; resetFilter: []; evidence: [caseId: string] }>();

const elementMap = new Map<string, HTMLElement>();

const visibleItems = computed(() => {
  if (props.filter === "all") {
    return props.items;
  }
  return props.items.filter((item) => {
    const outcome = displayItemOutcome(props.run, item.itemId);
    if (props.filter === "problems") {
      return ["failed", "blocked", "error"].includes(outcome);
    }
    return ["not_run", "manual_pending", "blocked"].includes(outcome);
  });
});

const firstProblemId = computed(
  () =>
    visibleItems.value.find((item) =>
      ["failed", "blocked", "error"].includes(displayItemOutcome(props.run, item.itemId)),
    )?.itemId ?? "",
);

function belongsTo(testCase: RunCase, itemId: string): boolean {
  return testCase.caseItemIds?.includes(itemId) ?? testCase.itemId === itemId;
}

function resultFor(itemId: string): CaseResult {
  // 与顶部计数、筛选使用同一来源：宿主运行以 CLI item_outcomes 为准。
  if (props.run.items.some((item) => item.itemId === itemId)) {
    return displayItemOutcome(props.run, itemId);
  }
  const cases = props.run.cases.filter((testCase) => belongsTo(testCase, itemId));
  if (cases.length === 0) {
    return "not_run";
  }
  return worstResult(cases.map((testCase) => effectiveCaseResult(testCase, props.run)));
}

function casesFor(itemId: string) {
  return props.run.cases.filter((testCase) => belongsTo(testCase, itemId));
}

function setItemRef(itemId: string, element: Element | ComponentPublicInstance | null): void {
  if (element instanceof HTMLElement) {
    elementMap.set(itemId, element);
  } else {
    elementMap.delete(itemId);
  }
}

async function focusItem(itemId: string): Promise<void> {
  const element = elementMap.get(itemId);
  if (!element) {
    return;
  }
  element.setAttribute("open", "");
  await nextTick();
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  element.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
  element.querySelector<HTMLElement>("summary")?.focus();
}

defineExpose({ focusItem });
</script>

<template>
  <ol class="ledger">
    <li v-for="item in visibleItems" :key="item.itemId">
      <details
        :ref="(element) => setItemRef(item.itemId, element)"
        class="ledger-item"
        :open="item.itemId === firstProblemId"
      >
        <summary>
          <span class="ledger-index">{{ item.no }}</span>
          <span class="ledger-text">{{ item.text }}</span>
          <span class="ledger-state">
            <StatusMark :result="resultFor(item.itemId)" />
            <StatusText
              :result="resultFor(item.itemId)"
              :label="running && resultFor(item.itemId) === 'not_run' ? '待执行' : ''"
            />
          </span>
          <Icon name="chevron-right" :size="14" class="ledger-chevron" />
        </summary>
        <div class="ledger-body">
          <CaseDetail
            v-for="testCase in casesFor(item.itemId)"
            :key="testCase.id"
            :test-case="testCase"
            :result="effectiveCaseResult(testCase, run)"
            :allow-manual="allowManual"
            :manual-busy="manualBusyCaseId === testCase.id"
            @manual="emit('manual', $event)"
            @evidence="emit('evidence', $event)"
          />
          <p v-if="casesFor(item.itemId).length === 0" class="doc-note">这个验收项没有用例。</p>
        </div>
      </details>
    </li>
    <li v-if="!visibleItems.length" class="ledger-empty">
      <p class="doc-note">
        当前筛选没有验收项。
        <button class="link-btn" type="button" @click="emit('resetFilter')">回到全部</button>
      </p>
    </li>
  </ol>
</template>
