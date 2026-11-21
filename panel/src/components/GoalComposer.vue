<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";

const props = defineProps<{
  modelValue: string;
  busy: boolean;
  canStart: boolean;
  blockedReason?: string;
  catalogCount: number;
  featureCount: number;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string];
  start: [goal: string];
}>();

const inputRef = ref<HTMLTextAreaElement | null>(null);
const error = ref("");
const disabled = computed(() => props.busy);
const remaining = computed(() => 200 - props.modelValue.length);

// 从候选填入目标时不会经过 textarea 的 input 事件；必须同样清掉旧错误。
watch(
  () => props.modelValue,
  () => {
    error.value = "";
  },
);

function update(value: string): void {
  error.value = "";
  emit("update:modelValue", value);
}

function onInput(event: Event): void {
  const target = event.target;
  if (target instanceof HTMLTextAreaElement) {
    update(target.value);
  }
}

function start(): void {
  const goal = props.modelValue.trim();
  if (!goal) {
    error.value = "先写一句要验证的目标。";
    void nextTick(() => inputRef.value?.focus());
    return;
  }
  if (!props.canStart) {
    error.value = props.blockedReason || "当前还不能创建任务。";
    return;
  }
  error.value = "";
  emit("start", goal);
}

function onKeydown(event: KeyboardEvent): void {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    start();
  }
}
</script>

<template>
  <section class="goal-composer" aria-labelledby="goal-composer-title">
    <div class="goal-composer-head">
      <h2 id="goal-composer-title" class="goal-composer-title">你想验证什么？</h2>
      <span class="goal-composer-meta">
        当前理解 <strong class="num">{{ catalogCount }}</strong> 个可测项 ·
        <strong class="num">{{ featureCount }}</strong> 个业务功能
      </span>
    </div>
    <label class="field goal-field" :class="{ 'has-error': error }">
      <span class="visually-hidden">验证目标</span>
      <textarea
        ref="inputRef"
        :value="modelValue"
        rows="3"
        maxlength="200"
        placeholder="例如：验证删除标记后批次仍然可读"
        :aria-invalid="error ? 'true' : undefined"
        :aria-describedby="error ? 'goal-error' : 'goal-hint'"
        :disabled="disabled"
        @input="onInput"
        @keydown="onKeydown"
      />
      <span id="goal-hint" class="field-hint">
        写目标或选候选，然后点“创建任务并生成计划”。Ctrl / Cmd + Enter 也可以。
        <span class="goal-count">{{ modelValue.length }} / 200</span>
      </span>
      <span v-if="error" id="goal-error" class="field-error" role="alert">{{ error }}</span>
      <span v-if="remaining <= 20" class="field-hint">还可输入 {{ remaining }} 个字。</span>
    </label>
    <div class="goal-actions">
      <button
        class="btn btn-primary"
        type="button"
        :disabled="disabled || !canStart"
        :aria-busy="busy"
        @click="start"
      >
        {{ busy ? "正在创建并生成计划…" : "创建任务并生成计划" }}
      </button>
    </div>
  </section>
</template>
