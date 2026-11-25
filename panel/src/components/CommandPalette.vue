<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";
import Icon from "./Icon.vue";

interface CommandEntry {
  id: string;
  group: string;
  label: string;
  hint?: string;
  keywords?: string;
  run: () => void | Promise<void>;
}

const router = useRouter();
const ui = useUiStore();
const workspace = useWorkspaceStore();

const inputRef = ref<HTMLInputElement | null>(null);
const query = ref("");
const activeIndex = ref(0);

const projectId = computed(() => workspace.currentProjectId ?? "");
const currentProject = computed(() => workspace.currentProject);

const allCommands = computed<CommandEntry[]>(() => {
  const entries: CommandEntry[] = [];
  const pid = projectId.value;

  if (pid) {
    entries.push(
      {
        id: "action:new-task",
        group: "快捷操作",
        label: "新建测试任务",
        hint: "向导",
        keywords: "new task create 测试 任务 新建",
        run: () => {
          ui.openNewTaskDrawer();
        },
      },
      {
        id: "action:quick-check",
        group: "快捷操作",
        label: "执行快速检查",
        hint: "L1 抽屉",
        keywords: "quick check l1 smoke 检查 快速",
        run: () => {
          ui.openQuickCheckDrawer();
        },
      },
    );
  }

  entries.push({
    id: "action:toggle-theme",
    group: "快捷操作",
    label: ui.resolvedTheme === "dark" ? "切换浅色晨曦主题" : "切换深色曜石主题",
    hint: "外观",
    keywords: "theme dark light 主题 深色 浅色",
    run: () => {
      ui.toggleTheme();
    },
  });

  if (workspace.mode === "host") {
    entries.push({
      id: "action:repair-env",
      group: "快捷操作",
      label: "诊断并修复运行环境",
      hint: "Doctor",
      keywords: "doctor repair env 修复 环境 自检",
      run: async () => {
        await workspace.repairEnvironment();
      },
    });
  } else {
    entries.push({
      id: "action:refresh-env",
      group: "快捷操作",
      label: "刷新运行环境自检",
      hint: "自检",
      keywords: "env refresh 环境 检查",
      run: async () => {
        await workspace.refreshEnvironment();
      },
    });
  }

  if (pid) {
    entries.push(
      {
        id: "nav:overview",
        group: "项目视图",
        label: "打开项目概览",
        hint: "概览",
        keywords: "overview 概览 首页",
        run: async () => {
          await router.push(`/projects/${pid}/overview`);
        },
      },
      {
        id: "nav:architecture",
        group: "项目视图",
        label: "打开全屏架构视图",
        hint: "代码图",
        keywords: "architecture graph topology 架构 调用链 代码图",
        run: async () => {
          await router.push(`/projects/${pid}/architecture`);
        },
      },
      {
        id: "nav:issues",
        group: "项目视图",
        label: "查看问题追踪",
        hint: "问题",
        keywords: "issues bugs 问题 缺陷",
        run: async () => {
          await router.push(`/projects/${pid}/issues`);
        },
      },
      {
        id: "nav:history",
        group: "项目视图",
        label: "查看历史运行记录",
        hint: "记录",
        keywords: "history runs reports 历史 运行 报告",
        run: async () => {
          await router.push(`/projects/${pid}/history`);
        },
      },
      {
        id: "nav:settings",
        group: "项目视图",
        label: "打开项目设置",
        hint: "设置",
        keywords: "settings config github 规则 设置 模型",
        run: async () => {
          await router.push(`/projects/${pid}/settings`);
        },
      },
    );

    const tasks = currentProject.value?.tasks ?? [];
    tasks.slice(0, 6).forEach((task) => {
      const target =
        task.stage === "result"
          ? `/projects/${pid}/tasks/${task.id}/result`
          : task.stage === "running"
            ? `/projects/${pid}/tasks/${task.id}/run`
            : task.plans.length > 0
              ? `/projects/${pid}/tasks/${task.id}/plan`
              : `/projects/${pid}/tasks/${task.id}/scope`;
      entries.push({
        id: `task:${task.id}`,
        group: "当前项目任务",
        label: task.title || "未命名任务",
        hint: task.goal || "打开任务",
        keywords: `task ${task.title} ${task.goal}`,
        run: async () => {
          await workspace.setActiveTask(pid, task.id);
          await router.push(target);
        },
      });
    });
  }

  workspace.projects
    .filter((entry) => !entry.archived && entry.id !== pid)
    .slice(0, 6)
    .forEach((project) => {
      entries.push({
        id: `project:${project.id}`,
        group: "切换项目",
        label: `切换到项目：${project.name}`,
        hint: project.root || "",
        keywords: `project switch ${project.name} ${project.root ?? ""}`,
        run: async () => {
          await workspace.setCurrentProject(project.id);
          await router.push(`/projects/${project.id}/overview`);
        },
      });
    });

  return entries;
});

