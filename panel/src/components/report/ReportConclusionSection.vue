<script setup lang="ts">
import { computed } from "vue";

import { VERDICT_LABEL } from "@/domain/labels";
import type { Report } from "@/domain/types";

const props = defineProps<{
  report: Report;
}>();

const mockEvaluated = computed(() => {
  const mock = props.report.mock;
  return Boolean(mock && mock.real + mock.mock + mock.mixed + mock.unknown > 0);
});
const realChainText = computed(() =>
  props.report.mock.realChain ? "通过" : mockEvaluated.value ? "未通过" : "未验证",
);
const selectedCount = computed(() => props.report.coverage.selected ?? 0);
const applicableCount = computed(() => props.report.coverage.applicable ?? 0);
const unselectedCount = computed(() =>
  Math.max(0, applicableCount.value - selectedCount.value),
);

const verdictHeadline = computed(() => {
  const current = props.report;
  if (current.verdict === "passed") {
    return selectedCount.value < applicableCount.value ? "本范围通过" : "全部通过";
  }
  if (current.verdict === "failed") {
    return selectedCount.value < applicableCount.value ? "本范围存在失败" : "存在失败";
  }
  if (current.verdict === "incomplete") {
    return selectedCount.value < applicableCount.value ? "本范围未完成" : "运行未完成";
  }
  return VERDICT_LABEL[current.verdict];
});

const coverageSegments = computed(() => {
  const current = props.report;
  if (applicableCount.value === 0) {
    return [];
  }
  const total = applicableCount.value;
  const rows = [
    { key: "passed", label: "通过", count: current.counts.passed },
    { key: "failed", label: "失败", count: current.counts.failed },
    { key: "incomplete", label: "未完成", count: current.counts.incomplete },
    { key: "unselected", label: "未纳入", count: unselectedCount.value },
  ];
  return rows
    .filter((row) => row.count > 0)
    .map((row) => ({ ...row, percent: (row.count / total) * 100 }));
});
</script>

<template>
  <section id="report-conclusion" class="section">
    <div class="result-head report-hero">
      <div>
        <p class="page-kicker">本次结论</p>
        <p class="result-verdict report-hero-title" :class="`is-${report.verdict}`">
          {{ verdictHeadline }}
        </p>
        <p class="report-scope-line">
          <span>
            已选 <strong class="num">{{ selectedCount }}</strong> /
            <strong class="num">{{ applicableCount }}</strong> 项
          </span>
          <span v-if="unselectedCount" class="report-scope-note">
            结论仅对已选范围成立
          </span>
        </p>
        <div
          v-if="coverageSegments.length"
          class="scope-progress"
          role="img"
          :aria-label="`本次覆盖 ${selectedCount} / ${applicableCount} 项，未纳入 ${unselectedCount} 项`"
        >
          <span
            v-for="segment in coverageSegments"
            :key="segment.key"
            class="scope-progress-segment"
            :class="`is-${segment.key}`"
            :style="{ width: `${segment.percent}%` }"
            :title="`${segment.label} ${segment.count}`"
          />
        </div>
        <div v-if="coverageSegments.length" class="scope-legend">
          <span v-for="segment in coverageSegments" :key="segment.key">
            <i class="scope-dot" :class="`is-${segment.key}`" />
            {{ segment.label }} {{ segment.count }}
          </span>
        </div>
        <div class="report-trust">
          <span class="badge is-neutral">证据 {{ report.evidenceIndex.length }}</span>
          <span class="badge is-neutral">调用链 {{ report.link?.nodes.length ?? 0 }} 节点</span>
          <span class="badge is-neutral">模拟 {{ report.mock.mock }} 条</span>
          <span
            class="badge"
            :class="
              report.mock.realChain ? 'is-pass' : mockEvaluated ? 'is-warn' : 'is-neutral'
            "
          >
            真实链路{{ realChainText }}
          </span>
          <template v-if="report.confidenceSummary">
            <span v-if="report.confidenceSummary.confirmed > 0" class="badge is-fail">
              铁证缺陷 {{ report.confidenceSummary.confirmed }}
            </span>
            <span v-if="report.confidenceSummary.suspected > 0" class="badge is-warn">
              疑似问题 {{ report.confidenceSummary.suspected }}
            </span>
            <span v-if="report.confidenceSummary.blocked > 0" class="badge is-neutral">
              环境阻塞 {{ report.confidenceSummary.blocked }}
            </span>
          </template>
          <span v-if="report.tokenUsage?.totalTokens" class="badge is-neutral">
            Token {{ report.tokenUsage.totalTokens }}
          </span>
        </div>
      </div>
      <div class="result-counts" role="group" aria-label="报告计数">
        <div class="result-count is-passed">
          <strong>{{ report.counts.passed }}</strong>
          <span>通过</span>
        </div>
        <div class="result-count is-failed">
          <strong>{{ report.counts.failed }}</strong>
          <span>失败</span>
        </div>
        <div class="result-count is-incomplete">
          <strong>{{ report.counts.incomplete }}</strong>
          <span>未完成</span>
        </div>
        <div class="result-count is-unselected">
          <strong>{{ unselectedCount }}</strong>
          <span>未纳入</span>
        </div>
      </div>
    </div>
  </section>

  <section class="section report-notes">
    <div class="report-note">
      <div class="section-head">
        <h2 class="section-title-lg">摘要</h2>
        <span class="badge is-neutral">
          {{
            report.summarySource === "ai"
              ? "AI 摘要 · 不改变结论"
              : report.summarySource === "none"
                ? "摘要未生成"
                : "本地摘要 · 模型未参与"
          }}
        </span>
      </div>
      <p class="doc-p">{{ report.summary || "本次没有生成摘要。" }}</p>
    </div>
    <div class="report-note">
      <div class="section-head">
        <h2 class="section-title-lg">模拟数据判断</h2>
        <span
          class="badge"
          :class="
            report.mock.realChain ? 'is-pass' : mockEvaluated ? 'is-warn' : 'is-neutral'
          "
        >
          真实链路{{ realChainText }}
        </span>
      </div>
      <p class="doc-p">
        真实 {{ report.mock.real }} · 模拟 {{ report.mock.mock }} · 混合
        {{ report.mock.mixed }} · 无法判断 {{ report.mock.unknown }}。{{
          report.mock.note
        }}
      </p>
      <p class="doc-note">
        含模拟或无法判断依赖的用例，不能获得“真实链路通过”。该判断不可手工修改。
      </p>
    </div>
  </section>
</template>
