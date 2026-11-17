<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    tone: "passed" | "failed" | "incomplete" | "neutral" | "running";
    label: string;
  }>(),
  { tone: "neutral" },
);

const glyph = computed(() => {
  switch (props.tone) {
    case "passed":
      return "✓";
    case "failed":
      return "✗";
    case "incomplete":
      return "!";
    case "running":
      return ">";
    default:
      return "·";
  }
});
</script>

<template>
  <span class="status" :class="`tone-${tone}`">
    <span class="glyph" aria-hidden="true">{{ glyph }}</span>
    <span class="label">{{ label }}</span>
  </span>
</template>

<style scoped>
.status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.04em;
  color: var(--text-2);
  white-space: nowrap;
}

.glyph {
  width: 10px;
  text-align: center;
}

.tone-passed {
  color: var(--passed);
}

.tone-failed {
  color: var(--failed);
}

.tone-incomplete {
  color: var(--incomplete);
}

.tone-running {
  color: var(--accent);
}

.tone-neutral {
  color: var(--text-3);
}
</style>
