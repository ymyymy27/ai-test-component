<script setup lang="ts">
import type { Report, RunStatus } from "@/domain/types";

withDefaults(
  defineProps<{
    report: Report | null;
    versionCount: number;
    runStatus: RunStatus;
    postprocess?: "pending" | "running" | "done" | "error";
    busy?: boolean;
  }>(),
  { postprocess: "done", busy: false },
);

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
          postprocess === "pending" || postprocess === "running"
            ? "报告生成中；完成后这里会自动出现。"
            : runStatus === "interrupted"
              ? "运行已中断，报告尚未生成。"
              : runStatus === "execution_error"
                ? "运行执行出错，不会生成报告；先查看项目级检查与运行记录。"
                : "运行结束后会生成报告；也可以现在生成。"
        }}
      </span>
    </div>
    <template v-if="report">
      <button
        class="btn btn-sm btn-primary"
        type="button"
        :disabled="busy"
        :aria-busy="busy"
        @click="emit('open', report.id)"
      >
        查看报告
      </button>
      <button
        class="btn btn-sm"
        type="button"
        :disabled="busy"
        :aria-busy="busy"
        @click="emit('export', report.id)"
      >
        {{ busy ? "正在导出…" : "导出 Markdown" }}
      </button>
    </template>
    <button
      v-else
      class="btn btn-sm"
      type="button"
      :disabled="busy || postprocess === 'pending' || postprocess === 'running'"
      :aria-busy="busy || postprocess === 'pending' || postprocess === 'running'"
      @click="emit('regenerate')"
    >
      {{
        busy
          ? "正在生成…"
          : postprocess === "pending" || postprocess === "running"
            ? "报告生成中…"
            : runStatus === "interrupted"
              ? "生成报告"
              : "重新生成报告"
      }}
    </button>
  </div>
</template>
