<script setup lang="ts">
import { computed, ref } from "vue";

import StatusMark from "@/components/StatusMark.vue";
import StatusText from "@/components/StatusText.vue";
import { buildMinimalReproCommand, confidenceTierLabel } from "@/domain/aiPrompt";
import type { EvidenceRef, Report, ReportCaseDetail, Run } from "@/domain/types";
import {
  coverageClass,
  coverageDescription,
  coverageLabel,
  evidenceLabel,
  shortMethod,
} from "./reportFormat";

const props = withDefaults(
  defineProps<{
    report: Report;
    run: Run | null;
    activeItemId: string;
    canOpenSource?: boolean;
  }>(),
  {
    canOpenSource: false,
  },
);

const emit = defineEmits<{
  clearFilter: [];
  showCommand: [testCase: ReportCaseDetail];
  openEvidence: [evidence: EvidenceRef];
  copyFix: [testCase: ReportCaseDetail];
  copyRepro: [command: string];
  markConvention: [testCase: ReportCaseDetail];
  openSource: [file: string, line: number];
}>();

function reproCommandFor(testCase: ReportCaseDetail): string {
  return buildMinimalReproCommand({
    method: testCase.method,
    testNodes: testCase.testNodes,
    file: testCase.file,
  });
}

function confidenceBadgeClass(tier?: ReportCaseDetail["confidence"]): string {
  if (tier === "confirmed") return "is-fail";
  if (tier === "suspected") return "is-warn";
  return "is-neutral";
}

const caseSection = ref<HTMLElement | null>(null);
const activeItem = computed(
  () => props.report.itemDetails.find((item) => item.itemId === props.activeItemId) ?? null,
);
const visibleCases = computed(() => {
  const cases = props.report.caseDetails ?? [];
  return props.activeItemId
    ? cases.filter((testCase) => testCase.itemId === props.activeItemId)
    : cases;
});
const caseNote = computed(() => {
  const total = props.report.caseDetails.length ?? 0;
  return props.activeItemId ? `${visibleCases.value.length} / ${total} 条` : `${total} 条`;
});

function scrollIntoView(): void {
  caseSection.value?.scrollIntoView({ behavior: "smooth", block: "start" });
}

defineExpose({ scrollIntoView });
</script>

