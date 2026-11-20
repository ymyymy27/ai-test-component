<script setup lang="ts">
import { computed } from "vue";

import type { Feature, Module, ProjectMap } from "@/domain/types";
import Icon from "./Icon.vue";

const props = defineProps<{
  map: ProjectMap | null;
  modules: Module[];
  features: Feature[];
  ignoredFeatures: string[];
  catalogCount: number;
  analyzing: boolean;
  busy: boolean;
}>();

const emit = defineEmits<{
  analyze: [];
  addModule: [];
  editModule: [module: Module];
  toggleFeature: [featureName: string, ignored: boolean];
}>();

const ignoredSet = computed(() => new Set(props.ignoredFeatures));
const activeFeatures = computed(() =>
  props.features.filter((feature) => !ignoredSet.value.has(feature.name)),
);
const visibleFeatures = computed(() => activeFeatures.value.slice(0, 4));
const ignoredRows = computed(() =>
  props.features.filter((feature) => ignoredSet.value.has(feature.name)),
);
const unknowns = computed(() => props.map?.unknowns ?? []);
</script>

<template>
  <section class="section project-map">
    <div class="section-head">
      <span class="section-title">项目理解</span>
      <button class="link-btn" type="button" :disabled="busy || analyzing" @click="emit('analyze')">
        {{ analyzing || busy ? "分析中…" : "重新分析" }}
      </button>
    </div>
    <p class="project-map-stats">
      <strong class="num">{{ activeFeatures.length }}</strong> 个业务功能 ·
      <strong class="num">{{ catalogCount }}</strong> 个可测项
    </p>
    <p v-if="map?.stale" class="notice is-warn">
      <Icon name="warn" :size="14" />
      <span>{{ map.staleReason || "项目地图已过期，重新分析后再使用。" }}</span>
    </p>

    <div v-if="visibleFeatures.length" class="project-map-list">
      <div v-for="feature in visibleFeatures" :key="feature.id" class="project-map-row">
        <span>{{ feature.name }}</span>
        <small>{{ feature.modules.join(" · ") }}</small>
        <em>{{
          feature.confidence === "high"
            ? "把握高"
            : feature.confidence === "medium"
              ? "把握中"
              : "把握低"
        }}</em>
        <button
          class="link-btn"
          type="button"
          :disabled="busy"
          :aria-label="`忽略业务功能 ${feature.name}`"
          @click="emit('toggleFeature', feature.name, true)"
        >
          忽略
        </button>
      </div>
    </div>
    <p v-else class="doc-note">
      {{
        analyzing
          ? "正在识别业务功能…"
          : activeFeatures.length === 0 && ignoredRows.length > 0
            ? "当前业务功能都被忽略了；可以从已忽略列表恢复。"
            : "还没有识别到业务功能；可以改用目标测试。"
      }}
    </p>

    <details v-if="ignoredRows.length" class="project-map-details">
      <summary>
        <span>已忽略 {{ ignoredRows.length }} 个</span>
      </summary>
      <div class="project-map-list">
        <div v-for="feature in ignoredRows" :key="feature.id" class="project-map-row">
          <span>{{ feature.name }}</span>
          <small>{{ feature.modules.join(" · ") }}</small>
          <button
            class="link-btn"
            type="button"
            :disabled="busy"
            @click="emit('toggleFeature', feature.name, false)"
          >
            恢复
          </button>
        </div>
      </div>
    </details>

    <details class="project-map-details">
      <summary>
        <span>模块 {{ modules.length }} 个</span>
      </summary>
      <div class="row project-map-actions">
        <button class="link-btn" type="button" @click="emit('addModule')">新增模块</button>
      </div>
      <div v-if="modules.length" class="project-map-list">
        <div v-for="module in modules" :key="module.id" class="project-map-row">
          <span>{{ module.name }}</span>
          <small>{{ module.responsibility || "责任未填写" }}</small>
          <em>{{ module.status }}</em>
          <button class="link-btn" type="button" @click="emit('editModule', module)">编辑</button>
        </div>
      </div>
      <p v-else class="doc-note">还没有模块。新增模块后，验收项可以关联到模块。</p>
    </details>

    <details class="project-map-details">
      <summary>
        <span>未知项 {{ unknowns.length }} 个</span>
        <span class="muted">不计入通过</span>
      </summary>
      <ul v-if="unknowns.length" class="unknown-list">
        <li v-for="unknown in unknowns" :key="unknown.id">
          <strong>{{ unknown.area }}</strong>
          <span>{{ unknown.reason }}</span>
        </li>
      </ul>
      <p v-else class="doc-note">没有登记未知项。</p>
    </details>
  </section>
</template>
