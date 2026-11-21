<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink, useRouter } from "vue-router";

import Icon from "@/components/Icon.vue";
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
const picking = ref(false);

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

async function chooseDirectory(): Promise<void> {
  if (picking.value) {
    return;
  }
  if (workspace.mode !== "host") {
    ui.showToast(
      "浏览器演示模式无法读取本机绝对路径；请直接输入路径，接入 Trae 后可用“选择目录”。",
      "info",
    );
    return;
  }
  picking.value = true;
  try {
    const path = await workspace.pickDirectory();
    if (path) {
      root.value = path;
      clearError("root");
    }
  } catch {
    // 宿主不可用时保留手动输入。
  } finally {
    picking.value = false;
  }
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

async function retryRead(): Promise<void> {
  try {
    await workspace.bootstrap();
  } catch {
    // store 已提示。
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
      title="建立项目"
      :subtitle="
        currentProject ? '登记另一个本地目录。' : '选择一个本地目录作为测试项目。'
      "
    />

    <p v-if="workspace.localMode" class="local-demo-banner" role="status">
      本地演示数据 · 未读取真实仓库
    </p>

    <div v-if="workspace.status === 'error'" class="notice is-warn" role="alert">
      <span>本地数据无法读取：{{ workspace.error }}</span>
      <button class="btn btn-sm" type="button" @click="retryRead">重新读取</button>
      <button class="btn btn-sm btn-danger" type="button" @click="clearAndRetry">
        清空本地数据
      </button>
    </div>

    <div class="setup-grid">
      <form class="setup-form" novalidate @submit.prevent="submit">
        <div class="field" :class="{ 'has-error': errors.root }">
          <label for="setup-root">项目目录</label>
          <div class="input-with-button">
            <input
              id="setup-root"
              ref="rootInput"
              v-model="root"
              name="root"
              type="text"
              autocomplete="off"
              spellcheck="false"
              placeholder="D:\work\service"
              :aria-invalid="errors.root ? 'true' : undefined"
              :aria-describedby="
                errors.root ? 'setup-root-hint setup-root-error' : 'setup-root-hint'
              "
              :disabled="busy"
              @input="clearError('root')"
            />
            <button
              class="btn btn-sm"
              type="button"
              :disabled="picking || busy || workspace.mode !== 'host'"
              :title="
                workspace.mode === 'host'
                  ? '从本机选择项目目录'
                  : '浏览器演示模式无法读取本机绝对路径'
              "
              @click="chooseDirectory"
            >
              <Icon name="folder" :size="14" />
              {{
                picking
                  ? "选择中…"
                  : workspace.mode === "host"
                    ? "选择目录"
                    : "选择目录（需 Trae）"
              }}
            </button>
          </div>
          <p id="setup-root-hint" class="field-hint">
            {{
              workspace.mode === "host"
                ? "输入绝对路径，或从本机选择目录。"
                : "输入绝对路径；浏览器演示模式无法读取本机目录。"
            }}
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
            :aria-describedby="
              errors.name ? 'setup-name-hint setup-name-error' : 'setup-name-hint'
            "
            :disabled="busy"
            @input="clearError('name')"
          />
          <p id="setup-name-hint" class="field-hint">最多 40 个字；只用于面板显示。</p>
          <p v-if="errors.name" id="setup-name-error" class="field-error" role="alert">
            {{ errors.name }}
          </p>
        </div>

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
      </form>

      <aside class="setup-aside" aria-label="登记之后会发生什么">
        <h2>登记后</h2>
        <ol class="setup-steps">
          <li><span class="num">01</span>读取入口与目录结构</li>
          <li><span class="num">02</span>整理功能、可测项与未知项</li>
          <li><span class="num">03</span>生成目标候选，确认后创建任务</li>
        </ol>
        <p class="setup-note">不会创建任务、不会写入被测仓库；运行前仍需人工确认。</p>
      </aside>
    </div>
  </div>
</template>
