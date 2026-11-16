<script setup lang="ts">
type IconName =
  | "chevron-down"
  | "chevron-right"
  | "arrow-left"
  | "folder"
  | "settings"
  | "history"
  | "play"
  | "check"
  | "close"
  | "alert"
  | "info"
  | "clock"
  | "refresh"
  | "export"
  | "plus"
  | "layers"
  | "file";

withDefaults(defineProps<{ name: IconName; size?: number }>(), { size: 16 });
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <template v-if="name === 'chevron-down'">
      <path d="M6 9l6 6 6-6" />
    </template>
    <template v-else-if="name === 'chevron-right'">
      <path d="M9 6l6 6-6 6" />
    </template>
    <template v-else-if="name === 'arrow-left'">
      <path d="M19 12H5" />
      <path d="M11 18l-6-6 6-6" />
    </template>
    <template v-else-if="name === 'folder'">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </template>
    <template v-else-if="name === 'settings'">
      <path d="M4 7h8" />
      <path d="M18 7h2" />
      <path d="M4 17h4" />
      <path d="M14 17h6" />
      <circle cx="15" cy="7" r="2.5" />
      <circle cx="11" cy="17" r="2.5" />
    </template>
    <template v-else-if="name === 'history'">
      <path d="M4 12a8 8 0 1 0 2.5-5.8" />
      <path d="M4 5v4h4" />
      <path d="M12 8v5l3 2" />
    </template>
    <template v-else-if="name === 'play'">
      <path d="M8 5l11 7-11 7z" />
    </template>
    <template v-else-if="name === 'check'">
      <path d="M5 12l5 5L20 7" />
    </template>
    <template v-else-if="name === 'close'">
      <path d="M6 6l12 12" />
      <path d="M18 6L6 18" />
    </template>
    <template v-else-if="name === 'alert'">
      <path d="M12 4l9 16H3z" />
      <path d="M12 10v4" />
      <path d="M12 17.2v.2" />
    </template>
    <template v-else-if="name === 'info'">
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5" />
      <path d="M12 7.8v.2" />
    </template>
    <template v-else-if="name === 'clock'">
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </template>
    <template v-else-if="name === 'refresh'">
      <path d="M20 11a8 8 0 0 0-14-5L4 8" />
      <path d="M4 4v4h4" />
      <path d="M4 13a8 8 0 0 0 14 5l2-2" />
      <path d="M20 20v-4h-4" />
    </template>
    <template v-else-if="name === 'export'">
      <path d="M12 3.5v10" />
      <path d="M8 7.5l4-4 4 4" />
      <path d="M5 15v4.5h14V15" />
    </template>
    <template v-else-if="name === 'plus'">
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </template>
    <template v-else-if="name === 'layers'">
      <path d="M12 3.5l9 5-9 5-9-5z" />
      <path d="M3 13l9 5 9-5" />
    </template>
    <template v-else-if="name === 'file'">
      <path d="M7 3h7l4 4v14H7z" />
      <path d="M14 3v5h4" />
    </template>
  </svg>
</template>

<style scoped>
svg {
  display: block;
  flex: none;
}
</style>
