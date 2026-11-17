<script setup lang="ts">
withDefaults(
  defineProps<{
    tone?: "ok" | "warn" | "error" | "idle" | "running";
    label?: string;
  }>(),
  { tone: "idle", label: "" },
);
</script>

<template>
  <span class="status" :class="`status-${tone}`">
    <span class="dot" aria-hidden="true"></span>
    <span v-if="label">{{ label }}</span>
  </span>
</template>

<style scoped>
.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-12);
  color: var(--text-2);
  white-space: nowrap;
}

.dot {
  width: 6px;
  height: 5px;
  border-radius: 50%;
  background: var(--neutral);
}

.status-ok .dot {
  background: var(--passed);
}

.status-warn .dot {
  background: var(--incomplete);
}

.status-error .dot {
  background: var(--failed);
}

.status-running .dot {
  background: var(--accent);
}
</style>
