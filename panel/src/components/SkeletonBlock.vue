<script setup lang="ts">
withDefaults(defineProps<{ lines?: number; label?: string }>(), {
  lines: 3,
  label: "正在读取",
});
</script>

<template>
  <div class="skeleton" role="status" :aria-label="label">
    <span
      v-for="line in lines"
      :key="line"
      class="line"
      :class="{ short: line === lines && lines > 1 }"
    ></span>
  </div>
</template>

<style scoped>
.skeleton {
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: var(--space-4);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
}

.line {
  height: 10px;
  border-radius: 3px;
  background: linear-gradient(90deg, #1d2226 0%, #232a2f 50%, #1d2226 100%);
  background-size: 200% 100%;
  animation: sweep 1.4s ease-in-out infinite;
}

.line.short {
  width: 46%;
}

@keyframes sweep {
  to {
    background-position: -200% 0;
  }
}
</style>
