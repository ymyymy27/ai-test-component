<script setup lang="ts">
import { computed, nextTick, type ComponentPublicInstance } from "vue";

import { compareItemNo } from "@/domain/derive";
import { LAYER_LABEL } from "@/domain/labels";
import type { Plan, Task } from "@/domain/types";
import Icon from "./Icon.vue";

const props = defineProps<{
  plan: Plan;
  task: Task;
}>();

const groups = computed(() =>
  props.plan.scope.acceptanceItemIds
    .map((itemId) => {
      const item = props.task.acceptanceItems.find((entry) => entry.id === itemId);
      return {
        itemId,
        no: item?.no ?? "",
        text: item?.text ?? "验收项已归档",
        cases: props.plan.cases.filter((testCase) => testCase.itemId === itemId),
      };
    })
    .sort((first, second) => compareItemNo(first.no, second.no)),
);

const firstOpen = computed(() => groups.value[0]?.itemId ?? "");
const elementMap = new Map<string, HTMLElement>();

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
    <li v-for="group in groups" :key="group.itemId">
      <details
        :ref="(element) => setItemRef(group.itemId, element)"
        class="ledger-item"
        :open="group.itemId === firstOpen"
      >
        <summary>
          <span class="ledger-index">{{ group.no }}</span>
          <span class="ledger-text">{{ group.text }}</span>
          <span class="ledger-state muted">{{ group.cases.length }} 条检查</span>
          <Icon name="chevron-right" :size="14" class="ledger-chevron" />
        </summary>
        <div class="ledger-body">
          <article v-for="testCase in group.cases" :key="testCase.id" class="case-block">
            <header class="case-head">
              <span class="case-title">{{ testCase.title }}</span>
              <span class="badge">{{ LAYER_LABEL[testCase.layer] }}</span>
              <span v-if="!testCase.expectationVerified" class="badge is-warn">预期未核实</span>
            </header>
            <p class="case-method">{{ testCase.verifyMethod || "人工检查" }}</p>
            <div class="case-compare">
              <div class="case-col">
                <span class="case-label">预期</span>
                <span class="case-value">{{ testCase.expected }}</span>
              </div>
              <div class="case-col">
                <span class="case-label">预期来源</span>
                <span class="case-value">{{
                  testCase.expectationSource || "未引用验收项原文"
                }}</span>
              </div>
            </div>
            <p v-if="testCase.dependsOn" class="case-message">
              依赖：{{
                testCase.dependsOn === "build" ? "构建" : "本地服务"
              }}；未通过时本条按阻塞计。
            </p>
          </article>
        </div>
      </details>
    </li>
  </ol>
</template>
