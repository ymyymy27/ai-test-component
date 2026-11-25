<script setup lang="ts">
import { computed, ref, watch } from "vue";

import FormField from "@/components/FormField.vue";
import type { Project } from "@/domain/types";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const props = defineProps<{
  project: Project;
  active: boolean;
}>();

const workspace = useWorkspaceStore();
const ui = useUiStore();
const profileName = ref("");
const profileGoal = ref("");
const profileError = ref("");
const savedStamp = ref("");
const busy = computed(() => workspace.busyKey(`project:${props.project.id}`));

watch(
  () => props.project.id,
  () => {
    profileName.value = props.project.name;
    profileGoal.value = props.project.goal;
    profileError.value = "";
    savedStamp.value = "";
  },
  { immediate: true },
);

async function saveProfile(): Promise<void> {
  const name = profileName.value.trim();
  if (!name || name.length > 40) {
    profileError.value = "项目名不能为空，且最多 40 个字。";
    document.querySelector<HTMLInputElement>("#project-name")?.focus();
    return;
  }
  profileError.value = "";
  try {
    await workspace.updateProject(props.project.id, { name, goal: profileGoal.value });
    savedStamp.value = new Date().toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    });
    ui.showToast("项目资料已保存。", "success");
  } catch {
    // store 已提示。
  }
}
</script>

<template>
  <section
    v-show="active"
    class="settings-section"
    id="settings-profile"
    role="tabpanel"
    aria-labelledby="settings-tab-settings-profile"
  >
    <div class="settings-head">
      <div>
        <h2 class="settings-title">项目资料</h2>
        <p class="settings-desc">名称与目标可改；目录登记后固定。</p>
      </div>
    </div>
    <form class="settings-form" novalidate @submit.prevent="saveProfile">
      <div class="form-grid">
        <FormField
          id="project-name"
          label="项目名"
          required
          :error="profileError"
          v-slot="{ inputId, describedBy, invalid }"
        >
          <input
            :id="inputId"
            v-model="profileName"
            type="text"
            maxlength="40"
            :aria-invalid="invalid"
            :aria-describedby="describedBy"
            @input="profileError = ''"
          />
        </FormField>
        <FormField id="project-root" label="项目目录" hint="项目登记后路径不可修改。">
          <input id="project-root" :value="project.root" type="text" disabled />
        </FormField>
      </div>
      <FormField id="project-goal" label="项目目标" optional="可随时修订">
        <textarea
          id="project-goal"
          v-model="profileGoal"
          rows="3"
          placeholder="这个项目要解决什么"
        />
      </FormField>
      <div class="form-actions">
        <button class="btn btn-primary" type="submit" :disabled="busy" :aria-busy="busy">
          {{ busy ? "正在保存…" : "保存项目资料" }}
        </button>
        <span v-if="savedStamp" class="save-stamp">已于 {{ savedStamp }} 保存</span>
      </div>
    </form>
  </section>
</template>
