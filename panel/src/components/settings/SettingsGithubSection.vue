<script setup lang="ts">
import { computed, ref, watch } from "vue";

import EmptyState from "@/components/EmptyState.vue";
import type { GithubSnapshot, Project } from "@/domain/types";
import { parseLineList } from "@/domain/validation";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const props = defineProps<{
  project: Project;
  active: boolean;
}>();

const workspace = useWorkspaceStore();
const ui = useUiStore();
const githubForm = ref({
  repo: "",
  branch: "main",
  commit: "",
  files: "",
  checks: "",
  account: "",
});
const githubError = ref("");
const githubReadRepo = ref("");
const githubReadRef = ref("main");
const githubReadError = ref("");
const busyGithub = computed(() => workspace.busyKey(`github:${props.project.id}`));
const busyGithubRead = computed(() => workspace.busyKey(`github-read:${props.project.id}`));

watch(
  () => props.project.id,
  () => {
    const gh = props.project.github;
    githubForm.value = {
      repo: gh?.repo ?? "",
      branch: gh?.branch ?? "main",
      commit: gh?.commit ?? "",
      files: gh?.files.join("\n") ?? "",
      checks: gh?.checks.join("\n") ?? "",
      account: gh?.account ?? "",
    };
    githubReadRepo.value = gh?.repo ?? "";
    githubReadRef.value = gh?.branch ?? "main";
    githubError.value = "";
    githubReadError.value = "";
  },
  { immediate: true },
);

async function readGithub(): Promise<void> {
  if (!githubReadRepo.value.trim()) {
    githubReadError.value = "填写仓库后再读取。";
    return;
  }
  githubReadError.value = "";
  try {
    await workspace.readGithub(
      props.project.id,
      githubReadRepo.value.trim(),
      githubReadRef.value.trim() || "main",
    );
    ui.showToast("GitHub 变更已读取。", "success");
  } catch {
    // store 已提示。
  }
}

async function saveGithubSnapshot(): Promise<void> {
  if (!githubForm.value.repo.trim() || !githubForm.value.commit.trim()) {
    githubError.value = "填写仓库与提交号后再保存缓存快照。";
    return;
  }
  githubError.value = "";
  const snapshot: GithubSnapshot = {
    repo: githubForm.value.repo.trim(),
    branch: githubForm.value.branch.trim() || "main",
    commit: githubForm.value.commit.trim(),
    files: parseLineList(githubForm.value.files),
    prs: [],
    checks: parseLineList(githubForm.value.checks),
    deployment: "",
    account: githubForm.value.account.trim(),
    host: "github.com",
    fetchedAt: new Date().toISOString(),
    cached: true,
    unavailableReason: "",
  };
  try {
    await workspace.saveGithubSnapshot(props.project.id, snapshot);
    ui.showToast("GitHub 缓存快照已保存；报告会标“未确认最新”。", "success");
  } catch {
    // store 已提示。
  }
}

async function clearGithubSnapshot(): Promise<void> {
  const answer = await ui.askConfirm({
    title: "清除 GitHub 缓存快照？",
    body: "清除后设置页显示未连接状态；不会影响本地测试、证据与报告。",
    confirmLabel: "清除快照",
    tone: "danger",
  });
  if (answer === null) {
    return;
  }
  try {
    await workspace.saveGithubSnapshot(props.project.id, null);
    ui.showToast("GitHub 缓存快照已清除。", "success");
  } catch {
    // store 已提示。
  }
}
</script>

