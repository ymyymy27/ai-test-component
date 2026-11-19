<script setup lang="ts">
defineProps<{
  sections: Array<{ id: string; label: string }>;
  active: string;
}>();

const emit = defineEmits<{ jump: [id: string] }>();
</script>

<template>
  <nav class="settings-index" aria-label="设置分区">
    <button
      v-for="section in sections"
      :key="section.id"
      type="button"
      :class="{ 'is-active': active === section.id }"
      :aria-current="active === section.id ? 'location' : undefined"
      @click="emit('jump', section.id)"
    >
      {{ section.label }}
    </button>
  </nav>
</template>
