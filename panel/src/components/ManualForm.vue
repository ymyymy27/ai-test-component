<script setup lang="ts">
import { computed, ref } from "vue";

import { api } from "@/api/commands";
import { describeError } from "@/api/errors";
import { CASE_RESULT_LABEL, methodLabel } from "@/api/labels";
import { workspace, loadRun } from "@/state/workspace";

const props = defineProps<{ caseId: string }>();
const emit = defineEmits<{ close: []; saved: [] }>();

const step = ref("");
const result = ref<"passed" | "failed" | "blocked">("passed");
const note = ref("");
const evidence = ref("");
const busy = ref(false);
const error = ref("");

const plan = computed(() => workspace.run?.plan ?? workspace.plan);
const planCase = computed(() =>
  plan.value?.cases.find((item) => item.case_id === props.caseId),
);
const runCase = computed(() =>
  workspace.run?.run.cases.find((item) => item.case_id === props.caseId),
);
const title = computed(() => planCase.value?.title || "人工检查");

async function submit(): Promise<void> {
  if (!workspace.project || !workspace.run) {
    return;
  }
  if (!step.value.trim()) {
    error.value = "先写一下这次记录的是哪一步。";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await api.recordManual(
      workspace.project.project_id,
      workspace.run.run.run_id,
      props.caseId,
      step.value.trim(),
      result.value,
      note.value.trim(),
      evidence.value.trim() ? [evidence.value.trim()] : [],
    );
    await loadRun(workspace.run.run.run_id);
    emit("saved");
    emit("close");
  } catch (failure) {
    const friendly = describeError(failure);
    error.value = `${friendly.title}。${friendly.next}`;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="ui-sheet" role="dialog" aria-modal="true" aria-labelledby="manual-title">
    <header class="ui-sheet-head">
      <strong id="manual-title">记录人工结果 · {{ title }}</strong>
      <button type="button" class="ui-link" @click="emit('close')">关闭</button>
    </header>
    <div class="ui-sheet-body form">
      <p class="ui-help">
        人工检查由人记录每一步的结果。未记录完的步骤仍保持“待人工”，不会自动算通过。
      </p>

      <div class="facts">
        <div>
          <span class="ui-label">当前结果</span>
          <span>{{ runCase ? CASE_RESULT_LABEL[runCase.result] : "待人工" }}</span>
        </div>
        <div>
          <span class="ui-label">检查方式</span>
          <span>{{ methodLabel(planCase?.verify_method) }}</span>
        </div>
      </div>

      <label class="field">
        <span class="ui-label">步骤</span>
        <input v-model="step" class="ui-input" placeholder="例如：打开收银台并提交订单" />
      </label>
      <label class="field">
        <span class="ui-label">结果</span>
        <select v-model="result" class="ui-select">
          <option value="passed">通过</option>
          <option value="failed">失败</option>
          <option value="blocked">阻塞</option>
        </select>
      </label>
      <label class="field">
        <span class="ui-label">备注（可选）</span>
        <textarea v-model="note" class="ui-textarea" placeholder="记下看到的现象或判断依据" />
      </label>
      <label class="field">
        <span class="ui-label">证据文件（可选）</span>
        <input v-model="evidence" class="ui-input" placeholder="例如 /abs/path/screenshot.png" />
      </label>

      <p v-if="error" class="ui-error">{{ error }}</p>
      <div class="actions">
        <button type="button" class="ui-btn" @click="emit('close')">取消</button>
        <button type="button" class="ui-btn primary" :disabled="busy" @click="submit">
          {{ busy ? "正在记录…" : "记录这一步" }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-4);
}

.facts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  padding: 8px 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  color: var(--text-2);
  font-size: var(--fs-11);
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
