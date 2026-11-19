<script setup lang="ts">
import { computed, nextTick, type ComponentPublicInstance } from "vue";

import { effectiveCaseResult, worstResult } from "@/domain/derive";
import type { CaseResult, Run, RunItem } from "@/domain/types";
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
  }>(),
  { filter: "all", allowManual: false, manualBusyCaseId: "" },
);

const emit = defineEmits<{ manual: [caseId: string] }>();

const elementMap = new Map<string, HTMLElement>();

const visibleItems = computed(() => {
  if (props.filter === "all") {
    return props.items;
  }
  return props.items.filter((item) => {
    const outcome = item.outcome;
    if (props.filter === "problems") {
      return ["failed", "blocked", "error"].includes(outcome);
    }
    return outcome !== "passed";
  });
});

const firstProblemId = computed(
  () =>
    visibleItems.value.find((item) => ["failed", "blocked", "error"].includes(item.outcome))
      ?.itemId ?? "",
);

function resultFor(itemId: string): CaseResult {
  const cases = props.run.cases.filter((testCase) => testCase.itemId === itemId);
  if (cases.length === 0) {
    return "not_run";
  }
  return worstResult(cases.map((testCase) => effectiveCaseResult(testCase, props.run)));
}

function casesFor(itemId: string) {
  return props.run.cases.filter((testCase) => testCase.itemId === itemId);
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
            <StatusText :result="resultFor(item.itemId)" />
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
          />
          <p v-if="casesFor(item.itemId).length === 0" class="doc-note">这个验收项没有用例。</p>
        </div>
      </details>
    </li>
  </ol>
</template>
