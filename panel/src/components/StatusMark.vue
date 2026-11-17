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
  gap: 5px;
  font-size: var(--fs-12);
  color: var(--text-2);
  white-space: nowrap;
}

.glyph {
  width: 9px;
  text-align: center;
  font-size: var(--fs-11);
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
