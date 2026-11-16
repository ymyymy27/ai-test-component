<script setup lang="ts">
import { computed } from "vue";

import {
  RUN_STATUS_BADGE,
  RUN_STATUS_LABEL,
  VERDICT_LABEL,
  VERDICT_TONE,
  formatDateTime,
} from "@/api/labels";
import AppButton from "@/components/AppButton.vue";
import AppDrawer from "@/components/AppDrawer.vue";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import {
  closeDrawers,
  openResult,
  openSettings,
  session,
} from "@/state/session";

const project = computed(() => session.project);
const recent = computed(() => session.history.slice(0, 3));

async function openRun(runId: string): Promise<void> {
  const opened = await openResult(runId);
  if (opened) {
    closeDrawers();
  }
}
</script>

<template>
  <AppDrawer
    :open="session.projectOpen"
    title="项目详情"
    note="这里只读项目登记事实；测试数据保存在本机工作空间。"
    @close="closeDrawers"
  >
    <div v-if="project" class="project">
      <header class="name-block">
        <h3>{{ project.name }}</h3>
        <p class="root mono" :title="project.root">{{ project.root }}</p>
      </header>

      <dl class="facts">
        <div class="fact">
          <dt>模块</dt>
          <dd>
            <span class="numeric">{{ project.modules.length }}</span>
            <span v-if="!project.modules.length" class="quiet">还没有登记模块</span>
          </dd>
        </div>
        <div class="fact">
          <dt>入口</dt>
          <dd>运行前由源码可运行性检查确认</dd>
        </div>
        <div v-if="session.lastRun" class="fact">
          <dt>最近运行</dt>
          <dd>
            <span class="numeric">{{ formatDateTime(session.lastRun.startedAt) }}</span>
            <Badge :tone="RUN_STATUS_BADGE[session.lastRun.status]">
              {{ RUN_STATUS_LABEL[session.lastRun.status] }}
            </Badge>
            <Badge v-if="session.lastRun.outcome" :tone="VERDICT_TONE[session.lastRun.outcome]">
              {{ VERDICT_LABEL[session.lastRun.outcome] }}
            </Badge>
            <span class="numeric coverage">
              {{ session.lastRun.selected }} / {{ session.lastRun.applicable }}
            </span>
          </dd>
        </div>
      </dl>

      <section class="modules">
        <h4>模块</h4>
        <ul v-if="project.modules.length" class="module-list">
          <li v-for="module in project.modules" :key="module.module_id">
            <span class="module-name">{{ module.name }}</span>
            <span class="module-note">{{ module.responsibility || module.status || "未记录职责" }}</span>
          </li>
        </ul>
        <p v-else class="hint">还没有模块。可以在“调整”里让 AI 建议并确认写入。</p>
      </section>

      <section class="modules">
        <h4>最近运行</h4>
        <ul v-if="recent.length" class="recent-list">
          <li v-for="run in recent" :key="run.run_id">
            <button type="button" class="recent-row" @click="openRun(run.run_id)">
              <span class="numeric">{{ formatDateTime(run.started_at) }}</span>
              <Badge :tone="RUN_STATUS_BADGE[run.status]">{{ RUN_STATUS_LABEL[run.status] }}</Badge>
              <span class="chev" aria-hidden="true"><Icon name="chevron-right" :size="13" /></span>
            </button>
          </li>
        </ul>
        <p v-else class="hint">还没有运行记录。</p>
      </section>

      <div class="drawer-links">
        <AppButton size="sm" variant="ghost" @click="openSettings">
          <Icon name="settings" :size="14" />
          打开设置
        </AppButton>
      </div>
    </div>
  </AppDrawer>
</template>

<style scoped>
.project {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.name-block {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.name-block h3 {
  font-size: var(--fs-16);
}

.root {
  color: var(--text-3);
  font-size: var(--fs-12);
  overflow-wrap: anywhere;
}

.facts {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: var(--space-3);
  background: var(--surface-sunken);
  border: 1px solid var(--line);
  border-radius: var(--r-control);
}

.fact {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: var(--space-2);
  align-items: baseline;
}

.fact dt {
  font-size: var(--fs-12);
  color: var(--text-3);
}

.fact dd {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: var(--fs-12);
  color: var(--text-2);
}

.coverage {
  color: var(--text);
}

.modules {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.modules h4 {
  margin: 0;
  font-size: var(--fs-12);
  font-weight: 500;
  color: var(--text-3);
}

.module-list,
.recent-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-control);
  background: var(--surface);
  overflow: hidden;
}

.module-list li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 7px var(--space-3);
  border-bottom: 1px solid var(--line);
}

.module-list li:last-child {
  border-bottom: 0;
}

.module-name {
  font-size: var(--fs-13);
}

.module-note {
  font-size: var(--fs-12);
  color: var(--text-3);
  text-align: right;
}

.recent-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: 7px var(--space-3);
  background: none;
  border: 0;
  border-bottom: 1px solid var(--line);
  color: var(--text);
  font-size: var(--fs-12);
  text-align: left;
}

.recent-list li:last-child .recent-row {
  border-bottom: 0;
}

.recent-row:hover {
  background: var(--surface-raised);
}

.recent-row .numeric {
  flex: 1 1 auto;
}

.chev {
  color: var(--text-3);
}

.drawer-links {
  display: flex;
  justify-content: flex-start;
  padding-top: var(--space-2);
  border-top: 1px solid var(--line);
}
</style>
