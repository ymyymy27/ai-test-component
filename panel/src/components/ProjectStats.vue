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
    <div class="stat">
      <span class="stat-value">{{ tasks }}</span>
      <span class="stat-label">任务</span>
    </div>
    <div class="stat">
      <span class="stat-value">{{ runs }}</span>
      <span class="stat-label">运行</span>
    </div>
    <div class="stat">
      <span class="stat-value">{{ openIssues }}</span>
      <span class="stat-label">未关闭问题</span>
    </div>
    <div class="stat">
      <span class="stat-value">{{ features }}</span>
      <span class="stat-label">业务功能</span>
    </div>
  </div>
</template>
