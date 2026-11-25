<script setup lang="ts">
import LinkPanel from "@/components/LinkPanel.vue";
import {
  ANALYSIS_REASON_LABEL,
  ANALYSIS_REASON_TEXT,
  ISSUE_STATUS_LABEL,
  TEMPLATE_DISPOSITION_LABEL,
} from "@/domain/labels";
import type { EvidenceRef, Issue, Report, Run } from "@/domain/types";
import type { LinkAssertInput } from "@/repositories/ProjectRepository";

defineProps<{
  report: Report;
  run: Run | null;
  issues: Issue[];
  linkBusy: boolean;
}>();

const emit = defineEmits<{
  assertLink: [input: LinkAssertInput];
  openEvidence: [evidence: EvidenceRef];
  openIssue: [issueId: string];
}>();
</script>

<template>
  <section v-if="issues.length" class="section">
    <h2 class="section-title-lg">问题</h2>
    <div class="list-plain">
      <p v-for="issue in issues" :key="issue.id">
        <button
          class="link-btn"
          type="button"
          @click="emit('openIssue', issue.id)"
        >
          {{ issue.severity }} · {{ issue.title }}
        </button>
        <span class="muted"> · {{ ISSUE_STATUS_LABEL[issue.status] }}</span>
      </p>
    </div>
  </section>

  <section v-if="report.analysis.status === 'generated'" class="section">
    <div class="section-head">
      <h2 class="section-title-lg">失败分析</h2>
      <span class="badge is-neutral">本地分析 · 不改变结论</span>
    </div>
    <p class="doc-p">{{ report.analysis.observed }}</p>
    <ul class="list-plain">
      <li v-for="cause in report.analysis.candidateCauses" :key="cause.cause">
        候选原因（把握：{{ cause.confidence }}）：{{ cause.cause }}
      </li>
      <li v-for="item in report.analysis.unverified" :key="item">未验证：{{ item }}</li>
      <li v-for="item in report.analysis.nextSteps" :key="item">下一步：{{ item }}</li>
    </ul>
  </section>
  <section v-else-if="report.counts.failed > 0" class="section">
    <div class="section-head">
      <h2 class="section-title-lg">失败分析</h2>
      <span class="badge is-neutral">
        {{ ANALYSIS_REASON_LABEL[report.analysis.status] }}
      </span>
    </div>
    <p class="doc-p">
      {{ ANALYSIS_REASON_TEXT[report.analysis.reason] || "分析未生成；结论与计数不受影响。" }}
    </p>
    <p class="doc-note">
      需要模型分析时，在设置里检查模型配置后重新生成报告；不会改变本次结论。
    </p>
  </section>

  <section id="report-appendix" class="section">
    <h2 class="section-title-lg">调用链</h2>
    <LinkPanel
      v-if="run"
      :link="report.link"
      :busy="linkBusy"
      @assert="emit('assertLink', $event)"
    />
    <p v-else class="doc-note">运行记录不存在，无法读取调用链。</p>
  </section>

  <section v-if="report.templates.length" class="section">
    <h2 class="section-title-lg">检查模板</h2>
    <div class="stack">
      <div v-for="template in report.templates" :key="template.templateId">
        <p class="doc-p">
          <strong>{{ template.name }} v{{ template.version }}</strong> · 适用
          {{ template.summary.applicable }} · 不适用 {{ template.summary.not_applicable }} ·
          不支持 {{ template.summary.unsupported }} · 无法判断 {{ template.summary.unknown }}
        </p>
        <ul class="list-plain">
          <li v-for="item in template.items" :key="item.id">
            {{ item.objective }} · {{ TEMPLATE_DISPOSITION_LABEL[item.disposition] }} ·
            {{ item.reason }}
          </li>
        </ul>
      </div>
    </div>
  </section>

  <div
    v-if="report.modules.length || report.unmapped.length || report.evidenceIndex.length"
    class="report-pair"
  >
    <section v-if="report.modules.length || report.unmapped.length" class="section">
      <h2 class="section-title-lg">涉及模块与文件</h2>
      <div v-if="report.modules.length" class="list-plain">
        <p v-for="module in report.modules" :key="module.name">
          {{ module.name }} · {{ module.count }} 条检查
        </p>
      </div>
      <p v-if="report.unmapped.length" class="doc-note mono">
        未映射文件：{{ report.unmapped.join("、") }}
      </p>
    </section>

    <section v-if="report.evidenceIndex.length" class="section">
      <h2 class="section-title-lg">证据索引</h2>
      <div class="evidence-list">
        <div v-for="evidence in report.evidenceIndex" :key="evidence.id" class="evidence-item">
          <span aria-hidden="true">•</span>
          <span>
            <span class="evidence-name">{{ evidence.name }}</span>
            <span class="evidence-meta">
              {{ evidence.mediaType }} · {{ evidence.bytes }} 字节 · {{ evidence.sha256 }}
              <template v-if="evidence.note"> · {{ evidence.note }}</template>
            </span>
          </span>
          <span
            class="badge"
            :class="evidence.missing ? 'is-fail' : evidence.truncated ? 'is-warn' : 'is-neutral'"
          >
            {{ evidence.missing ? "已缺失" : evidence.truncated ? "已截断" : "已登记" }}
          </span>
          <button
            class="link-btn"
            type="button"
            :aria-label="`查看证据 ${evidence.name}`"
            @click="emit('openEvidence', evidence)"
          >
            查看
          </button>
        </div>
      </div>
    </section>
  </div>

  <section class="section">
    <h2 class="section-title-lg">来源与环境</h2>
    <dl class="info-grid">
      <div class="info-cell">
        <dt>计划</dt>
        <dd>{{ report.source.planLabel }}</dd>
      </div>
      <div class="info-cell">
        <dt>规则版本</dt>
        <dd>{{ report.source.rulesLabel }}</dd>
      </div>
      <div class="info-cell">
        <dt>源码提交</dt>
        <dd>{{ report.source.commit }}</dd>
      </div>
      <div class="info-cell">
        <dt>模型</dt>
        <dd>{{ report.source.model }}</dd>
      </div>
      <div class="info-cell">
        <dt>GitHub</dt>
        <dd>{{ report.source.github }}</dd>
      </div>
      <div class="info-cell">
        <dt>报告版本</dt>
        <dd>第 {{ report.version }} 版</dd>
      </div>
    </dl>
  </section>
</template>
