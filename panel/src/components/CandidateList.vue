<script setup lang="ts">
import type { Candidate } from "@/domain/types";

defineProps<{
  candidates: Candidate[];
  selectedId: string | null;
  busy: boolean;
}>();

const emit = defineEmits<{
  select: [candidateId: string];
  create: [];
}>();
</script>

<template>
  <section class="candidate-section" aria-labelledby="candidate-title">
    <div class="section-head">
      <span id="candidate-title" class="section-title">或者从目标候选开始</span>
      <span class="section-note">依据本地分析；不声称来自真实提交或问题</span>
    </div>
    <div
      v-if="candidates.length"
      class="candidate-list"
      role="radiogroup"
      aria-label="验证目标候选"
    >
      <label v-for="candidate in candidates" :key="candidate.id" class="candidate">
        <input
          type="radio"
          name="target-candidate"
          :value="candidate.id"
          :checked="candidate.id === selectedId"
          @change="emit('select', candidate.id)"
        />
        <span>
          <span class="candidate-title">{{ candidate.title }}</span>
          <span class="candidate-meta">
            依据：{{ candidate.reason }} · {{ candidate.source }}
          </span>
          <span class="candidate-scope">
            预计范围：{{ candidate.estimatedScope }} · 不确定：{{ candidate.unknown }}
          </span>
        </span>
      </label>
    </div>
    <p v-else class="doc-note">还没有生成候选；可以直接在上方写一句目标。</p>
    <div class="candidate-foot">
      <p class="field-hint">候选只提供建议；确认后会创建任务与验收项。首个任务同时写入项目目标。</p>
      <button
        class="btn"
        type="button"
        :disabled="!selectedId || busy"
        :aria-busy="busy"
        @click="emit('create')"
      >
        {{ busy ? "正在创建任务…" : "用选中候选创建任务" }}
      </button>
    </div>
  </section>
</template>