<template>
  <section id="case-details" ref="caseSection" class="section">
    <div class="section-head case-section-head">
      <h2 class="section-title-lg">用例明细</h2>
      <span class="section-note">{{ caseNote }}</span>
      <span v-if="activeItem" class="case-filter">
        仅看 {{ activeItem.no }} · {{ activeItem.text }}
        <button class="case-filter-clear" type="button" @click="emit('clearFilter')">清除</button>
      </span>
    </div>
    <div
      v-if="visibleCases.length"
      class="table-wrap report-data-table-wrap case-table-wrap"
      tabindex="0"
      role="region"
      aria-label="用例明细表"
    >
      <table class="table report-data-table is-cases">
        <thead>
          <tr>
            <th scope="col">验收项</th>
            <th scope="col">用例</th>
            <th scope="col">检查方式</th>
            <th scope="col">覆盖</th>
            <th scope="col">结果</th>
            <th scope="col">证据</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="testCase in visibleCases" :key="testCase.caseId">
            <td class="cell-num" data-label="验收项">{{ testCase.itemNo }}</td>
            <td class="cell-main" data-label="用例">
              <details class="case-report-detail">
                <summary>
                  <span>{{ testCase.title }}</span>
                  <span
                    v-if="testCase.confidence"
                    class="badge stage3-inline-badge"
                    :class="confidenceBadgeClass(testCase.confidence)"
                  >
                    {{ confidenceTierLabel(testCase.confidence) }}
                  </span>
                </summary>
                <p><strong>预期</strong>：{{ testCase.expected || "未记录" }}</p>
                <p><strong>实际</strong>：{{ testCase.actual || "未记录" }}</p>
                <p v-if="testCase.phaseFailed">
                  <strong>失败阶段</strong>：<code>{{ testCase.phaseFailed }}</code>
                </p>
                <p v-if="testCase.confidenceReason">
                  <strong>可信度说明</strong>：{{ testCase.confidenceReason }}
                </p>
                <p v-if="testCase.judgement?.reason">
                  <strong>独立裁判</strong>：{{ testCase.judgement.reason }}
                </p>
                <div v-if="testCase.serviceLogIssues?.length" class="stage3-log-box">
                  <strong>窗口期服务异常日志：</strong>
                  <ul class="list-plain">
                    <li v-for="(logLine, idx) in testCase.serviceLogIssues.slice(0, 4)" :key="idx">
                      <code>{{ logLine }}</code>
                    </li>
                  </ul>
                </div>
                <p v-if="testCase.testNodes?.length">
                  <strong>测试节点</strong>：
                  <code class="case-node">{{ testCase.testNodes.join(" · ") }}</code>
                </p>
                <p>
                  <strong>覆盖</strong>：{{ coverageLabel(testCase.coverageKind) }} ·
                  {{ coverageDescription(testCase.coverageKind) }}
                </p>
                <p v-if="testCase.analysis">
                  <strong>分析</strong>：{{ testCase.analysis.observed }}
                </p>
                <div
                  v-if="
                    testCase.result === 'failed' ||
                    testCase.result === 'error' ||
                    testCase.result === 'blocked'
                  "
                  class="stage3-case-actions"
                >
                  <button
                    v-if="reproCommandFor(testCase)"
                    class="btn btn-sm"
                    type="button"
                    @click="emit('copyRepro', reproCommandFor(testCase))"
                  >
                    复制最小复现命令
                  </button>
                  <button
                    class="btn btn-primary btn-sm"
                    type="button"
                    @click="emit('copyFix', testCase)"
                  >
                    复制 AI 修复任务包
                  </button>
                  <button
                    v-if="testCase.file && canOpenSource"
                    class="btn btn-ghost btn-sm"
                    type="button"
                    @click="emit('openSource', testCase.file, Math.max(1, testCase.line ?? 1))"
                  >
                    跳源码
                  </button>
                  <button
                    class="link-btn"
                    type="button"
                    @click="emit('markConvention', testCase)"
                  >
                    标为项目正常约定
                  </button>
                </div>
              </details>
            </td>
            <td data-label="检查方式">
              <button
                class="method-short method-open"
                type="button"
                title="查看完整命令"
                @click="emit('showCommand', testCase)"
              >
                {{ shortMethod(testCase.method) }}
              </button>
            </td>
            <td data-label="覆盖">
              <span class="badge" :class="coverageClass(testCase.coverageKind)">
                {{
                  run?.simulated && !testCase.coverageKind
                    ? "演示未记录"
                    : coverageLabel(testCase.coverageKind)
                }}
              </span>
            </td>
            <td data-label="结果">
              <span class="status-pill" :class="`is-${testCase.result}`">
                <StatusMark :result="testCase.result" />
                <StatusText :result="testCase.result" />
              </span>
            </td>
            <td data-label="证据">
              <template v-if="testCase.evidence.length">
                <button
                  class="link-btn"
                  type="button"
                  :title="testCase.evidence[0]?.sourcePath || testCase.evidence[0]?.name"
                  @click="emit('openEvidence', testCase.evidence[0]!)"
                >
                  {{ evidenceLabel(testCase.evidence[0]!) }}
                </button>
                <span v-if="testCase.evidence.length > 1" class="muted">
                  +{{ testCase.evidence.length - 1 }}
                </span>
              </template>
              <span v-else class="muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="doc-note">
      {{ report.caseDetails.length ? "当前筛选没有用例。" : "这一版没有保留用例明细。" }}
    </p>
  </section>
</template>
