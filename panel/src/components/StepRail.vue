<script setup lang="ts">
import Icon from "./Icon.vue";

defineProps<{
  steps: { title: string; hint?: string }[];
  current: number;
}>();

defineEmits<{ select: [index: number] }>();
</script>

<template>
  <nav class="rail" aria-label="新建测试步骤">
    <ol>
      <li
        v-for="(step, index) in steps"
        :key="step.title"
        :class="{
          done: index + 1 < current,
          active: index + 1 === current,
          future: index + 1 > current,
        }"
      >
        <button
          type="button"
          class="step"
          :disabled="index + 1 > current"
          :aria-current="index + 1 === current ? 'step' : undefined"
          @click="$emit('select', index + 1)"
        >
          <span class="marker" aria-hidden="true">
            <Icon v-if="index + 1 < current" name="check" :size="11" />
            <template v-else>{{ index + 1 }}</template>
          </span>
          <span class="step-copy">
            <span class="step-title">{{ step.title }}</span>
            <span v-if="step.hint" class="step-hint">{{ step.hint }}</span>
          </span>
        </button>
      </li>
    </ol>
  </nav>
</template>

<style scoped>
.rail ol {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.rail li {
  position: relative;
}

.rail li:not(:last-child)::after {
  content: "";
  position: absolute;
  left: 12px;
  top: 30px;
  bottom: -2px;
  width: 1px;
  background: var(--line);
}

.step {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 4px 6px 4px 0;
  background: none;
  border: 0;
  text-align: left;
  color: var(--text-3);
  border-radius: var(--r-control);
}

.step:disabled {
  cursor: default;
}

.marker {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  flex: none;
  border-radius: 50%;
  border: 1px solid var(--line-strong);
  background: var(--surface);
  color: var(--text-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.step-copy {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding-top: 2px;
}

.step-title {
  font-size: var(--fs-13);
  color: var(--text-3);
}

.step-hint {
  font-size: var(--fs-12);
  color: var(--text-disabled);
}

.done .marker {
  border-color: rgba(134, 179, 148, 0.4);
  color: var(--passed);
  background: var(--passed-fill);
}

.done .step-title {
  color: var(--text-2);
}

.done .step:hover {
  background: var(--surface-raised);
}

.active .marker {
  border-color: var(--accent-line);
  color: var(--accent);
  background: var(--accent-fill);
}

.active .step-title {
  color: var(--text);
  font-weight: 600;
}

@media (max-width: 719px) {
  .rail ol {
    flex-direction: row;
    gap: var(--space-1);
  }

  .rail li {
    flex: 1 1 0;
  }

  .rail li:not(:last-child)::after {
    left: 50%;
    right: -50%;
    top: 12px;
    bottom: auto;
    width: auto;
    height: 1px;
  }

  .step {
    justify-content: center;
    flex-direction: column;
    align-items: center;
    gap: 5px;
    padding: 0;
  }

  .step-copy {
    align-items: center;
    padding-top: 0;
  }

  .step-hint {
    display: none;
  }

  .step-title {
    font-size: var(--fs-12);
  }
}
</style>
