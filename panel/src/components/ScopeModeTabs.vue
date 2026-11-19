<script setup lang="ts">
defineProps<{
  mode: "recommended" | "custom" | "all";
  note: string;
}>();

const emit = defineEmits<{
  change: [mode: "recommended" | "custom" | "all"];
}>();

const modes: Array<{ value: "recommended" | "custom" | "all"; label: string }> = [
  { value: "recommended", label: "推荐范围" },
  { value: "custom", label: "自己勾选" },
  { value: "all", label: "全量" },
];

function select(mode: "recommended" | "custom" | "all"): void {
  emit("change", mode);
}
</script>

<template>
  <div class="scope-mode-bar" role="group" aria-label="范围选择方式">
    <button
      v-for="item in modes"
      :key="item.value"
      class="scope-mode"
      :class="{ 'is-active': mode === item.value }"
      type="button"
      :aria-pressed="mode === item.value"
      @click="select(item.value)"
    >
      {{ item.label }}
    </button>
    <span class="scope-mode-note">{{ note }}</span>
  </div>
</template>
