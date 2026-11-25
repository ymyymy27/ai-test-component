<script setup lang="ts">
import { computed, ref } from "vue";

import type { CodeGraph, LocalGitInfo } from "@/domain/types";
import Icon from "./Icon.vue";

const props = withDefaults(
  defineProps<{
    localGit?: LocalGitInfo | null;
    codeGraph?: CodeGraph | null;
    dirtyFiles?: string[];
    refreshing?: boolean;
  }>(),
  {
    localGit: null,
    codeGraph: null,
    dirtyFiles: () => [],
    refreshing: false,
  },
);

const emit = defineEmits<{
  refresh: [];
  testAffected: [goalText: string];
  openSource: [file: string, line: number];
}>();

const expanded = ref(false);

const diffTotals = computed(() => {
  const rows = props.localGit?.diffStat ?? [];
  let additions = 0;
  let deletions = 0;
  for (const row of rows) {
    additions += row.additions;
    deletions += row.deletions;
  }
  return { files: rows.length, additions, deletions };
});

const dirtyCount = computed(() => {
  if (!props.localGit?.isGit) {
    return 0;
  }
  return (
    (props.localGit.stagedFiles?.length ?? 0) +
    (props.localGit.unstagedFiles?.length ?? 0) +
    (props.localGit.untrackedFiles?.length ?? 0)
  );
});

const affectedModules = computed(() => props.codeGraph?.impact.affectedModules ?? []);
const affectedRoutes = computed(() => props.codeGraph?.impact.affectedRoutes ?? []);
const hasImpact = computed(
  () => affectedModules.value.length > 0 || affectedRoutes.value.length > 0,
);

const firstChangedLineByFile = computed(() => {
  const map = new Map<string, number>();
  for (const hunk of props.localGit?.changedHunks ?? []) {
    if (hunk.path && !map.has(hunk.path)) {
      map.set(hunk.path, Math.max(1, hunk.start || 1));
    }
  }
  return map;
});

function triggerTestAffected(): void {
  const parts: string[] = [];
  if (affectedModules.value.length) {
    parts.push(`模块：${affectedModules.value.slice(0, 6).join("、")}`);
  }
  if (affectedRoutes.value.length) {
    parts.push(`接口：${affectedRoutes.value.slice(0, 6).join("、")}`);
  }
  const changed = props.localGit?.changedFiles?.slice(0, 5) ?? [];
  if (!parts.length && changed.length) {
    parts.push(`改动文件：${changed.join("、")}`);
  }
  const goalText = parts.length
    ? `回归验证近期 Git 变动受影响范围（${parts.join("；")}）`
    : "回归验证近期代码变更受影响功能";
  emit("testAffected", goalText);
}

function openFile(file: string): void {
  const line = firstChangedLineByFile.value.get(file) ?? 1;
  emit("openSource", file, line);
}
</script>

<template>
  <section class="git-bar" aria-label="本地 Git 状态栏">
    <div v-if="localGit?.isGit" class="git-bar-main">
      <div class="git-bar-facts">
        <span class="git-branch-pill">
          <Icon name="history" :size="13" />
          <strong>{{ localGit.detached ? "分离 HEAD" : localGit.branch || "HEAD" }}</strong>
          <code v-if="localGit.shortHead" class="mono">{{ localGit.shortHead }}</code>
        </span>
        <span v-if="localGit.ahead > 0 || localGit.behind > 0" class="badge is-neutral">
          ↑{{ localGit.ahead }} ↓{{ localGit.behind }}
        </span>
        <span class="badge" :class="localGit.dirty ? 'is-warn' : 'is-pass'">
          {{ localGit.dirty ? `工作区 ${dirtyCount} 处未提交` : "工作区干净" }}
        </span>
        <span v-if="diffTotals.files > 0" class="git-diff-stat">
          变更 {{ diffTotals.files }} 文件 ·
          <span class="is-add">+{{ diffTotals.additions }}</span>
          /
          <span class="is-del">-{{ diffTotals.deletions }}</span>
        </span>
        <span v-if="localGit.headSubject" class="git-commit-subject" :title="localGit.headSubject">
          {{ localGit.headSubject }}
        </span>
      </div>

      <div class="git-bar-actions">
        <span v-if="dirtyFiles.length > 0" class="badge is-warn">
          源码已保存更新（{{ dirtyFiles.length }} 个文件）
        </span>
        <button
          v-if="(localGit.changedFiles?.length ?? 0) > 0"
          class="link-btn"
          type="button"
          :aria-expanded="expanded"
          @click="expanded = !expanded"
        >
          {{ expanded ? "收起变动" : `变动文件 ${localGit.changedFiles?.length ?? 0}` }}
        </button>
        <button
          class="btn btn-sm btn-ghost"
          type="button"
          :disabled="refreshing"
          @click="emit('refresh')"
        >
          {{ refreshing ? "刷新中…" : dirtyFiles.length > 0 ? "刷新代码图" : "同步 Git" }}
        </button>
        <button
          v-if="hasImpact"
          class="btn btn-sm"
          type="button"
          @click="triggerTestAffected"
        >
          测受影响范围
        </button>
      </div>
    </div>

    <div v-else class="git-bar-main is-nongit">
      <div class="git-bar-facts">
        <span class="muted">未检测到 Git 仓库；仅基于静态代码图分析项目结构。</span>
        <span v-if="dirtyFiles.length > 0" class="badge is-warn">
          源码已保存更新（{{ dirtyFiles.length }} 个文件）
        </span>
      </div>
      <div class="git-bar-actions">
        <button
          class="btn btn-sm btn-ghost"
          type="button"
          :disabled="refreshing"
          @click="emit('refresh')"
        >
          {{ refreshing ? "刷新中…" : "刷新代码图" }}
        </button>
      </div>
    </div>

    <div v-if="expanded && localGit?.changedFiles?.length" class="git-changed-drawer">
      <div class="git-changed-list">
        <button
          v-for="file in (localGit.changedFiles ?? []).slice(0, 30)"
          :key="file"
          class="git-file-chip"
          type="button"
          title="点击尝试在编辑器跳转至变更行"
          @click="openFile(file)"
        >
          <span class="mono">{{ file }}</span>
          <span v-if="firstChangedLineByFile.has(file)" class="muted">
            :L{{ firstChangedLineByFile.get(file) }}
          </span>
        </button>
      </div>
    </div>
  </section>
</template>
