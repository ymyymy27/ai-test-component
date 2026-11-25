<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";

import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";
import Icon from "./Icon.vue";
import NewTaskComposer from "./NewTaskComposer.vue";

const router = useRouter();
const ui = useUiStore();
const workspace = useWorkspaceStore();

const project = computed(() => workspace.currentProject);

async function onDone(taskId: string, routeTarget: "plan" | "scope"): Promise<void> {
  const pid = project.value?.id;
  ui.closeNewTaskDrawer();
  if (!pid) {
    return;
  }
  await router.push(`/projects/${pid}/tasks/${taskId}/${routeTarget}`);
}
</script>

<template>
  <div
    v-if="ui.newTaskDrawerOpen"
    class="drawer-backdrop"
    role="presentation"
    @click.self="ui.closeNewTaskDrawer()"
  >
    <aside
      class="drawer-panel is-wide"
      role="dialog"
      aria-modal="true"
      aria-label="新建测试"
    >
      <div class="drawer-head">
        <div>
          <h2 class="drawer-title">新建测试</h2>
          <p class="drawer-sub">
            {{ project ? project.name : "请先选择一个项目" }}
          </p>
        </div>
        <button
          class="btn btn-ghost btn-sm"
          type="button"
          aria-label="关闭新建测试抽屉"
          @click="ui.closeNewTaskDrawer()"
        >
          <Icon name="close" :size="15" />
        </button>
      </div>

      <div class="drawer-body">
        <NewTaskComposer
          v-if="project"
          :project="project"
          in-drawer
          @done="onDone"
          @cancel="ui.closeNewTaskDrawer()"
        />
        <p v-else class="side-empty">请先在左侧新建或打开一个本地项目。</p>
      </div>
    </aside>
  </div>
</template>
