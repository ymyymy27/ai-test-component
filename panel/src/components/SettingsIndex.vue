<script setup lang="ts">
defineProps<{
  sections: Array<{ id: string; label: string }>;
  active: string;
}>();

const emit = defineEmits<{ jump: [id: string] }>();

function jump(id: string, event: MouseEvent): void {
  const target = event.currentTarget;
  if (target instanceof HTMLElement) {
    target.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  }
  emit("jump", id);
}
</script>

<template>
  <nav class="settings-index" aria-label="设置分区">
    <button
      v-for="section in sections"
      :key="section.id"
      type="button"
      :class="{ 'is-active': active === section.id }"
      :aria-current="active === section.id ? 'location' : undefined"
      @click="jump(section.id, $event)"
    >
      {{ section.label }}
    </button>
  </nav>
</template>