const filteredCommands = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) {
    return allCommands.value;
  }
  return allCommands.value.filter((entry) => {
    const hay = `${entry.group} ${entry.label} ${entry.hint ?? ""} ${entry.keywords ?? ""}`.toLowerCase();
    return hay.includes(q);
  });
});

const groupedCommands = computed(() => {
  const groups = new Map<string, CommandEntry[]>();
  filteredCommands.value.forEach((entry) => {
    const list = groups.get(entry.group);
    if (list) {
      list.push(entry);
    } else {
      groups.set(entry.group, [entry]);
    }
  });
  return Array.from(groups.entries()).map(([name, items]) => ({ name, items }));
});

const flatIndexMap = computed(() => {
  const map = new Map<string, number>();
  filteredCommands.value.forEach((entry, index) => {
    map.set(entry.id, index);
  });
  return map;
});

watch(
  () => ui.commandPaletteOpen,
  async (open) => {
    if (!open) {
      return;
    }
    query.value = "";
    activeIndex.value = 0;
    await nextTick();
    inputRef.value?.focus();
  },
);

watch(filteredCommands, (list) => {
  if (list.length === 0) {
    activeIndex.value = 0;
  } else if (activeIndex.value >= list.length) {
    activeIndex.value = list.length - 1;
  }
});

async function executeEntry(entry: CommandEntry | undefined): Promise<void> {
  if (!entry) {
    return;
  }
  ui.closeCommandPalette();
  await entry.run();
}

function onInputKeydown(event: KeyboardEvent): void {
  const total = filteredCommands.value.length;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    if (total > 0) {
      activeIndex.value = (activeIndex.value + 1) % total;
    }
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    if (total > 0) {
      activeIndex.value = (activeIndex.value - 1 + total) % total;
    }
  } else if (event.key === "Enter") {
    event.preventDefault();
    void executeEntry(filteredCommands.value[activeIndex.value]);
  } else if (event.key === "Escape") {
    event.preventDefault();
    ui.closeCommandPalette();
  }
}
</script>

<template>
  <div
    v-if="ui.commandPaletteOpen"
    class="cmd-backdrop"
    role="presentation"
    @click.self="ui.closeCommandPalette()"
  >
    <div
      class="cmd-dialog"
      role="dialog"
      aria-modal="true"
      aria-label="快捷命令面板"
    >
      <div class="cmd-input-row">
        <Icon name="search" :size="16" />
        <input
          ref="inputRef"
          v-model="query"
          class="cmd-input"
          type="search"
          aria-label="搜索命令、项目或页面"
          placeholder="输入关键词跳转页面、新建测试、快速检查或切换主题…"
          autocomplete="off"
          @keydown="onInputKeydown"
        />
        <button
          class="btn btn-ghost btn-sm"
          type="button"
          aria-label="关闭命令面板"
          @click="ui.closeCommandPalette()"
        >
          Esc
        </button>
      </div>

      <div class="cmd-list" role="listbox" aria-label="可用命令">
        <p v-if="filteredCommands.length === 0" class="side-empty">
          没有匹配的命令，请换个关键词试试。
        </p>
        <template v-for="group in groupedCommands" :key="group.name">
          <div class="cmd-group-label">{{ group.name }}</div>
          <button
            v-for="entry in group.items"
            :key="entry.id"
            class="cmd-item"
            :class="{ 'is-active': flatIndexMap.get(entry.id) === activeIndex }"
            type="button"
            role="option"
            :aria-selected="flatIndexMap.get(entry.id) === activeIndex"
            @mouseenter="activeIndex = flatIndexMap.get(entry.id) ?? 0"
            @click="executeEntry(entry)"
          >
            <span>{{ entry.label }}</span>
            <span v-if="entry.hint" class="cmd-item-sub">{{ entry.hint }}</span>
          </button>
        </template>
      </div>
    </div>
  </div>
</template>
