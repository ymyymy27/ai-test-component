<script setup lang="ts">
import { nextTick, ref, watch } from "vue";

import type { EvidencePreview } from "@/host/dto";
import { useUiStore } from "@/stores/ui";
import { useWorkspaceStore } from "@/stores/workspace";
import { bytesText, type ReportDetail } from "./reportFormat";

const props = withDefaults(
  defineProps<{
    detail: ReportDetail | null;
    simulated: boolean;
    hostMode: boolean;
    projectId?: string;
    runId?: string;
  }>(),
  { projectId: "", runId: "" },
);

const emit = defineEmits<{
  close: [];
  openHostPath: [path: string];
}>();

const ui = useUiStore();
const workspace = useWorkspaceStore();
const dialogEl = ref<HTMLDialogElement | null>(null);
const preview = ref<EvidencePreview | null>(null);
const previewLoading = ref(false);
const previewError = ref("");
let previewToken = 0;

watch(
  () => props.detail,
  (next) => {
    const token = ++previewToken;
    preview.value = null;
    previewLoading.value = false;
    previewError.value = "";
    if (
      next?.kind === "evidence" &&
      !props.simulated &&
      props.hostMode &&
      props.projectId &&
      props.runId
    ) {
      const refPath = next.evidence.id || next.evidence.sourcePath || "";
      if (refPath) {
        previewLoading.value = true;
        void workspace
          .previewEvidence(props.projectId, props.runId, refPath)
          .then((result) => {
            if (token !== previewToken) {
              return;
            }
            preview.value = result;
          })
          .catch(() => {
            if (token !== previewToken) {
              return;
            }
            previewError.value = "未能加载内联预览，可点下方按钮打开原文件。";
          })
          .finally(() => {
            if (token === previewToken) {
              previewLoading.value = false;
            }
          });
      }
    }
    void nextTick(() => {
      const dialog = dialogEl.value;
      if (!dialog) {
        return;
      }
      if (next && !dialog.open) {
        dialog.showModal();
      } else if (!next && dialog.open) {
        dialog.close();
      }
    });
  },
);

function closeDetail(): void {
  dialogEl.value?.close();
}

async function copyCommand(): Promise<void> {
  const detail = props.detail;
  if (detail?.kind !== "command" || !detail.command.trim()) {
    return;
  }
  try {
    await navigator.clipboard.writeText(detail.command);
    ui.showToast(
      `命令已复制。到项目根目录 ${detail.root || "（未记录）"} 打开终端粘贴执行。`,
      "success",
    );
  } catch {
    ui.showToast("复制失败，请手动选择命令。", "error");
  }
}

async function copyEvidencePath(): Promise<void> {
  const detail = props.detail;
  if (detail?.kind !== "evidence") {
    return;
  }
  const reference = detail.evidence.sourcePath || detail.evidence.name;
  try {
    await navigator.clipboard.writeText(reference);
    ui.showToast("证据路径已复制，可粘贴到问题记录、终端或工单中引用。", "success");
  } catch {
    ui.showToast("复制失败，请手动选择路径。", "error");
  }
}

function openEvidenceFile(): void {
  const detail = props.detail;
  if (detail?.kind !== "evidence" || !detail.evidence.sourcePath) {
    return;
  }
  emit("openHostPath", detail.evidence.sourcePath);
}
</script>

<template>
  <dialog ref="dialogEl" class="report-detail-dialog" @close="emit('close')">
    <template v-if="detail">
      <div class="dialog-form">
        <div class="dialog-head">
          <h2 class="dialog-title">
            {{ detail.kind === "command" ? "测试命令" : "证据详情" }}
          </h2>
          <button class="icon-btn" type="button" aria-label="关闭" @click="closeDetail">
            ×
          </button>
        </div>

        <template v-if="detail.kind === 'command'">
          <p class="dialog-text">{{ detail.title }}</p>
          <pre class="command-block">{{ detail.command }}</pre>
          <p class="field-hint">
            到项目根目录
            <span class="mono">{{ detail.root || "（未记录）" }}</span>
            打开终端粘贴执行；执行前确认依赖与服务已就绪。
          </p>
          <p v-if="simulated" class="field-hint">
            本地演示数据：这条命令仅用于预览，不会真正执行。
          </p>
          <div class="dialog-actions">
            <button class="btn btn-primary" type="button" @click="copyCommand">
              复制命令
            </button>
            <button class="btn" type="button" @click="closeDetail">关闭</button>
          </div>
        </template>

        <template v-else>
          <p class="dialog-text mono">{{ detail.evidence.name }}</p>
          <dl class="info-grid">
            <div class="info-cell">
              <dt>类型</dt>
              <dd>{{ detail.evidence.mediaType }}</dd>
            </div>
            <div class="info-cell">
              <dt>大小</dt>
              <dd>{{ bytesText(detail.evidence.bytes) }}</dd>
            </div>
            <div class="info-cell">
              <dt>sha256</dt>
              <dd class="mono">{{ detail.evidence.sha256 }}</dd>
            </div>
            <div class="info-cell">
              <dt>状态</dt>
              <dd>
                {{
                  detail.evidence.missing
                    ? "已缺失"
                    : detail.evidence.truncated
                      ? "已截断"
                      : "已登记"
                }}
              </dd>
            </div>
            <div class="info-cell info-cell-wide">
              <dt>说明</dt>
              <dd>{{ detail.evidence.note || "没有附加说明。" }}</dd>
            </div>
            <div class="info-cell info-cell-wide">
              <dt>路径</dt>
              <dd class="mono">
                {{ detail.evidence.sourcePath || detail.evidence.name }}
              </dd>
            </div>
          </dl>
          <div v-if="previewLoading" class="evidence-preview-box">
            <p class="field-hint">正在加载证据预览…</p>
          </div>
          <div v-else-if="previewError" class="evidence-preview-box">
            <p class="field-hint">{{ previewError }}</p>
          </div>
          <div v-else-if="preview?.kind === 'text'" class="evidence-preview-box">
            <div class="evidence-preview-meta">
              <span>文本预览</span>
              <span v-if="preview.truncated">内容较长，仅展示前段</span>
            </div>
            <pre class="evidence-preview-pre">{{ preview.text }}</pre>
          </div>
          <div
            v-else-if="preview?.kind === 'image' && preview.data_url"
            class="evidence-preview-box"
          >
            <div class="evidence-preview-meta">
              <span>图片预览</span>
            </div>
            <img
              class="evidence-preview-img"
              :src="preview.data_url"
              :alt="detail.evidence.name"
            />
          </div>
          <p v-else-if="preview?.kind === 'binary'" class="field-hint">
            二进制文件暂不支持内联预览，可点击“打开文件”在外部查看。
          </p>
          <p v-else-if="preview?.kind === 'missing'" class="field-hint">
            证据原文件当前未找到。
          </p>
          <p class="field-hint">
            复制的是本次运行的证据路径，可粘贴到问题记录、终端或工单中引用。
          </p>
          <div class="dialog-actions">
            <button
              v-if="hostMode && detail.evidence.sourcePath"
              class="btn btn-primary"
              type="button"
              @click="openEvidenceFile"
            >
              打开文件
            </button>
            <button class="btn" type="button" @click="copyEvidencePath">复制路径</button>
            <button class="btn" type="button" @click="closeDetail">关闭</button>
          </div>
        </template>
      </div>
    </template>
  </dialog>
</template>
