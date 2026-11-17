<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";

import Icon from "./Icon.vue";

const props = withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    note?: string;
  }>(),
  { note: "" },
);

const emit = defineEmits<{ close: [] }>();

const panel = ref<HTMLElement | null>(null);
let restoreFocus: HTMLElement | null = null;
let bodyLocked = false;

function lockBody(): void {
  if (!bodyLocked) {
    document.body.style.overflow = "hidden";
    bodyLocked = true;
  }
}

function unlockBody(): void {
  if (bodyLocked) {
    document.body.style.overflow = "";
    bodyLocked = false;
  }
}

function close(): void {
  emit("close");
}

function onKeydown(event: KeyboardEvent): void {
  if (!props.open) {
    return;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    close();
    return;
  }
  if (event.key !== "Tab" || !panel.value) {
    return;
  }
  // 抽屉是模态层：焦点不离开面板。
  const focusable = panel.value.querySelectorAll<HTMLElement>(
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
  );
  if (focusable.length === 0) {
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement as HTMLElement | null;
  if (event.shiftKey && (active === first || !panel.value.contains(active))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      restoreFocus = document.activeElement as HTMLElement | null;
      document.addEventListener("keydown", onKeydown);
      lockBody();
      await nextTick();
      const target = panel.value?.querySelector<HTMLElement>(
        "input, select, textarea, button",
      );
      target?.focus();
    } else {
      document.removeEventListener("keydown", onKeydown);
      unlockBody();
      restoreFocus?.focus();
      restoreFocus = null;
    }
  },
);

onBeforeUnmount(() => {
  document.removeEventListener("keydown", onKeydown);
  unlockBody();
});
</script>

<template>
  <div v-if="open" class="scrim" @click.self="close">
    <aside
      ref="panel"
      class="drawer"
      role="dialog"
      aria-modal="true"
      :aria-label="title"
    >
      <header class="drawer-head">
        <div class="drawer-copy">
          <span class="drawer-kicker">INSPECTOR</span>
          <h2>{{ title }}</h2>
          <p v-if="note" class="hint">{{ note }}</p>
        </div>
        <button type="button" class="close" aria-label="关闭" @click="close">
          <Icon name="close" :size="15" />
        </button>
      </header>
      <div class="drawer-body">
        <slot />
      </div>
      <footer v-if="$slots.footer" class="drawer-foot">
        <slot name="footer" />
      </footer>
    </aside>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
  justify-content: flex-end;
  background: var(--scrim);
  animation: fade 140ms ease-out;
}

.drawer {
  display: flex;
  flex-direction: column;
  width: min(var(--drawer-w), 100vw);
  height: 100%;
  background: var(--bg);
  border-left: 1px solid var(--line-strong);
  box-shadow: var(--shadow-pop);
  animation: slide 180ms cubic-bezier(0.16, 1, 0.3, 1);
}

.drawer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-4) var(--space-3);
  border-bottom: 1px solid var(--line);
}

.drawer-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.drawer-kicker {
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.2em;
  color: var(--accent);
}

.drawer-copy h2 {
  font-size: var(--fs-16);
}

.close {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: none;
  background: none;
  border: 1px solid transparent;
  border-radius: var(--r-control);
  color: var(--text-3);
}

.close:hover {
  color: var(--text);
  border-color: var(--line);
  background: var(--surface-raised);
}

.drawer-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-4);
}

.drawer-foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--line-strong);
  background: var(--bg);
}

@keyframes slide {
  from {
    transform: translateX(24px);
    opacity: 0;
  }
}

@keyframes fade {
  from {
    opacity: 0;
  }
}
</style>
