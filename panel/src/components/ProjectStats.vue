<script setup lang="ts">
defineProps<{
  tasks: number;
  runs: number;
  openIssues: number;
  features: number;
}>();
</script>

<template>
  <div class="stats-bar" role="group" aria-label="项目统计">
    <div class="stat is-tasks">
      <span class="stat-label">任务</span>
      <span class="stat-value">{{ tasks }}</span>
    </div>
    <div class="stat is-runs">
      <span class="stat-label">运行</span>
      <span class="stat-value">{{ runs }}</span>
    </div>
    <div class="stat is-issues" :class="{ 'has-alert': openIssues > 0 }">
      <span class="stat-label">未关闭问题</span>
      <span class="stat-value">{{ openIssues }}</span>
    </div>
    <div class="stat is-features">
      <span class="stat-label">业务功能</span>
      <span class="stat-value">{{ features }}</span>
    </div>
  </div>
</template>
