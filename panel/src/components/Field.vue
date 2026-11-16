<script setup lang="ts">
defineProps<{
  label: string;
  forId?: string;
  hint?: string;
  error?: string;
  required?: boolean;
}>();
</script>

<template>
  <div class="field">
    <label :for="forId" class="field-label">
      {{ label }}
      <span v-if="required" class="req" aria-hidden="true">*</span>
    </label>
    <slot />
    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-else-if="hint" class="hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: var(--fs-12);
  color: var(--text-2);
  font-weight: 500;
}

.req {
  color: var(--incomplete);
  margin-left: 2px;
}
</style>
