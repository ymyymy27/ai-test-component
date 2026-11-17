<script setup lang="ts">
withDefaults(
  defineProps<{
    variant?: "primary" | "secondary" | "ghost" | "danger";
    size?: "md" | "sm";
    htmlType?: "button" | "submit";
    disabled?: boolean;
    loading?: boolean;
    block?: boolean;
  }>(),
  {
    variant: "secondary",
    size: "md",
    htmlType: "button",
    disabled: false,
    loading: false,
    block: false,
  },
);
</script>

<template>
  <button
    :type="htmlType"
    :class="['btn', `btn-${variant}`, `btn-${size}`, { block, loading }]"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
  >
    <span v-if="loading" class="busy" aria-hidden="true"></span>
    <slot />
  </button>
</template>

<style scoped>
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  border-radius: var(--r-control);
  border: 1px solid transparent;
  font-weight: 500;
  line-height: 1.2;
  white-space: nowrap;
  transition:
    background-color 140ms ease,
    border-color 140ms ease,
    color 140ms ease;
}

.btn-md {
  min-height: 30px;
  padding: 6px 13px;
  font-size: var(--fs-12);
  letter-spacing: 0.02em;
}

.btn-sm {
  min-height: 26px;
  padding: 3px 9px;
  font-size: var(--fs-11);
  letter-spacing: 0.02em;
}

.btn.block {
  display: flex;
  width: 100%;
}

.btn-primary {
  background: var(--primary-bg);
  color: var(--primary-text);
}

.btn-primary:hover:not(:disabled) {
  background: var(--primary-bg-hover);
}

.btn-secondary {
  background: transparent;
  border-color: var(--line-strong);
  color: var(--text);
}

.btn-secondary:hover:not(:disabled) {
  background: var(--surface-raised);
  border-color: var(--accent-line);
}

.btn-ghost {
  background: transparent;
  color: var(--text-2);
}

.btn-ghost:hover:not(:disabled) {
  background: var(--surface-raised);
  color: var(--text);
}

.btn-danger {
  background: transparent;
  border-color: rgba(214, 141, 132, 0.4);
  color: var(--failed);
}

.btn-danger:hover:not(:disabled) {
  background: var(--failed-fill);
}

.btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.btn.loading {
  cursor: progress;
  opacity: 0.8;
}

.busy {
  width: 10px;
  height: 10px;
  border: 1.5px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: turn 700ms linear infinite;
}

@keyframes turn {
  to {
    transform: rotate(360deg);
  }
}
</style>
