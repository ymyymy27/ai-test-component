<script setup lang="ts">
import { computed } from "vue";

import type { Candidate } from "@/domain/types";

const props = defineProps<{
  candidates: Candidate[];
}>();
const emit = defineEmits<{ view: [] }>();

const titles = computed(() =>
  props.candidates
    .slice(0, 2)
    .map((candidate) => candidate.title)
    .join("；"),
);
</script>

<template>
  <div class="candidate-brief">
    <div class="candidate-brief-main">
      <span class="page-kicker">项目目标候选</span>
      <span class="candidate-brief-title">已整理 {{ candidates.length }} 条候选</span>
      <span class="candidate-brief-text">{{ titles }}</span>
    </div>
    <button class="btn btn-sm" type="button" @click="emit('view')">查看候选</button>
  </div>
</template>
