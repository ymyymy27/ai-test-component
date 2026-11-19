<script setup lang="ts">
import { nextTick, ref, watch } from "vue";

import type { Module, ModuleDependency } from "@/domain/types";
import type { ModuleInput } from "@/repositories/ProjectRepository";

const props = defineProps<{
  module: Module | null;
}>();

const emit = defineEmits<{
  save: [input: ModuleInput];
}>();

const open = defineModel<boolean>({ default: false });
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
  } else if (dialog.open) {
    dialog.close();
  }
});

function close(): void {
  open.value = false;
}

function submit(): void {
  if (!name.value.trim()) {
    error.value = "模块名称不能为空。";
    return;
  }
  emit("save", {
    name: name.value,
    responsibility: responsibility.value,
    owner: owner.value,
    status: status.value,
    dependencies: dependencies.value,
  });
  open.value = false;
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
        <label for="module-name">模块名</label>
        <input
          id="module-name"
          v-model="name"
          type="text"
          maxlength="40"
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
      <p class="field-hint">
        模块用于定位和筛选；改模块名称或责任不会使计划过期，改验收项所属模块会使计划过期。
      </p>
      <footer class="dialog-actions">
        <button class="btn" type="button" @click="close">取消</button>
        <button class="btn btn-primary" type="submit">保存模块</button>
      </footer>
    </form>
  </dialog>
</template>
