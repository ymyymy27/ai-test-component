<script setup lang="ts">
import { computed, onMounted } from "vue";

import { isHostReady } from "@/api/host";
import AppHeader from "@/components/AppHeader.vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import SkeletonBlock from "@/components/SkeletonBlock.vue";
import SessionView from "@/views/SessionView.vue";
import SettingsView from "@/views/SettingsView.vue";
import {
  bootstrap,
  clearNotice,
  openSettings,
  session,
  switchProject,
} from "@/state/session";

const hostReady = computed(() => isHostReady());

onMounted(() => {
  if (hostReady.value) {
    void bootstrap();
  }
});
</script>

<template>
  <a class="skip-link" href="#main">跳到主内容</a>

  <AppHeader
    v-if="hostReady && session.ready"
    :projects="session.projects"
    :project="session.project"
    :env="session.env"
    @select="switchProject"
    @settings="openSettings"
  />

  <header v-if="!hostReady" class="rawbar">
    <span class="rawbar-brand">AITEST<span aria-hidden="true">▌</span></span>
    <span class="rawbar-status">NO HOST</span>
  </header>

  <main id="main" class="container">
    <EmptyState
      v-if="!hostReady"
      title="面板没有连接到编辑器"
      icon="info"
    >
      <template #body>
        这个页面需要通过编辑器的“AI 测试”面板打开。独立浏览器页面只能查看静态预览，无法读取项目或运行测试。
      </template>
    </EmptyState>

    <template v-else-if="session.booting || !session.ready">
      <div class="boot">
        <p class="boot-title">AI 辅助测试</p>
        <SkeletonBlock :lines="4" label="正在读取项目与环境" />
      </div>
    </template>

    <template v-else>
      <div v-if="session.notice" class="notice-bar" role="status">
        <span>{{ session.notice }}</span>
        <button type="button" class="notice-close" aria-label="关闭提示" @click="clearNotice">
          <Icon name="close" :size="13" />
        </button>
      </div>

      <SettingsView v-if="session.screen === 'settings'" />
      <SessionView v-else />
    </template>
  </main>

  <footer class="statusline">
    <span class="status-item"><i aria-hidden="true"></i> LOCAL</span>
    <span class="status-item">NO NETWORK</span>
    <span class="status-item">
      {{
        session.phase === "running"
          ? "POLL / 2S"
          : session.phase === "proposal"
            ? "CTRL+ENTER RUN"
            : "LOCAL CLI"
      }}
    </span>
  </footer>
</template>

<style scoped>
.rawbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--header-h);
  padding: 0 var(--space-4);
  border-bottom: 1px solid var(--line);
}

.rawbar-brand {
  font-family: var(--font-mono);
  font-size: var(--fs-11);
  letter-spacing: 0.2em;
  color: var(--text);
}

.rawbar-brand span {
  margin-left: 5px;
  color: var(--accent);
}

.rawbar-status {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.14em;
  color: var(--failed);
}

.statusline {
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 9;
  display: flex;
  align-items: center;
  gap: var(--space-4);
  height: 24px;
  padding: 0 var(--space-4);
  border-top: 1px solid var(--line);
  background: var(--bg);
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.14em;
  color: var(--text-disabled);
}

.status-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.status-item i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--passed);
}

.status-item:nth-child(3) {
  margin-left: auto;
}

.boot {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding-top: var(--space-4);
}

.boot-title {
  font-size: var(--fs-20);
  font-weight: 600;
}

.notice-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
  padding: 8px var(--space-3);
  background: var(--accent-fill);
  border: 1px solid var(--accent-border);
  border-radius: var(--r-control);
  font-size: var(--fs-12);
  color: var(--text-2);
}

.notice-close {
  flex: none;
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  background: none;
  border: 0;
  border-radius: 4px;
  color: var(--text-3);
  line-height: 1;
}

.notice-close:hover {
  background: var(--surface-raised);
  color: var(--text);
}
</style>
