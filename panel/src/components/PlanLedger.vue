<script setup lang="ts">
import { computed, nextTick, type ComponentPublicInstance } from "vue";

import { compareItemNo } from "@/domain/derive";
import { LAYER_LABEL } from "@/domain/labels";
import type { Plan, PlanCase, Task } from "@/domain/types";
import Icon from "./Icon.vue";

const props = withDefaults(
  defineProps<{
    plan: Plan;
    task: Task;
    groupByFlow?: boolean;
  }>(),
  {
    groupByFlow: false,
  },
);

interface LedgerItemGroup {
  itemId: string;
  no: string;
  text: string;
  flowName: string;
  cases: PlanCase[];
}

const groups = computed<LedgerItemGroup[]>(() => {
  const scoped: LedgerItemGroup[] = props.plan.scope.acceptanceItemIds.map((itemId) => {
    const item = props.task.acceptanceItems.find((entry) => entry.id === itemId);
    return {
      itemId,
      no: item?.no ?? "",
      text: item?.text ?? "验收项已归档",
      flowName: item?.featureName || item?.moduleName || "通用验证流程",
      cases: props.plan.cases.filter((testCase) =>
        [testCase.itemId, ...testCase.extraItemIds].filter(Boolean).includes(itemId),
      ),
    };
  });
  const unbound = props.plan.cases.filter(
    (testCase) =>
      ![testCase.itemId, ...testCase.extraItemIds]
        .filter(Boolean)
        .some((itemId) => props.plan.scope.acceptanceItemIds.includes(itemId)),
  );
  if (unbound.length > 0) {
    scoped.push({
      itemId: "__unbound__",
      no: "—",
      text: "未绑定验收项的检查",
      flowName: "附加核验项",
      cases: unbound,
    });
  }
  return scoped
    .filter((group) => group.cases.length > 0 || group.itemId !== "__unbound__")
    .sort((first, second) => compareItemNo(first.no, second.no));
});

const flowBuckets = computed(() => {
  const buckets = new Map<string, LedgerItemGroup[]>();
  groups.value.forEach((group) => {
    const list = buckets.get(group.flowName);
    if (list) {
      list.push(group);
    } else {
      buckets.set(group.flowName, [group]);
    }
  });
  return Array.from(buckets.entries()).map(([name, items], idx) => ({
    stepNo: idx + 1,
    name,
    items,
  }));
});

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
  <div v-if="groupByFlow" class="stack">
    <section v-for="bucket in flowBuckets" :key="bucket.name" class="stack-sm">
      <div class="section-head">
        <h3 class="section-title">
          <span class="mono muted">{{ String(bucket.stepNo).padStart(2, "0") }}</span>
          {{ bucket.name }}
        </h3>
        <span class="section-note">{{ bucket.items.length }} 项验收</span>
      </div>
      <ol class="ledger">
        <li v-for="group in bucket.items" :key="group.itemId">
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
                <div class="case-compare">
                  <div class="case-col">
                    <span class="case-label">期望效果</span>
                    <span class="case-value">{{ testCase.expected }}</span>
                  </div>
                  <div class="case-col">
                    <span class="case-label">验收依据</span>
                    <span class="case-value">{{
                      testCase.expectationSource || "未引用验收项原文"
                    }}</span>
                  </div>
                </div>
                <details class="details-tech">
                  <summary>执行方式与依赖细节</summary>
                  <p class="case-method">检查手段：{{ testCase.verifyMethod || "人工检查" }}</p>
                  <p v-if="testCase.dependsOn" class="case-message">
                    前置依赖：{{
                      testCase.dependsOn === "build" ? "构建" : "本地服务"
                    }}（未通过时将阻塞本条）。
                  </p>
                </details>
              </article>
            </div>
          </details>
        </li>
      </ol>
    </section>
  </div>

  <ol v-else class="ledger">
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
