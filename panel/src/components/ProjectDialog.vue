<script setup lang="ts">
import { ref } from "vue";

import { addProject, closeAddProject, workspace } from "@/state/workspace";

const root = ref(workspace.prefillRoot);
const name = ref("");
const localError = ref("");

async function submit(): Promise<void> {
  localError.value = "";
  if (!root.value.trim()) {
    localError.value = "先填写项目目录。";
    return;
  }
  const ok = await addProject(root.value.trim(), name.value.trim() || undefined);
  if (ok) {
    closeAddProject();
  }
}
</script>

<template>
  <div class="ui-sheet" role="dialog" aria-modal="true" aria-labelledby="add-project-title">
    <header class="ui-sheet-head">
      <strong id="add-project-title">添加项目</strong>
      <button type="button" class="ui-link" @click="closeAddProject">关闭</button>
    </header>
    <div class="ui-sheet-body dialog-body">
      <p class="ui-help">填写要验证的项目目录。项目根目录不能和已登记项目互相包含。</p>
      <label class="field">
        <span class="ui-label">项目目录</span>
        <input v-model="root" class="ui-input" placeholder="例如 E:\work\order-service" autofocus />
      </label>
      <label class="field">
        <span class="ui-label">名称（可选）</span>
        <input v-model="name" class="ui-input" placeholder="不填就用目录名" />
      </label>
      <p v-if="localError" class="ui-error">{{ localError }}</p>
      <p v-if="workspace.bootError" class="ui-error">
        {{ workspace.bootError.title }}。{{ workspace.bootError.next }}
      </p>
      <div class="actions">
        <button type="button" class="ui-btn" @click="closeAddProject">取消</button>
        <button
          type="button"
          class="ui-btn primary"
          :disabled="Boolean(workspace.loading.projectAdd)"
          @click="submit"
        >
          {{ workspace.loading.projectAdd ? "正在登记…" : "登记项目" }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-5);
}

.field {
  display: block;
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-2);
}
</style>
