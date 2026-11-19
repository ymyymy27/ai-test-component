<script setup lang="ts">
import { computed, nextTick, ref } from "vue";

const props = defineProps<{
  modelValue: string;
  busy: boolean;
  analysisBusy: boolean;
  ready: boolean;
  catalogCount: number;
  featureCount: number;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string];
  start: [goal: string];
  analyze: [];
  quickCheck: [];
}>();

const inputRef = ref<HTMLTextAreaElement | null>(null);
const error = ref("");

const disabled = computed(() => props.busy || props.analysisBusy);

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
  if (!props.ready) {
    error.value = "项目分析还没有完成；先点“帮我分析项目”，或等分析结束后再开始。";
    return;
  }
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
        rows="2"
        placeholder="例如：验证删除标记后批次仍然可读"
        :aria-invalid="error ? 'true' : undefined"
        :aria-describedby="error ? 'goal-error' : 'goal-hint'"
        :disabled="busy"
        @input="onInput"
        @keydown="onKeydown"
      />
      <span id="goal-hint" class="field-hint">
        写目标后创建任务，下一步选范围。Ctrl / Cmd + Enter 开始。
      </span>
      <span v-if="error" id="goal-error" class="field-error" role="alert">{{ error }}</span>
    </label>
    <div class="goal-composer-actions">
      <button
        class="btn btn-primary"
        type="button"
        :disabled="disabled || !ready"
        :aria-busy="busy"
        @click="start"
      >
        {{ busy ? "正在提取验收项…" : "创建任务并选范围" }}
      </button>
      <button
        class="btn btn-ghost"
        type="button"
        :disabled="disabled"
        :aria-busy="analysisBusy"
        @click="emit('analyze')"
      >
        {{ analysisBusy ? "分析中…" : "帮我分析项目" }}
      </button>
      <button class="btn btn-ghost" type="button" :disabled="busy" @click="emit('quickCheck')">
        快速检查（项目级）
      </button>
    </div>
    <p v-if="!ready" class="field-hint">分析完成后可创建带验收项的任务。</p>
  </section>
</template>
