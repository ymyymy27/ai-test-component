<script setup lang="ts">
import { computed, ref } from "vue";

import PreflightAndProbesCard from "@/components/PreflightAndProbesCard.vue";
import StatusMark from "@/components/StatusMark.vue";
import StatusText from "@/components/StatusText.vue";
import type { EvidenceRef, ProjectCheck, Report } from "@/domain/types";
import { formatTime, parseUncovered } from "./reportFormat";

const props = withDefaults(
  defineProps<{
    report: Report;
    activeItemId: string;
    canOpenSource?: boolean;
  }>(),
  {
    canOpenSource: false,
  },
);

const emit = defineEmits<{
  filterItem: [itemId: string];
  openEvidence: [evidence: EvidenceRef];
  addConvention: [payload: { pattern: string; reason: string; scope: string }];
  openSource: [file: string, line: number];
}>();

const expandedCheckKey = ref("");
const activeScopeNote = ref<"" | "uncovered" | "unverified" | "warnings">("");

const projectCheckNote = computed(() => {
  const checks = props.report.l1 ?? [];
  const passed = checks.filter((check) => check.result === "passed").length;
  return `${checks.length} 项 · ${passed} 项通过`;
});

function toggleScopeNote(note: "uncovered" | "unverified" | "warnings"): void {
  activeScopeNote.value = activeScopeNote.value === note ? "" : note;
}

function toggleCheck(key: string): void {
  expandedCheckKey.value = expandedCheckKey.value === key ? "" : key;
}

function evidenceForCheck(check: ProjectCheck): EvidenceRef | null {
  const reference = check.evidenceRef?.trim();
  if (!reference) {
    return null;
  }
  return (
    props.report.evidenceIndex.find(
      (evidence) =>
        evidence.id === reference ||
        evidence.name === reference ||
        evidence.sourcePath === reference,
    ) ?? null
  );
}
</script>

