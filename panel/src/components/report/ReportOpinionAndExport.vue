<script setup lang="ts">
import { ref, watch } from "vue";

import type { ExportResult, Report } from "@/domain/types";
import { bytesText, formatTime } from "./reportFormat";

const props = defineProps<{
  report: Report;
  isLatest: boolean;
  busyOpinion: boolean;
  exportResult: ExportResult | null;
}>();

const emit = defineEmits<{
  saveOpinion: [payload: { by: string; text: string }];
  openExport: [];
  copyMarkdown: [];
}>();

const opinionBy = ref("本机用户");
const opinionText = ref("");
const opinionError = ref("");

watch(
  () => props.report.id,
  () => {
    opinionBy.value = props.report.opinion?.by ?? "本机用户";
    opinionText.value = props.report.opinion?.text ?? "";
    opinionError.value = "";
  },
  { immediate: true },
);

function submitOpinion(): void {
  if (props.busyOpinion) {
    return;
  }
  if (!opinionText.value.trim()) {
    opinionError.value = "先填写验收意见。";
    return;
  }
  opinionError.value = "";
  emit("saveOpinion", { by: opinionBy.value, text: opinionText.value });
}
</script>

<template>
  <section id="report-opinion" class="section">
    <h2 class="section-title-lg">验收意见</h2>
    <div class="report-form-shell">
      <div v-if="report.opinion && !isLatest" class="opinion-saved">
        <p class="opinion-saved-text">{{ report.opinion.text }}</p>
        <div class="opinion-saved-meta">
          <span><strong>{{ report.opinion.by }}</strong> · {{ formatTime(report.opinion.at) }}</span>
          <span class="badge is-pass">已保存</span>
        </div>
      </div>
      <form v-if="isLatest" class="opinion-form" novalidate @submit.prevent="submitOpinion">
        <p v-if="report.opinion" class="opinion-saved-hint">
          上次保存：<strong>{{ report.opinion.by }}</strong> ·
          {{ formatTime(report.opinion.at) }}
        </p>
        <div class="opinion-composer" :class="{ 'has-error': opinionError }">
          <div class="field" :class="{ 'has-error': opinionError }">
            <label class="visually-hidden" for="opinion-text">意见</label>
            <textarea
              id="opinion-text"
              v-model="opinionText"
              rows="3"
              placeholder="写下本次结论、遗留风险或下一步安排。"
              :aria-invalid="opinionError ? 'true' : undefined"
              :aria-describedby="opinionError ? 'opinion-error' : undefined"
              @input="opinionError = ''"
            />
            <p v-if="opinionError" id="opinion-error" class="field-error" role="alert">
              {{ opinionError }}
            </p>
          </div>
          <div class="opinion-footer">
            <div class="opinion-by">
              <label for="opinion-by">署名</label>
              <input id="opinion-by" v-model="opinionBy" type="text" maxlength="40" />
            </div>
            <button
              class="btn btn-primary"
              type="submit"
              :disabled="busyOpinion"
              :aria-busy="busyOpinion"
            >
              {{ busyOpinion ? "正在保存…" : "保存意见" }}
            </button>
          </div>
        </div>
      </form>
      <p v-else class="doc-note">旧版本只读，不能在旧版本上填写验收意见。</p>
    </div>
  </section>

  <section v-if="exportResult" class="section">
    <h2 class="section-title-lg">导出结果</h2>
    <dl class="info-grid">
      <div class="info-cell">
        <dt>文件</dt>
        <dd class="mono">{{ exportResult.filename }}</dd>
      </div>
      <div class="info-cell">
        <dt>大小</dt>
        <dd>{{ bytesText(exportResult.bytes) }}</dd>
      </div>
      <div class="info-cell">
        <dt>行数</dt>
        <dd>{{ exportResult.lines }}</dd>
      </div>
      <div class="info-cell">
        <dt>sha256</dt>
        <dd class="mono">{{ exportResult.sha256 }}</dd>
      </div>
    </dl>
    <div v-if="exportResult.path" class="notice">
      <span class="mono">{{ exportResult.path }}</span>
      <button class="link-btn" type="button" @click="emit('openExport')">用系统程序打开</button>
      <p class="field-hint">
        宿主模式由 CLI 写入工作空间文件；面板不提供 Markdown 预览或复制，请在打开的文件中查看。
      </p>
    </div>
    <details v-else class="unselected-block">
      <summary>Markdown 预览</summary>
      <textarea class="markdown-preview" readonly :value="exportResult.markdown" />
      <div class="form-actions">
        <button class="btn btn-sm" type="button" @click="emit('copyMarkdown')">复制 Markdown</button>
      </div>
    </details>
  </section>
</template>
