<script setup lang="ts">
import { computed } from "vue";

import { sideEffectsFor } from "@/domain/derive";
import type { EnvironmentCheck, Plan, Project, Task } from "@/domain/types";

const props = defineProps<{
  plan: Plan;
  task: Task;
  project: Project;
  environment: EnvironmentCheck[];
  checked: boolean;
  warningsAcknowledged: boolean;
  busy: boolean;
  startDisabled: boolean;
  startError?: string;
}>();

const emit = defineEmits<{
  "update:checked": [value: boolean];
  "update:warningsAcknowledged": [value: boolean];
  start: [];
}>();

const dependencies = computed(() => [
  ...new Set(props.plan.cases.flatMap((testCase) => testCase.dependencies)),
]);
const effects = computed(() => sideEffectsFor(props.project));
const environmentLine = computed(() => {
  const wanted = ["cli", "model", "github"];
  return props.environment
    .filter((check) => wanted.includes(check.key))
    .map((check) => `${check.name}：${check.value}`)
    .join(" · ");
});
const stale = computed(() => props.plan.stale);
const missingEnvironment = computed(() => props.environment.filter((check) => !check.ok).length);

function toggle(event: Event): void {
  const target = event.target;
  if (target instanceof HTMLInputElement) {
    emit("update:checked", target.checked);
  }
}

function toggleWarnings(event: Event): void {
  const target = event.target;
  if (target instanceof HTMLInputElement) {
    emit("update:warningsAcknowledged", target.checked);
  }
}
</script>

<template>
  <section class="confirm-block" :class="{ 'is-active': checked }" aria-labelledby="confirm-title">
    <div class="confirm-head">
      <h2 id="confirm-title" class="confirm-title">执行前确认</h2>
      <span class="confirm-note">确认绑定计划版本、范围、环境、依赖与副作用</span>
    </div>
    <p v-if="stale" class="notice is-warn" role="alert">
      {{ plan.staleReason || "验收项或验证方式已变化，需要生成新计划。" }}
    </p>
    <dl class="confirm-grid">
      <div class="confirm-item">
        <dt>计划</dt>
        <dd>计划 {{ plan.no }} · 第 {{ plan.revision }} 版</dd>
      </div>
      <div class="confirm-item">
        <dt>范围</dt>
        <dd>{{ plan.scope.coverage.selected }} 项 / 共 {{ plan.scope.coverage.applicable }} 项</dd>
      </div>
      <div class="confirm-item">
        <dt>依赖</dt>
        <dd>{{ dependencies.length ? dependencies.join("、") : "无额外依赖" }}</dd>
      </div>
      <div class="confirm-item">
        <dt>环境</dt>
        <dd>
          {{ missingEnvironment ? `缺失 ${missingEnvironment} 项` : "通过" }}
          <span v-if="environmentLine" class="confirm-env">{{ environmentLine }}</span>
          <span v-if="missingEnvironment" class="confirm-env">
            缺失只影响对应能力，本地记录、证据与报告仍可用。
          </span>
        </dd>
      </div>
    </dl>
    <h3 class="side-effects-title">副作用</h3>
    <ul class="side-effects">
      <li v-for="effect in effects" :key="effect">{{ effect }}</li>
    </ul>
    <p v-if="startError" class="field-error" role="alert">{{ startError }}</p>
    <label class="checkline" for="confirm-execution">
      <input
        id="confirm-execution"
        type="checkbox"
        :checked="checked"
        :disabled="startDisabled"
        aria-describedby="confirm-start-hint"
        @change="toggle"
      />
      <span>我已确认本次范围、依赖和副作用，同意开始测试。</span>
    </label>
    <p id="confirm-start-hint" class="field-hint">
      勾选后按 Ctrl / Cmd + Enter 也可以开始；未勾选时不会启动。
    </p>
    <template v-if="plan.warnings.length">
      <ul class="list-plain confirm-warnings">
        <li v-for="warning in plan.warnings" :key="warning">{{ warning }}</li>
      </ul>
      <label class="checkline" for="confirm-warnings">
        <input
          id="confirm-warnings"
          type="checkbox"
          :checked="warningsAcknowledged"
          :disabled="startDisabled"
          @change="toggleWarnings"
        />
        <span>我已逐条查看以上 {{ plan.warnings.length }} 条计划警告。</span>
      </label>
    </template>
  </section>
</template>
