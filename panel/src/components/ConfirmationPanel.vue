<script setup lang="ts">
import { computed, ref } from "vue";

import type { Plan } from "@/api/types";

const props = defineProps<{
  plan: Plan;
  scope: string[];
  applicable: number;
  stale?: boolean;
  staleReason?: string;
  busy?: boolean;
}>();

const emit = defineEmits<{ confirm: []; "open-plan": [] }>();
const checked = ref(false);

const selectedCases = computed(() =>
  props.plan.cases.filter((item) => {
    const ids = [
      ...(item.acceptance_item_id ? [item.acceptance_item_id] : []),
      ...(item.acceptance_item_ids ?? []),
    ];
    return ids.length === 0 || ids.some((id) => props.scope.includes(id));
  }),
);

const dependencies = computed(() => {
  const values = new Set<string>();
  for (const item of selectedCases.value) {
    for (const dep of item.dependencies ?? []) {
      values.add(dep);
    }
  }
  return [...values];
});

const planLabel = computed(() =>
  props.plan.plan_no
    ? `计划 ${props.plan.plan_no}${props.plan.revision > 1 ? ` · 第 ${props.plan.revision} 版` : ""}`
    : "当前计划",
);
</script>

<template>
  <section class="confirm" :class="{ stale }">
    <div class="head">
      <strong>执行前确认</strong>
      <span>{{ stale ? "计划需要更新" : "确认后才开始" }}</span>
    </div>

    <p v-if="stale" class="stale-copy">
      {{ staleReason || "任务验收项已变化，当前计划不能再运行。" }}
      <button type="button" class="ui-link" @click="emit('open-plan')">查看计划</button>
    </p>

    <dl class="facts">
      <div>
        <dt>范围</dt>
        <dd>{{ scope.length }} 项 · {{ selectedCases.length }} 条用例</dd>
      </div>
      <div>
        <dt>计划</dt>
        <dd>{{ planLabel }}</dd>
      </div>
      <div>
        <dt>依赖</dt>
        <dd>{{ dependencies.length ? dependencies.join("、") : "无额外依赖" }}</dd>
      </div>
      <div>
        <dt>会改动什么</dt>
        <dd>按用例执行检查；可能写入测试数据</dd>
      </div>
    </dl>

    <div class="foot">
      <label class="check">
        <input v-model="checked" type="checkbox" :disabled="stale || busy" />
        <span>我已确认范围、环境和副作用</span>
      </label>
      <div class="actions">
        <button type="button" class="ui-link" @click="emit('open-plan')">查看 / 编辑计划</button>
        <button
          type="button"
          class="ui-btn primary"
          :disabled="!checked || stale || busy"
          @click="emit('confirm')"
        >
          {{ busy ? "正在开始…" : "开始测试" }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.confirm {
  flex: none;
  border-top: 1px solid var(--line);
  border-left: 2px solid var(--accent);
  background: var(--surface-sunken);
  padding: 10px var(--space-4) 12px;
}

.confirm.stale {
  border-left-color: var(--incomplete);
}

.head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: 7px;
}

.head strong {
  font-size: var(--fs-12);
}

.head span {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-10);
}

.stale-copy {
  margin-bottom: 6px;
  color: var(--incomplete);
  font-size: var(--fs-11);
  line-height: 1.5;
}

.facts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 16px;
  margin: 0;
}

.facts div {
  min-width: 0;
}

.facts dt {
  color: var(--text-3);
  font-size: var(--fs-10);
}

.facts dd {
  margin: 0;
  color: var(--text-2);
  font-size: var(--fs-11);
  line-height: 1.45;
}

.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-top: 9px;
}

.check {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-2);
  font-size: var(--fs-11);
  line-height: 1.4;
}

.actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

@media (max-width: 520px) {
  .confirm {
    padding: 9px var(--space-3) 11px;
  }

  .facts {
    grid-template-columns: 1fr;
    gap: 4px;
  }

  .foot {
    align-items: flex-start;
    flex-direction: column;
  }

  .actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
