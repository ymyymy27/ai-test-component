<script setup lang="ts">
import { nextTick, ref, watch } from "vue";

import AppButton from "@/components/AppButton.vue";
import Icon from "@/components/Icon.vue";
import {
  addProjectByPath,
  closeAddProject,
  isBusy,
  session,
} from "@/state/session";

const pathValue = ref("");
const touched = ref(false);
const input = ref<HTMLInputElement | null>(null);

watch(
  () => session.addProjectOpen,
  async (open) => {
    if (!open) {
      return;
    }
    pathValue.value = "";
    touched.value = false;
    await nextTick();
    input.value?.focus();
  },
);

async function submit(): Promise<void> {
  touched.value = true;
  const root = pathValue.value.trim();
  if (!root) {
    input.value?.focus();
    return;
  }
  if (await addProjectByPath(root)) {
    closeAddProject();
  }
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    closeAddProject();
  }
}
</script>

<template>
  <div
    v-if="session.addProjectOpen"
    class="scrim"
    @click.self="closeAddProject"
    @keydown="onKeydown"
  >
    <section
      class="dialog"
      role="dialog"
      aria-modal="true"
      aria-label="添加项目"
      data-testid="add-project-dialog"
    >
      <header class="dialog-head">
        <h2>添加项目</h2>
        <button type="button" class="close" aria-label="关闭" @click="closeAddProject">
          <Icon name="close" :size="14" />
        </button>
      </header>

      <form class="dialog-body" @submit.prevent="submit">
        <label class="field">
          <span class="label">项目根目录</span>
          <input
            ref="input"
            v-model="pathValue"
            class="input mono"
            type="text"
            placeholder="例如 E:\work\order-service"
            spellcheck="false"
            autocomplete="off"
            data-testid="add-project-path"
            :aria-invalid="touched && !pathValue.trim()"
            @blur="touched = true"
          />
          <span v-if="touched && !pathValue.trim()" class="error-text">
            请填写项目根目录的绝对路径
          </span>
          <span v-else class="hint">目录需要存在且可读。</span>
        </label>

        <div class="row">
          <AppButton html-type="submit" variant="primary" :loading="isBusy('projectAdd')">
            添加
          </AppButton>
          <AppButton variant="ghost" @click="closeAddProject">取消</AppButton>
        </div>
      </form>
    </section>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: grid;
  place-items: center;
  padding: var(--space-4);
  background: var(--scrim);
}

.dialog {
  width: min(420px, 100%);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-pop);
}

.dialog-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--line);
}

.dialog-head h2 {
  font-size: var(--fs-14);
  font-weight: 600;
}

.close {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  background: none;
  border: 1px solid transparent;
  border-radius: var(--r-control);
  color: var(--text-3);
}

.close:hover {
  background: var(--surface-raised);
  border-color: var(--line);
  color: var(--text);
}

.dialog-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.label {
  font-size: var(--fs-12);
  color: var(--text-2);
  font-weight: 500;
}

.input {
  width: 100%;
  min-height: 34px;
  padding: 6px 10px;
  background: var(--surface-sunken);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-13);
}

.input:focus {
  border-color: var(--accent-line);
  outline: none;
  box-shadow: 0 0 0 2px var(--accent-glow);
}
</style>
