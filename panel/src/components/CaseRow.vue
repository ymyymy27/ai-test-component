<script setup lang="ts">
import { computed, ref } from "vue";

import {
  CASE_RESULT_LABEL,
  CASE_RESULT_TONE,
  LAYER_LABEL,
  dependencyLabel,
  methodLabel,
} from "@/api/labels";
import type { PlanCase, RunCase } from "@/api/types";
import Badge from "./Badge.vue";
import Icon from "./Icon.vue";

const props = defineProps<{
  runCase: RunCase;
  planCase?: PlanCase;
  itemText?: string;
  index: number;
}>();

const expanded = ref(false);

const title = computed(() => {
  const planCase = props.planCase;
  if (planCase?.title) {
    return planCase.title;
  }
  if (planCase?.expected) {
    return planCase.expected;
  }
  return `用例 ${props.index + 1}`;
});

const attempts = computed(() => props.runCase.attempts ?? []);
const messages = computed(() =>
  attempts.value
    .map((attempt) => attempt.message?.trim())
    .filter((message): message is string => Boolean(message)),
);
const evidence = computed(() => {
  const refs = new Set<string>([
    ...(props.runCase.evidence_refs ?? []),
    ...attempts.value.flatMap((attempt) => attempt.evidence_refs ?? []),
  ]);
  return [...refs];
});
</script>

<template>
  <article class="case" :class="{ open: expanded }">
    <button
      type="button"
      class="case-head"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <span class="chev" aria-hidden="true">
        <Icon name="chevron-right" :size="13" />
      </span>
      <span class="case-main">
        <span class="case-title">{{ title }}</span>
        <span class="case-meta">
          <Badge v-if="planCase" tone="neutral">{{ LAYER_LABEL[planCase.layer] }}</Badge>
          <span v-if="itemText" class="item-text">{{ itemText }}</span>
        </span>
      </span>
      <Badge :tone="CASE_RESULT_TONE[runCase.result]">
        {{ CASE_RESULT_LABEL[runCase.result] }}
      </Badge>
    </button>

    <div v-if="expanded" class="case-body">
      <dl v-if="planCase" class="facts">
        <div>
          <dt>预期</dt>
          <dd>{{ planCase.expected }}</dd>
        </div>
        <div v-if="planCase.expected === planCase.expectation_source">
          <dt>依据</dt>
          <dd>直接引用验收标准原文</dd>
        </div>
        <div v-else-if="planCase.expectation_source">
          <dt>依据</dt>
          <dd>{{ planCase.expectation_source }}</dd>
        </div>
        <div>
          <dt>验证方式</dt>
          <dd>{{ methodLabel(planCase.verify_method) }}</dd>
        </div>
        <div v-if="dependencyLabel(planCase.depends_on)">
          <dt>依赖</dt>
          <dd>{{ dependencyLabel(planCase.depends_on) }}</dd>
        </div>
      </dl>

      <div v-if="messages.length" class="block">
        <h3>执行输出</h3>
        <pre class="output">{{ messages.join("\n\n") }}</pre>
      </div>

      <div v-if="evidence.length" class="block">
        <h3>证据（{{ evidence.length }}）</h3>
        <ul class="evidence">
          <li v-for="ref in evidence" :key="ref" class="mono">{{ ref }}</li>
        </ul>
      </div>

      <p v-if="!messages.length && !evidence.length" class="hint">
        这条用例还没有附执行输出或证据。
      </p>
    </div>
  </article>
</template>

<style scoped>
.case {
  border-bottom: 1px solid var(--line);
}

.case:last-child {
  border-bottom: 0;
}

.case-head {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-3) var(--space-4);
  background: none;
  border: 0;
  text-align: left;
  color: var(--text);
}

.case-head:hover {
  background: var(--surface-raised);
}

.chev {
  color: var(--text-3);
  margin-top: 2px;
  transition: transform 140ms ease;
}

.case.open .chev {
  transform: rotate(90deg);
}

.case-main {
  display: flex;
  flex-direction: column;
  gap: 5px;
  flex: 1 1 auto;
  min-width: 0;
}

.case-title {
  font-size: var(--fs-13);
  line-height: 1.45;
  word-break: break-word;
}

.case-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.item-text {
  font-size: var(--fs-12);
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 34ch;
}

.case-body {
  padding: 0 var(--space-4) var(--space-4) 34px;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.facts {
  display: grid;
  gap: var(--space-2);
  margin: 0;
}

.facts > div {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: var(--space-2);
}

.facts dt {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.facts dd {
  margin: 0;
  font-size: var(--fs-12);
  color: var(--text-2);
  line-height: 1.6;
  word-break: break-word;
}

.block h3 {
  font-size: var(--fs-12);
  color: var(--text-3);
  font-weight: 500;
  margin-bottom: 6px;
}

.evidence {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.evidence li {
  color: var(--text-2);
  word-break: break-all;
}

@media (max-width: 480px) {
  .case-head {
    padding: var(--space-3);
  }

  .case-body {
    padding: 0 var(--space-3) var(--space-3) 28px;
  }

  .item-text {
    max-width: 100%;
  }
}
</style>
