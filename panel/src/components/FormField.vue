<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    id: string;
    label: string;
    optional?: string;
    hint?: string;
    error?: string;
    required?: boolean;
  }>(),
  {
    optional: "",
    hint: "",
    error: "",
    required: false,
  },
);

const hintId = computed(() => (props.hint ? `${props.id}-hint` : undefined));
const errorId = computed(() => (props.error ? `${props.id}-error` : undefined));
const describedBy = computed(() => {
  const ids = [hintId.value, errorId.value].filter(Boolean);
  return ids.length ? ids.join(" ") : undefined;
});
const invalid = computed<"true" | undefined>(() => (props.error ? "true" : undefined));
</script>

<template>
  <div class="field" :class="{ 'has-error': Boolean(error) }">
    <label :for="id">
      <span>{{ label }}</span>
      <span v-if="required" class="field-required" aria-hidden="true">*</span>
      <span v-if="optional" class="optional">{{ optional }}</span>
    </label>
    <slot :input-id="id" :described-by="describedBy" :invalid="invalid" />
    <p v-if="hint" :id="hintId" class="field-hint">{{ hint }}</p>
    <p v-if="error" :id="errorId" class="field-error" role="alert">{{ error }}</p>
  </div>
</template>