<template>
  <section id="report-scope" class="section">
    <h2 class="section-title-lg">范围与覆盖</h2>
    <div class="table-wrap report-data-table-wrap" tabindex="0" role="region" aria-label="范围与覆盖表">
      <table class="table report-data-table">
        <thead>
          <tr>
            <th scope="col">编号</th>
            <th scope="col">验收项</th>
            <th scope="col">最差结果</th>
            <th scope="col">用例数</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in report.itemDetails.filter((entry) => entry.selected)"
            :key="item.itemId"
            :class="{ 'is-filtered': activeItemId === item.itemId }"
          >
            <td class="cell-num" data-label="编号">{{ item.no }}</td>
            <td class="cell-main" data-label="验收项">
              <button
                class="row-link row-filter"
                type="button"
                :aria-pressed="activeItemId === item.itemId"
                title="在下方用例明细中筛选"
                @click="emit('filterItem', item.itemId)"
              >
                <span>{{ item.text }}</span>
                <span class="row-filter-hint">
                  {{ activeItemId === item.itemId ? "已筛选" : "筛选" }}
                </span>
              </button>
            </td>
            <td data-label="最差结果">
              <StatusMark :result="item.outcome" />
              <StatusText :result="item.outcome" />
            </td>
            <td data-label="用例数">{{ item.caseIds.length }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div
      v-if="
        report.uncoveredItems.length ||
        report.unverifiedItems.length ||
        report.planWarnings?.length
      "
      class="scope-notes"
    >
      <button
        v-if="report.uncoveredItems.length"
        class="scope-note-btn"
        type="button"
        :aria-expanded="activeScopeNote === 'uncovered'"
        @click="toggleScopeNote('uncovered')"
      >
        未纳入 {{ report.uncoveredItems.length }} 项
        <span class="scope-note-caret" aria-hidden="true" />
      </button>
      <button
        v-if="report.unverifiedItems.length"
        class="scope-note-btn"
        type="button"
        :aria-expanded="activeScopeNote === 'unverified'"
        @click="toggleScopeNote('unverified')"
      >
        未核实预期 {{ report.unverifiedItems.length }} 条
        <span class="scope-note-caret" aria-hidden="true" />
      </button>
      <button
        v-if="report.planWarnings?.length"
        class="scope-note-btn"
        type="button"
        :aria-expanded="activeScopeNote === 'warnings'"
        @click="toggleScopeNote('warnings')"
      >
        计划警告 {{ report.planWarnings.length }} 条
        <span class="scope-note-caret" aria-hidden="true" />
      </button>
    </div>
    <div v-if="activeScopeNote === 'uncovered'" class="scope-note-panel">
      <p class="scope-note-hint">未纳入项不计入本次结论。</p>
      <div class="coverage-chip-list">
        <span
          v-for="item in report.uncoveredItems"
          :key="item"
          class="coverage-chip"
          :title="parseUncovered(item).id || item"
        >
          {{ parseUncovered(item).text }}
        </span>
      </div>
    </div>
    <div v-if="activeScopeNote === 'unverified'" class="scope-note-panel">
      <p class="scope-note-hint">执行通过也按阻塞计。</p>
      <ul class="list-plain">
        <li v-for="item in report.unverifiedItems" :key="item">{{ item }}</li>
      </ul>
    </div>
    <div v-if="activeScopeNote === 'warnings'" class="scope-note-panel">
      <ul class="list-plain">
        <li v-for="item in report.planWarnings ?? []" :key="item">{{ item }}</li>
      </ul>
    </div>
  </section>

  <section id="report-checks" class="section">
    <div class="section-head">
      <h2 class="section-title-lg">项目级检查</h2>
      <span v-if="report.l1.length" class="section-note">{{ projectCheckNote }}</span>
    </div>
    <div v-if="report.l1.length" class="table-wrap report-data-table-wrap" tabindex="0" role="region" aria-label="项目级检查表">
      <table class="table report-data-table">
        <thead>
          <tr>
            <th scope="col">检查项</th>
            <th scope="col">检查方式</th>
            <th scope="col">结果</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="check in report.l1" :key="check.key">
            <tr :class="{ 'is-open': expandedCheckKey === check.key }">
              <td class="cell-main" data-label="检查项">
                <button
                  class="row-toggle"
                  type="button"
                  :aria-expanded="expandedCheckKey === check.key"
                  @click="toggleCheck(check.key)"
                >
                  <StatusMark :result="check.result" />
                  <span class="row-toggle-text">{{ check.name }}</span>
                  <span
                    class="report-toggle-chevron"
                    :class="{ 'is-open': expandedCheckKey === check.key }"
                    aria-hidden="true"
                  />
                </button>
              </td>
              <td data-label="检查方式"><code class="check-method-chip">{{ check.method }}</code></td>
              <td data-label="结果">
                <span class="status-pill" :class="`is-${check.result}`">
                  <StatusText :result="check.result" />
                </span>
                <span v-if="check.reason" class="muted"> · {{ check.reason }}</span>
              </td>
            </tr>
            <tr v-if="expandedCheckKey === check.key" class="detail-row">
              <td colspan="3">
                <div class="check-detail">
                  <p class="check-detail-text">{{ check.detail || "没有附加说明。" }}</p>
                  <p v-if="check.reason" class="check-detail-text">
                    未通过原因：{{ check.reason }}
                  </p>
                  <div class="check-detail-meta">
                    <span v-if="check.time">{{ formatTime(check.time) }}</span>
                    <button
                      v-if="evidenceForCheck(check)"
                      class="link-btn"
                      type="button"
                      @click="emit('openEvidence', evidenceForCheck(check)!)"
                    >
                      查看证据
                    </button>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
    <p v-else class="report-empty-note">
      本次没有项目级检查记录；快速检查或计划中含 L1 用例时会显示在这里。
    </p>
  </section>

  <PreflightAndProbesCard
    :preflight="report.preflight ?? null"
    :probe-results="report.probeResults ?? []"
    :can-open-source="canOpenSource"
    @add-convention="emit('addConvention', $event)"
    @open-source="(file, line) => emit('openSource', file, line)"
  />
</template>
