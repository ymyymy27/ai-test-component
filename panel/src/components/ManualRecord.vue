<script setup lang="ts">
import { ref } from "vue";

import { LAYER_LABEL, methodLabel } from "@/api/labels";
import type { PlanCase, RunCase } from "@/api/types";
import AppButton from "@/components/AppButton.vue";
import { recordManual } from "@/state/session";

const props = defineProps<{
  runCase: RunCase;
  planCase?: PlanCase;
  itemText?: string;
}>();

const open = ref(false);
const result = ref<"passed" | "failed" | "blocked">("passed");
const note = ref("");
const saving = ref(false);
const error = ref("");

async function submit(): Promise<void> {
  error.value = "";
  saving.value = true;
  try {
    const saved = await recordManual(props.runCase.case_id, result.value, note.value.trim());
    if (saved) {
      open.value = false;
    } else {
      error.value = "记录没有保存成功，请查看顶部提示后重试。";
    }
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="manual-row">
    <div class="manual-head">
      <span class="manual-title">{{ planCase?.title || planCase?.expected || "待人工记录" }}</span>
      <span class="manual-meta">
        <template v-if="planCase?.layer">{{ LAYER_LABEL[planCase.layer] }}</template>
        <template v-if="planCase?.verify_method">
          · {{ methodLabel(planCase.verify_method) }}
        </template>
      </span>
      <AppButton v-if="!open" size="sm" variant="secondary" @click="open = true">
        记录结果
      </AppButton>
    </div>
    <p v-if="itemText" class="manual-item">验收标准：{{ itemText }}</p>

    <form v-if="open" class="manual-form" @submit.prevent="submit">
      <label class="manual-field">
        <span>结果</span>
        <select v-model="result" aria-label="人工结果">
          <option value="passed">通过</option>
          <option value="failed">失败</option>
          <option value="blocked">阻塞</option>
        </select>
      </label>
      <label class="manual-field note">
        <span>备注（可选）</span>
        <textarea
          v-model="note"
          class="input textarea"
          rows="2"
          aria-label="人工备注"
          placeholder="例如：本地手工核对，订单只出现一次"
        ></textarea>
      </label>
      <p v-if="error" class="manual-error" role="alert">{{ error }}</p>
      <div class="row">
        <AppButton html-type="submit" size="sm" variant="primary" :loading="saving">
          保存记录
        </AppButton>
        <AppButton size="sm" variant="ghost" @click="open = false">取消</AppButton>
      </div>
    </form>
  </div>
</template>

<style scoped>
.manual-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--line);
}

.manual-row:last-child {
  border-bottom: 0;
}

.manual-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.manual-title {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--fs-13);
  font-weight: 500;
  overflow-wrap: anywhere;
}

.manual-meta {
  font-size: var(--fs-11);
  color: var(--text-3);
}

.manual-item {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.manual-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
}

.manual-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--fs-11);
  color: var(--text-3);
}

.manual-field select,
.manual-field textarea {
  width: 100%;
  padding: 6px 8px;
  background: var(--surface);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-control);
  color: var(--text);
  font-size: var(--fs-13);
}

.manual-error {
  font-size: var(--fs-12);
  color: var(--failed);
}
</style>
