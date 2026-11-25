<script setup lang="ts">
import { ref } from "vue";

import { useWorkspaceStore } from "@/stores/workspace";

defineProps<{
  active: boolean;
}>();

const workspace = useWorkspaceStore();
const envBusy = ref(false);

async function refreshEnvironment(): Promise<void> {
  envBusy.value = true;
  try {
    await workspace.refreshEnvironment();
  } finally {
    envBusy.value = false;
  }
}
</script>

<template>
  <section
    v-show="active"
    class="settings-section"
    id="settings-environment"
    role="tabpanel"
    aria-labelledby="settings-tab-settings-environment"
  >
    <div class="settings-head">
      <div>
        <h2 class="settings-title">环境自检</h2>
        <p class="settings-desc">缺失只影响对应能力，本地记录与报告仍可用。</p>
      </div>
      <button
        class="btn btn-sm"
        type="button"
        :disabled="envBusy"
        :aria-busy="envBusy"
        @click="refreshEnvironment"
      >
        {{ envBusy ? "正在检查…" : "重新检查" }}
      </button>
    </div>
    <div v-if="workspace.environment.length" class="env-list">
      <div v-for="check in workspace.environment" :key="check.key" class="env-row">
        <span class="env-name">{{ check.name }}</span>
        <span class="env-value">{{ check.value }}</span>
        <span class="env-state" :class="{ 'is-missing': !check.ok }">
          {{ check.ok ? "通过" : "缺失" }}
        </span>
        <span v-if="!check.ok && (check.detail || check.repair)" class="env-detail">
          {{ check.detail }}<template v-if="check.repair"> · {{ check.repair }}</template>
        </span>
      </div>
    </div>
    <p v-else class="doc-note">还没有检查结果；点“重新检查”读取当前环境。</p>
  </section>
</template>
