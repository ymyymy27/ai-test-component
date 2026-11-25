<script setup lang="ts">
import type { PreflightReport, ProbeFinding, ProbeResult } from "@/domain/types";

withDefaults(
  defineProps<{
    preflight?: PreflightReport | null;
    probeResults?: ProbeResult[];
    canAddConvention?: boolean;
    canOpenSource?: boolean;
  }>(),
  {
    preflight: null,
    probeResults: () => [],
    canAddConvention: true,
    canOpenSource: false,
  },
);

const emit = defineEmits<{
  addConvention: [payload: { pattern: string; reason: string; scope: string }];
  openSource: [file: string, line: number];
}>();

function probeStatusLabel(status: string): string {
  if (status === "passed") return "通过";
  if (status === "failed") return "发现异常";
  if (status === "skipped") return "已跳过";
  return status;
}

function probeBadgeClass(status: string): string {
  if (status === "passed") return "is-pass";
  if (status === "failed") return "is-fail";
  return "is-neutral";
}

function markAsConvention(finding: ProbeFinding): void {
  const pattern = (finding.path || finding.detail || "").trim();
  if (!pattern) return;
  emit("addConvention", {
    pattern,
    reason: "已核实为项目正常约定（来自硬探针降噪）",
    scope: "probe",
  });
}
</script>

<template>
  <section v-if="preflight || probeResults.length" class="section stage3-checks-section">
    <div v-if="preflight" class="preflight-card" :class="preflight.ok ? 'is-passed' : 'is-blocked'">
      <div class="section-head">
        <h3 class="section-title">开测前环境安检门</h3>
        <span class="badge" :class="preflight.ok ? 'is-pass' : 'is-fail'">
          {{ preflight.ok ? "安检通过" : "存在阻塞项" }}
        </span>
      </div>
      <p class="doc-note">
        port 检查 {{ preflight.portChecks.length }} 项 · env 检查 {{ preflight.envChecks.length }} 项 ·
        工具检查 {{ preflight.toolChecks.length }} 项
        <template v-if="preflight.orderedServices.length">
          · 编排服务：{{ preflight.orderedServices.join(" → ") }}
        </template>
      </p>
      <ul v-if="preflight.blockingReasons.length" class="stage3-alert-list is-error">
        <li v-for="(msg, idx) in preflight.blockingReasons" :key="`blk-${idx}`">{{ msg }}</li>
      </ul>
      <ul v-if="preflight.warnings.length" class="stage3-alert-list is-warn">
        <li v-for="(msg, idx) in preflight.warnings" :key="`warn-${idx}`">{{ msg }}</li>
      </ul>
    </div>

    <div v-if="probeResults.length" class="probes-card">
      <div class="section-head">
        <h3 class="section-title">免 AI 确定性硬探针</h3>
        <span class="section-note">{{ probeResults.length }} 项自动核对</span>
      </div>
      <div class="probe-list">
        <article v-for="probe in probeResults" :key="probe.probeId" class="probe-item">
          <div class="probe-item-head">
            <strong>{{ probe.name }}</strong>
            <span class="badge" :class="probeBadgeClass(probe.status)">
              {{ probeStatusLabel(probe.status) }}
            </span>
            <span v-if="probe.suppressedFindings.length" class="badge is-neutral">
              已按错题本豁免 {{ probe.suppressedFindings.length }} 项
            </span>
          </div>
          <p v-if="probe.summary" class="doc-note">{{ probe.summary }}</p>
          <ul v-if="probe.findings.length" class="probe-findings">
            <li v-for="(finding, idx) in probe.findings" :key="`${probe.probeId}-${idx}`">
              <span class="probe-finding-detail">{{ finding.detail }}</span>
              <span class="probe-finding-actions">
                <button
                  v-if="finding.source && canOpenSource"
                  class="link-btn mono"
                  type="button"
                  @click="emit('openSource', finding.source, Math.max(1, finding.line || 1))"
                >
                  {{ finding.source }}
                </button>
                <button
                  v-if="canAddConvention"
                  class="link-btn"
                  type="button"
                  @click="markAsConvention(finding)"
                >
                  标为正常约定
                </button>
              </span>
            </li>
          </ul>
        </article>
      </div>
    </div>
  </section>
</template>
