<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, useRoute, useRouter } from "vue-router";

import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import SettingsIndex from "@/components/SettingsIndex.vue";
import SettingsConfigSection from "@/components/settings/SettingsConfigSection.vue";
import SettingsEnvironmentSection from "@/components/settings/SettingsEnvironmentSection.vue";
import SettingsGithubSection from "@/components/settings/SettingsGithubSection.vue";
import SettingsProfileSection from "@/components/settings/SettingsProfileSection.vue";
import SettingsRulesSection from "@/components/settings/SettingsRulesSection.vue";
import SettingsSystemSections from "@/components/settings/SettingsSystemSections.vue";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const route = useRoute();
const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();
const projectId = computed(() => String(route.params.projectId ?? ""));
const project = computed(() => workspace.currentProject);

const sections = [
  { id: "settings-profile", label: "项目资料" },
  { id: "settings-config", label: "运行配置" },
  { id: "settings-environment", label: "环境自检" },
  { id: "settings-rules", label: "项目规则" },
  { id: "settings-github", label: "GitHub 变更" },
  { id: "settings-appearance", label: "外观" },
  { id: "settings-backup", label: "备份" },
];
const activeSection = ref(
  sections.find((section) => section.id === route.query.section)?.id ?? sections[0]?.id ?? "",
);
const boundaryExpanded = ref(route.query.section === "settings-boundary");
const configDirty = ref(false);

async function jumpToBoundaryNote(): Promise<void> {
  boundaryExpanded.value = true;
  await nextTick();
  document.getElementById("settings-boundary")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

onMounted(async () => {
  if (route.query.section === "settings-boundary") {
    void jumpToBoundaryNote();
  }
  if (workspace.environment.length === 0) {
    await workspace.refreshEnvironment();
  }
});

watch(
  () => route.query.section,
  (value) => {
    if (value === "settings-boundary") {
      void jumpToBoundaryNote();
      return;
    }
    const next = sections.find((section) => section.id === value);
    if (next) {
      activeSection.value = next.id;
    }
  },
);

onBeforeRouteLeave(async () => {
  if (!configDirty.value) {
    return true;
  }
  const answer = await ui.askConfirm({
    title: "离开设置页？",
    body: "运行配置还有未保存的修改，离开后会丢失。",
    confirmLabel: "放弃修改并离开",
    tone: "danger",
  });
  return answer !== null;
});

async function selectSection(id: string): Promise<void> {
  activeSection.value = id;
  if (route.query.section !== id) {
    void router.replace({ query: { ...route.query, section: id } });
  }
  // 分区导航只切 tabpanel 不移动焦点；读屏会以为还停在导航上。
  await nextTick();
  const panel = document.getElementById(id);
  if (panel) {
    panel.setAttribute("tabindex", "-1");
    panel.focus();
  }
}
</script>

<template>
  <div v-if="!project" class="page">
    <ErrorState
      title="项目不可用"
      text="回到项目概览重新选择项目。"
      retry-label="回到概览"
      @retry="router.push(projectId ? `/projects/${projectId}/overview` : '/')"
    />
  </div>
  <div v-else class="page page-data">
    <PageHeader title="设置" :subtitle="`${project.name} · 环境、规则与运行配置`">
      <template #actions>
        <button
          class="btn btn-sm settings-back"
          type="button"
          @click="router.push(`/projects/${projectId}/overview`)"
        >
          回到项目概览
        </button>
      </template>
    </PageHeader>

    <p class="doc-note">
      {{
        workspace.mode === "host"
          ? "工作空间由宿主 CLI 管理；面板不直接写业务文件。凭据不写入配置、日志、报告或导出。"
          : "工作空间位于浏览器本地存储，项目仓库内不留插件数据。凭据不写入配置、日志、报告或导出。"
      }}
    </p>

    <div class="settings-layout">
      <SettingsIndex :sections="sections" :active="activeSection" @jump="selectSection" />
      <div class="settings-content">
        <SettingsProfileSection :project="project" :active="activeSection === 'settings-profile'" />
        <SettingsConfigSection
          :project="project"
          :active="activeSection === 'settings-config'"
          @dirty-change="configDirty = $event"
        />
        <SettingsEnvironmentSection :active="activeSection === 'settings-environment'" />
        <SettingsRulesSection :project="project" :active="activeSection === 'settings-rules'" />
        <SettingsGithubSection :project="project" :active="activeSection === 'settings-github'" />
        <SettingsSystemSections
          :active-section="activeSection"
          :boundary-open="boundaryExpanded"
          @toggle-boundary="boundaryExpanded = $event"
        />
      </div>
    </div>
  </div>
</template>
