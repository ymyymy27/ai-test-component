<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink, useRouter } from "vue-router";

import ErrorState from "@/components/ErrorState.vue";
import PageHeader from "@/components/PageHeader.vue";
import { validateProjectForm, type ProjectFormErrors } from "@/domain/validation";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";

const router = useRouter();
const workspace = useWorkspaceStore();
const ui = useUiStore();

const root = ref("");
const name = ref("");
const errors = ref<ProjectFormErrors>({});
const rootInput = ref<HTMLInputElement | null>(null);
const nameInput = ref<HTMLInputElement | null>(null);

const busy = computed(() => workspace.busyKey("create-project"));
const currentProject = computed(() => workspace.currentProject);

function validate(): { root: string; name: string } | null {
  const result = validateProjectForm({ root: root.value, name: name.value }, workspace.projects);
  errors.value = result.errors;
  if (!result.ok) {
    if (result.errors.root) {
      rootInput.value?.focus();
    } else if (result.errors.name) {
      nameInput.value?.focus();
    }
    return null;
  }
  return { root: result.root, name: result.name };
}

function clearError(field: keyof ProjectFormErrors): void {
  if (!errors.value[field]) {
    return;
  }
  errors.value = { ...errors.value, [field]: undefined };
}

async function submit(): Promise<void> {
  if (busy.value) {
    return;
  }
  const valid = validate();
  if (!valid) {
    return;
  }
  try {
    const project = await workspace.createProject({
      root: valid.root,
      name: valid.name,
    });
    await router.push(`/projects/${project.id}/overview`);
  } catch {
    // store 已提示错误，保留输入。
  }
}

async function clearAndRetry(): Promise<void> {
  const answer = await ui.askConfirm({
    title: "清空本地面板数据？",
    body: "会删除本机保存的项目、任务、问题与报告，不影响被测仓库。",
    confirmLabel: "清空并重新开始",
    tone: "danger",
  });
  if (answer === null) {
    return;
  }
  await workspace.clearData();
  await workspace.bootstrap();
}
</script>

<template>
  <div class="page">
    <PageHeader
      kicker="建立项目"
      title="建立项目"
      :subtitle="
        currentProject
          ? '再登记一个本地目录；不会影响当前项目的数据。'
          : '先把一个本地目录登记为项目，再从项目概览开始。'
      "
    />

    <ErrorState
      v-if="workspace.status === 'error'"
      title="本地数据无法读取"
      :text="workspace.error"
      retry-label="清空并重新开始"
      @retry="clearAndRetry"
    />

    <div v-else class="setup-grid">
      <form class="setup-form" novalidate @submit.prevent="submit">
        <div class="field" :class="{ 'has-error': errors.root }">
          <label for="setup-root">项目目录</label>
          <input
            id="setup-root"
            ref="rootInput"
            v-model="root"
            name="root"
            type="text"
            autocomplete="off"
            spellcheck="false"
            placeholder="D:\work\service 或 /home/me/service"
            :aria-invalid="errors.root ? 'true' : undefined"
            aria-describedby="setup-root-hint setup-root-error"
            @input="clearError('root')"
          />
          <p id="setup-root-hint" class="field-hint">
            绝对路径。浏览器环境请手动输入；宿主支持目录选择时会由宿主提供。
          </p>
          <p v-if="errors.root" id="setup-root-error" class="field-error" role="alert">
            {{ errors.root }}
          </p>
        </div>

        <div class="field" :class="{ 'has-error': errors.name }">
          <label for="setup-name">项目名 <span class="optional">留空则取目录名</span></label>
          <input
            id="setup-name"
            ref="nameInput"
            v-model="name"
            name="name"
            type="text"
            autocomplete="off"
            placeholder="例如：数据同步服务"
            :aria-invalid="errors.name ? 'true' : undefined"
            :aria-describedby="errors.name ? 'setup-name-error' : 'setup-name-hint'"
            @input="clearError('name')"
          />
          <p id="setup-name-hint" class="field-hint">最多 40 个字；只用于面板显示。</p>
          <p v-if="errors.name" id="setup-name-error" class="field-error" role="alert">
            {{ errors.name }}
          </p>
        </div>

        <p class="setup-note">
          项目目标不在这里手填。登记后会整理目标候选；确认目标后才创建第一个任务。
        </p>

        <div class="setup-actions">
          <button class="btn btn-primary" type="submit" :disabled="busy" :aria-busy="busy">
            {{ busy ? "正在登记…" : "登记并分析项目" }}
          </button>
          <RouterLink
            v-if="currentProject"
            class="btn btn-ghost"
            :to="`/projects/${currentProject.id}/overview`"
          >
            取消
          </RouterLink>
        </div>
        <p class="setup-note">
          当前面板不读取磁盘。分析由本地规则完成，模块、业务功能与候选会明确标注来源。
        </p>
      </form>

      <aside class="setup-aside" aria-label="登记之后会发生什么">
        <h2>登记之后</h2>
        <ol class="setup-steps">
          <li><span class="num">01</span>整理启动入口与目录结构</li>
          <li><span class="num">02</span>识别模块、业务功能与可测项</li>
          <li><span class="num">03</span>给出项目目标候选与未知项</li>
          <li><span class="num">04</span>进入概览；确认目标后才创建任务</li>
        </ol>
        <div class="setup-contract">
          <strong>不会自动发生</strong>
          <ul>
            <li>不会创建任务或运行测试。</li>
            <li>不会写入被测仓库。</li>
            <li>运行前仍要人工确认计划、依赖与副作用。</li>
          </ul>
        </div>
        <p class="setup-note">
          团队内部使用：项目记录、证据与报告留在本机工作空间，不上传、不外发。
        </p>
      </aside>
    </div>
  </div>
</template>
