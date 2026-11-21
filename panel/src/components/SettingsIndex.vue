<script setup lang="ts">
import { ref } from "vue";

const props = defineProps<{
  sections: Array<{ id: string; label: string }>;
  active: string;
}>();

const emit = defineEmits<{ jump: [id: string] }>();
const buttons = ref<HTMLButtonElement[]>([]);

function select(id: string): void {
  emit("jump", id);
}

function onKeydown(event: KeyboardEvent, index: number): void {
  const count = props.sections.length;
  let next = index;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") {
    next = (index + 1) % count;
  } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
    next = (index - 1 + count) % count;
  } else if (event.key === "Home") {
    next = 0;
  } else if (event.key === "End") {
    next = count - 1;
  } else {
    return;
  }
  event.preventDefault();
  const section = props.sections[next];
  if (!section) {
    return;
  }
  select(section.id);
  buttons.value[next]?.focus();
}
</script>

<template>
  <div class="settings-index" role="tablist" aria-label="设置分区" aria-orientation="vertical">
    <button
      v-for="(section, index) in sections"
      :id="`settings-tab-${section.id}`"
      :key="section.id"
      ref="buttons"
      type="button"
      role="tab"
      :class="{ 'is-active': active === section.id }"
      :aria-selected="active === section.id"
      :aria-controls="section.id"
      :tabindex="active === section.id ? 0 : -1"
      @click="select(section.id)"
      @keydown="onKeydown($event, index)"
    >
      {{ section.label }}
    </button>
  </div>
</template>
