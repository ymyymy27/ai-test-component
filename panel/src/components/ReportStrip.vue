<script setup lang="ts">
import type { Report, RunStatus } from "@/domain/types";

defineProps<{
  report: Report | null;
  versionCount: number;
  runStatus: RunStatus;
}>();

const emit = defineEmits<{
  open: [reportId: string];
  regenerate: [];
  export: [reportId: string];
}>();
</script>

<template>
  <div class="report-strip">
    <div v-if="report" class="report-strip-main">
      <span class="report-strip-title">测试报告 · 第 {{ report.version }} 版</span>
      <span class="report-strip-meta">
        生成于 {{ new Date(report.generatedAt).toLocaleString("zh-CN") }} ·
        {{ versionCount }} 个版本 · 验收意见{{ report.opinion ? "已填写" : "未填写" }}
      </span>
    </div>
    <div v-else class="report-strip-main">
      <span class="report-strip-title">还没有报告</span>
      <span class="report-strip-meta">
        {{
          runStatus === "interrupted"
            ? "中断运行没有报告。"
            : "运行结束后会生成报告；也可以现在重新生成。"
        }}
      </span>
    </div>
    <template v-if="report">
      <button class="btn btn-sm" type="button" @click="emit('open', report.id)">查看报告</button>
      <button class="btn btn-sm" type="button" @click="emit('export', report.id)">
        导出 Markdown
      </button>
    </template>
    <button v-else class="btn btn-sm" type="button" @click="emit('regenerate')">
      重新生成报告
    </button>
  </div>
</template>
