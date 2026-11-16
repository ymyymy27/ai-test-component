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
  height: 6px;
  border-radius: 50%;
  background: var(--neutral);
  box-shadow: 0 0 0 3px rgba(154, 163, 171, 0.08);
}

.status-ok .dot {
  background: var(--passed);
  box-shadow: 0 0 0 3px rgba(134, 179, 148, 0.1);
}

.status-warn .dot {
  background: var(--incomplete);
  box-shadow: 0 0 0 3px rgba(201, 168, 111, 0.1);
}

.status-error .dot {
  background: var(--failed);
  box-shadow: 0 0 0 3px rgba(214, 141, 132, 0.12);
}

.status-running .dot {
  background: var(--accent);
  box-shadow: 0 0 0 3px rgba(168, 200, 224, 0.12);
  animation: pulse 1.6s ease-in-out infinite;
}

@keyframes pulse {
  50% {
    opacity: 0.45;
  }
}
</style>