<template>
  <section
    v-show="active"
    class="settings-section"
    id="settings-github"
    role="tabpanel"
    aria-labelledby="settings-tab-settings-github"
  >
    <div class="settings-head">
      <div>
        <h2 class="settings-title">GitHub 变更</h2>
        <p class="settings-desc">只读提交与检查；缓存不冒充最新。</p>
      </div>
    </div>
    <div v-if="project.github" class="surface-strip">
      <div class="row-between">
        <p class="doc-p">
          <strong>{{ project.github.repo }}</strong> · {{ project.github.commit }} ·
          {{ project.github.branch }}
        </p>
        <span class="badge" :class="project.github.cached ? 'is-warn' : 'is-pass'">
          {{ project.github.cached ? "缓存快照，未确认最新" : "最新读取" }}
        </span>
      </div>
      <p class="doc-note">
        读取于 {{ new Date(project.github.fetchedAt).toLocaleString("zh-CN") }}
        <template v-if="project.github.account"> · {{ project.github.account }}</template>
      </p>
      <p class="doc-note mono">{{ project.github.files.join(" · ") || "没有文件列表" }}</p>
      <p v-if="project.github.checks.length" class="doc-note">
        检查：{{ project.github.checks.join(" · ") }}
      </p>
      <button
        class="btn btn-sm btn-danger"
        type="button"
        :disabled="busyGithub"
        @click="clearGithubSnapshot"
      >
        清除缓存快照
      </button>
    </div>
    <EmptyState
      v-else
      title="未连接 GitHub"
      :text="
        workspace.mode === 'host'
          ? '输入仓库与分支后读取；读取失败不会用缓存冒充最新。'
          : '只读提交、PR、检查与部署不可用；本地测试、证据与报告不受影响。可以手动登记缓存快照，或接入宿主后读取。'
      "
    />
    <form
      v-if="workspace.mode === 'host'"
      class="settings-form github-read-form"
      novalidate
      @submit.prevent="readGithub"
    >
      <div class="form-grid">
        <div class="field">
          <label for="github-read-repo">仓库</label>
          <input id="github-read-repo" v-model="githubReadRepo" type="text" placeholder="owner/name" />
        </div>
        <div class="field">
          <label for="github-read-ref">分支或引用</label>
          <input id="github-read-ref" v-model="githubReadRef" type="text" placeholder="main" />
        </div>
      </div>
      <p v-if="githubReadError" class="field-error" role="alert">{{ githubReadError }}</p>
      <div class="form-actions">
        <button class="btn btn-primary" type="submit" :disabled="busyGithubRead" :aria-busy="busyGithubRead">
          {{ busyGithubRead ? "正在读取…" : "读取 GitHub" }}
        </button>
      </div>
    </form>
    <details v-if="workspace.mode !== 'host'" class="link-tools" :open="!project.github">
      <summary>{{ project.github ? "更新缓存快照" : "登记缓存快照" }}</summary>
      <form class="settings-form" novalidate @submit.prevent="saveGithubSnapshot">
        <div class="form-grid">
          <div class="field">
            <label for="github-repo">仓库</label>
            <input id="github-repo" v-model="githubForm.repo" type="text" placeholder="owner/name" />
          </div>
          <div class="field">
            <label for="github-branch">分支</label>
            <input id="github-branch" v-model="githubForm.branch" type="text" placeholder="main" />
          </div>
          <div class="field">
            <label for="github-commit">提交</label>
            <input id="github-commit" v-model="githubForm.commit" type="text" placeholder="abc123" />
          </div>
          <div class="field">
            <label for="github-account">账号 <span class="optional">选填</span></label>
            <input id="github-account" v-model="githubForm.account" type="text" />
          </div>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="github-files">文件 <span class="optional">一行一条</span></label>
            <textarea id="github-files" v-model="githubForm.files" rows="3" />
          </div>
          <div class="field">
            <label for="github-checks">检查 <span class="optional">一行一条</span></label>
            <textarea id="github-checks" v-model="githubForm.checks" rows="3" />
          </div>
        </div>
        <p v-if="githubError" class="field-error" role="alert">{{ githubError }}</p>
        <p class="field-hint">
          这是手动登记的缓存快照，不代表 GitHub 最新状态；报告会明确标注“未确认最新”。
        </p>
        <div class="form-actions">
          <button class="btn btn-primary" type="submit" :disabled="busyGithub" :aria-busy="busyGithub">
            {{ busyGithub ? "正在保存…" : "保存缓存快照" }}
          </button>
        </div>
      </form>
    </details>
  </section>
</template>
