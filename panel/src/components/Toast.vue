<script setup lang="ts">
import { useUiStore } from "@/stores/ui";

const ui = useUiStore();
</script>

<template>
  <div
    class="toast"
    :class="{ 'is-visible': ui.toastVisible, 'is-error': ui.toast?.tone === 'error' }"
    role="status"
    aria-live="polite"
  >
    {{ ui.toast?.message ?? "" }}
  </div>
</template>
