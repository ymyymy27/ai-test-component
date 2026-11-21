<script setup lang="ts">
import { nextTick, ref, watch } from "vue";

import type { Module, ModuleDependency } from "@/domain/types";
import type { ModuleInput } from "@/repositories/ProjectRepository";
import { useUiStore } from "@/stores/ui";

const props = defineProps<{
  module: Module | null;
}>();

const emit = defineEmits<{
  save: [input: ModuleInput];
}>();

const open = defineModel<boolean>({ default: false });
const ui = useUiStore();
const dialogRef = ref<HTMLDialogElement | null>(null);
const name = ref("");
const responsibility = ref("");
const owner = ref("");
const status = ref("开发中");
const error = ref("");
const dependencies = ref<ModuleDependency[]>([]);

watch(open, async (value) => {
  const dialog = dialogRef.value;
  if (!dialog) {
    return;
  }
  if (value) {
    name.value = props.module?.name ?? "";
    responsibility.value = props.module?.responsibility ?? "";
    owner.value = props.module?.owner ?? "";
    status.value = props.module?.status ?? "开发中";
    dependencies.value = props.module?.dependencies ?? [];
    error.value = "";
    await nextTick();
    if (!dialog.open) {
      dialog.showModal();
    }
    dialog.querySelector<HTMLElement>("#module-name")?.focus();
  } else if (dialog.open) {
    dialog.close();
  }
});

function isDirty(): boolean {
  if (props.module) {
    return (
      name.value !== props.module.name ||
      responsibility.value !== props.module.responsibility ||
      owner.value !== props.module.owner ||
      status.value !== props.module.status
    );
  }
  return Boolean(name.value.trim() || responsibility.value.trim() || owner.value.trim());
}

async function close(): Promise<void> {
  if (isDirty()) {
    const answer = await ui.askConfirm({
      title: "放弃模块编辑？",
      body: "已填写的模块信息会丢失。",
      confirmLabel: "放弃",
      tone: "danger",
    });
    if (answer === null) {
      return;
    }
  }
  open.value = false;
}

function submit(): void {
  if (!name.value.trim()) {
    error.value = "模块名称不能为空。";
    return;
  }
  // 由父级保存成功后关闭；保存失败时保留输入。
  emit("save", {
    name: name.value,
    responsibility: responsibility.value,
    owner: owner.value,
    status: status.value,
    dependencies: dependencies.value,
  });
}

function onDialogClick(event: MouseEvent): void {
  if (event.target === dialogRef.value) {
    close();
  }
}
</script>

<template>
  <dialog
    ref="dialogRef"
    class="dialog"
    aria-labelledby="module-dialog-title"
    aria-describedby="module-dialog-desc"
    @cancel.prevent="close"
    @close="open = false"
    @click="onDialogClick"
  >
    <form class="dialog-form" novalidate @submit.prevent="submit">
      <header class="dialog-head">
        <h2 id="module-dialog-title" class="dialog-title">
          {{ module ? "编辑模块" : "新增模块" }}
        </h2>
        <button class="icon-btn" type="button" aria-label="关闭" @click="close">
          <svg
            viewBox="0 0 16 16"
            width="15"
            height="15"
            fill="none"
            stroke="currentColor"
            stroke-width="1.5"
          >
            <path d="M4 4l8 8M12 4l-8 8" stroke-linecap="round" />
          </svg>
        </button>
      </header>
      <div class="field" :class="{ 'has-error': error }">
        <label for="module-name">
          模块名 <span v-if="module" class="optional">创建后不可改</span><span v-else class="optional">必填</span>
        </label>
        <input
          id="module-name"
          v-model="name"
          :disabled="Boolean(module)"
          type="text"
          maxlength="40"
          :aria-required="module ? undefined : 'true'"
          :aria-invalid="error ? 'true' : undefined"
          :aria-describedby="error ? 'module-name-error' : undefined"
          @input="error = ''"
        />
        <p v-if="error" id="module-name-error" class="field-error" role="alert">{{ error }}</p>
      </div>
      <div class="field">
        <label for="module-responsibility">责任</label>
        <input id="module-responsibility" v-model="responsibility" type="text" maxlength="120" />
      </div>
      <div class="form-grid">
        <div class="field">
          <label for="module-owner">负责人</label>
          <input id="module-owner" v-model="owner" type="text" maxlength="40" />
        </div>
        <div class="field">
          <label for="module-status">状态</label>
          <select id="module-status" v-model="status">
            <option value="已识别">已识别</option>
            <option value="待确认">待确认</option>
            <option value="开发中">开发中</option>
            <option value="已完成">已完成</option>
          </select>
        </div>
      </div>
      <p id="module-dialog-desc" class="field-hint">
        模块用于定位和筛选；改模块名称或责任不会使计划过期，改验收项所属模块会使计划过期。
      </p>
      <footer class="dialog-actions">
        <button class="btn" type="button" @click="close">取消</button>
        <button class="btn btn-primary" type="submit">保存模块</button>
      </footer>
    </form>
  </dialog>
</template>
