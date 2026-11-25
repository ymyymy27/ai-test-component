<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import type { CodeGraph, CodeGraphLayer, CodeGraphNode } from "@/domain/types";
import { downloadText } from "@/services/exportService";
import {
  type ArchCard,
  type ArchStory,
  buildArchitecturePanorama,
  buildArchitecturePanoramaSvg,
} from "./codeGraphArchitecture";
import {
  buildStandaloneCodeGraphSvg,
  COL_X,
  computeNodeDegrees,
  LAYER_ORDER,
  NODE_CAP,
  NODE_H,
  NODE_W,
  pickBalancedNodes,
  type PositionedNode,
  ROW_GAP,
  traceBidirectionalChain,
} from "./codeGraphHelpers";
import Icon from "./Icon.vue";

const props = withDefaults(
  defineProps<{
    graph?: CodeGraph | null;
    projectName?: string;
    canOpenSource?: boolean;
  }>(),
  {
    graph: null,
    projectName: "",
    canOpenSource: true,
  },
);

const emit = defineEmits<{
  openSource: [file: string, line: number];
  testAffected: [goalText: string];
  refresh: [];
  exportSvg: [svgText: string];
}>();

const viewMode = ref<"architecture" | "graph" | "list">("architecture");

// 精确链路视角状态
const zoom = ref(1);
const panX = ref(0);
const panY = ref(0);
const dragging = ref(false);
let dragStartX = 0;
let dragStartY = 0;
let basePanX = 0;
let basePanY = 0;

// 架构全景视角状态
const archZoom = ref(1);
const archPanX = ref(0);
const archPanY = ref(0);
const archDragging = ref(false);
let archDragStartX = 0;
let archDragStartY = 0;
let archBasePanX = 0;
let archBasePanY = 0;

const activeStoryId = ref<ArchStory["id"]>("overview");
const storyPlaying = ref(false);
let storyTimer: number | null = null;
const selectedCardId = ref("");

const filterLayer = ref<CodeGraphLayer | "all">("all");
const filterModule = ref("all");
const onlyImpact = ref(false);
const onlyConflict = ref(false);
const keyword = ref("");
const showAllNodes = ref(false);
const selectedNodeId = ref("");
const focusChainOnly = ref(false);
const exportNotice = ref("");
const viewportEl = ref<HTMLDivElement | null>(null);
const archViewportEl = ref<HTMLDivElement | null>(null);
const searchEl = ref<HTMLInputElement | null>(null);

const allNodes = computed(() => props.graph?.nodes ?? []);
const allEdges = computed(() => props.graph?.edges ?? []);
const conflicts = computed(() => props.graph?.conflicts ?? []);
const degreeStats = computed(() => computeNodeDegrees(allEdges.value));

const panorama = computed(() => {
  if (!props.graph) return null;
  return buildArchitecturePanorama(props.graph, props.projectName);
});

const activeStory = computed<ArchStory | null>(
  () => panorama.value?.stories.find((s) => s.id === activeStoryId.value) ?? null,
);

const storyStepIndex = computed(() => {
  const idx = panorama.value?.stories.findIndex((s) => s.id === activeStoryId.value) ?? -1;
  return idx >= 0 ? idx + 1 : 0;
});

const highlightedCardSet = computed(() => {
  if (!activeStory.value) return null;
  return new Set(activeStory.value.highlightedCardIds);
});

const highlightedRelSet = computed(() => {
  if (!activeStory.value) return null;
  return new Set(activeStory.value.highlightedRelIds);
});

const selectedArchCard = computed<ArchCard | null>(
  () => panorama.value?.cards.find((c) => c.id === selectedCardId.value) ?? null,
);

const selectedArchCardConflicts = computed(() => {
  const card = selectedArchCard.value;
  if (!card || card.nodeIds.length === 0) return [];
  const cardNodeSet = new Set(card.nodeIds);
  return conflicts.value.filter((c) => c.nodeIds.some((id) => cardNodeSet.has(id))).slice(0, 8);
});

const modules = computed(() => {
  const set = new Set<string>();
  for (const node of allNodes.value) {
    if (node.module) set.add(node.module);
  }
  return [...set].sort();
});

const traceResult = computed(() => traceBidirectionalChain(selectedNodeId.value, allEdges.value));

const filteredNodes = computed(() => {
  const q = keyword.value.trim().toLowerCase();
  const focusSet = focusChainOnly.value && selectedNodeId.value ? traceResult.value.nodeIds : null;
  return allNodes.value.filter((node) => {
    if (focusSet && !focusSet.has(node.id)) return false;
    if (filterLayer.value !== "all" && node.layer !== filterLayer.value) return false;
    if (filterModule.value !== "all" && node.module !== filterModule.value) return false;
    if (onlyImpact.value && node.impact === "none") return false;
    if (onlyConflict.value && !node.hasConflict) return false;
    if (q) {
      const hay = `${node.label} ${node.file} ${node.module} ${node.kind}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
});

const cappedNotice = computed(
  () =>
    !focusChainOnly.value && !showAllNodes.value && filteredNodes.value.length > NODE_CAP,
);

const visibleNodes = computed(() => {
  if (showAllNodes.value || focusChainOnly.value) {
    return filteredNodes.value;
  }
  return pickBalancedNodes(filteredNodes.value, NODE_CAP, degreeStats.value.total);
});

const visibleNodeIds = computed(() => new Set(visibleNodes.value.map((n) => n.id)));

const layoutData = computed(() => {
  const byLayer: Record<CodeGraphLayer, CodeGraphNode[]> = {
    frontend: [],
    api: [],
    backend: [],
    storage: [],
  };
  for (const node of visibleNodes.value) {
    byLayer[node.layer].push(node);
  }
  const positions = new Map<string, PositionedNode>();
  let maxRows = 1;
  for (const layerMeta of LAYER_ORDER) {
    const list = byLayer[layerMeta.key];
    if (list.length > maxRows) maxRows = list.length;
    list.forEach((node, index) => {
      positions.set(node.id, {
        node,
        x: COL_X[layerMeta.key],
        y: 54 + index * (NODE_H + ROW_GAP),
      });
    });
  }
  const canvasHeight = Math.max(260, 76 + maxRows * (NODE_H + ROW_GAP));
  const canvasWidth = 1012;
  return { byLayer, positions, canvasWidth, canvasHeight };
});

const relatedNodeIds = computed(() => {
  if (!selectedNodeId.value) return new Set<string>();
  return traceResult.value.nodeIds;
});

const visibleEdges = computed(() => {
  const valid = visibleNodeIds.value;
  const tracedEdges = traceResult.value.edgeIds;
  return allEdges.value
    .filter((edge) => valid.has(edge.source) && valid.has(edge.target))
    .map((edge) => {
      const src = layoutData.value.positions.get(edge.source);
      const dst = layoutData.value.positions.get(edge.target);
      if (!src || !dst) return null;
      const startX = src.x + NODE_W;
      const startY = src.y + NODE_H / 2;
      const endX = dst.x;
      const endY = dst.y + NODE_H / 2;
      const dx = Math.max(40, Math.abs(endX - startX) * 0.45);
      const d = `M ${startX} ${startY} C ${startX + dx} ${startY}, ${endX - dx} ${endY}, ${endX} ${endY}`;
      const highlighted =
        selectedNodeId.value !== "" &&
        (tracedEdges.has(edge.id) ||
          edge.source === selectedNodeId.value ||
          edge.target === selectedNodeId.value);
      return {
        edge,
        d,
        highlighted,
        dimmed: selectedNodeId.value !== "" && !highlighted,
      };
    })
    .filter((item): item is NonNullable<typeof item> => item !== null);
});

const selectedNode = computed<CodeGraphNode | null>(
  () => allNodes.value.find((n) => n.id === selectedNodeId.value) ?? null,
);

const tracedPathLayers = computed(() => {
  if (!selectedNodeId.value) return [];
  const ids = traceResult.value.nodeIds;
  const tracedNodes = allNodes.value.filter((n) => ids.has(n.id));
  return LAYER_ORDER.map((layer) => ({
    key: layer.key,
    label: layer.shortLabel,
    nodes: tracedNodes.filter((n) => n.layer === layer.key).slice(0, 6),
  })).filter((group) => group.nodes.length > 0);
});

const selectedNeighborNodes = computed(() => {
  if (!selectedNodeId.value) return [];
  const ids = relatedNodeIds.value;
  return allNodes.value.filter((n) => n.id !== selectedNodeId.value && ids.has(n.id));
});

function changeZoom(delta: number): void {
  zoom.value = Math.min(1.5, Math.max(0.45, Number((zoom.value + delta).toFixed(2))));
}

function resetView(): void {
  zoom.value = 1;
  panX.value = 0;
  panY.value = 0;
}

function fitToView(): void {
  const vp = viewportEl.value;
  const cw = layoutData.value.canvasWidth;
  if (!vp || cw <= 0) {
    resetView();
    return;
  }
  const availW = Math.max(320, vp.clientWidth - 24);
  const nextZoom = Math.min(1.15, Math.max(0.48, Number((availW / cw).toFixed(2))));
  zoom.value = nextZoom;
  panX.value = Math.max(0, Math.round((vp.clientWidth - cw * nextZoom) / 2));
  panY.value = 0;
}

function changeArchZoom(delta: number): void {
  archZoom.value = Math.min(1.5, Math.max(0.5, Number((archZoom.value + delta).toFixed(2))));
}

function resetArchView(): void {
  archZoom.value = 1;
  archPanX.value = 0;
  archPanY.value = 0;
}

function fitArchToView(): void {
  const vp = archViewportEl.value;
  const model = panorama.value;
  if (!vp || !model || vp.clientWidth <= 0 || model.canvasWidth <= 0) {
    resetArchView();
    return;
  }
  const availW = Math.max(220, vp.clientWidth - 32);
  const nextZoom = Math.min(1.05, Math.max(0.32, Number((availW / model.canvasWidth).toFixed(2))));
  archZoom.value = nextZoom;
  archPanX.value = Math.max(8, Math.round((vp.clientWidth - model.canvasWidth * nextZoom) / 2));
  archPanY.value = 0;
}

watch(
  () => [props.graph?.sourceHash, viewMode.value] as const,
  async () => {
    if (viewMode.value === "architecture") {
      await nextTick();
      fitArchToView();
    }
  },
);

onMounted(() => {
  if (viewMode.value === "architecture") {
    void nextTick(() => fitArchToView());
  }
});

onBeforeUnmount(() => {
  stopStoryPlay();
});

function stopStoryPlay(): void {
  storyPlaying.value = false;
  if (storyTimer !== null) {
    window.clearInterval(storyTimer);
    storyTimer = null;
  }
}

function toggleStory(storyId: ArchStory["id"]): void {
  stopStoryPlay();
  activeStoryId.value = activeStoryId.value === storyId ? "overview" : storyId;
}

function togglePlayStory(): void {
  if (storyPlaying.value) {
    stopStoryPlay();
    return;
  }
  const order: ArchStory["id"][] = ["main_flow", "risk_hotspots", "git_impact", "overview"];
  storyPlaying.value = true;
  let stepIdx = Math.max(0, order.indexOf(activeStoryId.value));
  activeStoryId.value = order[stepIdx] ?? "main_flow";
  storyTimer = window.setInterval(() => {
    stepIdx = (stepIdx + 1) % order.length;
    activeStoryId.value = order[stepIdx] ?? "overview";
    if (activeStoryId.value === "overview") {
      stopStoryPlay();
    }
  }, 2200);
}

function selectArchCard(card: ArchCard): void {
  selectedCardId.value = selectedCardId.value === card.id ? "" : card.id;
}

function drillDownCard(card: ArchCard): void {
  stopStoryPlay();
  selectedCardId.value = "";
  filterLayer.value = "all";
  keyword.value = "";
  onlyImpact.value = false;
  onlyConflict.value = false;
  focusChainOnly.value = false;
  if (!card.isAggregateOthers && card.moduleKey !== "__all__" && card.moduleKey !== "__external__") {
    filterModule.value = modules.value.includes(card.moduleKey) ? card.moduleKey : "all";
  } else {
    filterModule.value = "all";
  }
  selectedNodeId.value = card.nodeIds[0] ?? "";
  viewMode.value = "graph";
  void nextTick(() => fitToView());
}

function returnToArchitecture(): void {
  filterModule.value = "all";
  filterLayer.value = "all";
  focusChainOnly.value = false;
  selectedNodeId.value = "";
  viewMode.value = "architecture";
}

function onPointerDown(event: PointerEvent): void {
  const target = event.target as HTMLElement | null;
  if (target?.closest("button, a, input, select")) {
    return;
  }
  dragging.value = true;
  dragStartX = event.clientX;
  dragStartY = event.clientY;
  basePanX = panX.value;
  basePanY = panY.value;
}

function onPointerMove(event: PointerEvent): void {
  if (!dragging.value) return;
  panX.value = basePanX + (event.clientX - dragStartX);
  panY.value = basePanY + (event.clientY - dragStartY);
}

function onPointerUp(): void {
  dragging.value = false;
}

function onArchPointerDown(event: PointerEvent): void {
  const target = event.target as HTMLElement | null;
  if (target?.closest("button, a, input, select")) return;
  archDragging.value = true;
  archDragStartX = event.clientX;
  archDragStartY = event.clientY;
  archBasePanX = archPanX.value;
  archBasePanY = archPanY.value;
}

function onArchPointerMove(event: PointerEvent): void {
  if (!archDragging.value) return;
  archPanX.value = archBasePanX + (event.clientX - archDragStartX);
  archPanY.value = archBasePanY + (event.clientY - archDragStartY);
}

function onArchPointerUp(): void {
  archDragging.value = false;
}

function onViewportWheel(event: WheelEvent): void {
  if (!event.ctrlKey && !event.metaKey) return;
  event.preventDefault();
  changeZoom(event.deltaY < 0 ? 0.1 : -0.1);
}

function onArchViewportWheel(event: WheelEvent): void {
  if (!event.ctrlKey && !event.metaKey) return;
  event.preventDefault();
  changeArchZoom(event.deltaY < 0 ? 0.1 : -0.1);
}

function onSectionKeydown(event: KeyboardEvent): void {
  const tag = (event.target as HTMLElement | null)?.tagName.toLowerCase();
  const inInput = tag === "input" || tag === "textarea" || tag === "select";
  if (event.key === "Escape") {
    if (selectedCardId.value) {
      selectedCardId.value = "";
    } else if (activeStoryId.value !== "overview") {
      activeStoryId.value = "overview";
      stopStoryPlay();
    } else if (focusChainOnly.value) {
      focusChainOnly.value = false;
    } else if (selectedNodeId.value) {
      selectedNodeId.value = "";
    }
    return;
  }
  if (inInput) return;
  if (event.key === "/" && viewMode.value !== "architecture") {
    event.preventDefault();
    searchEl.value?.focus();
  } else if (event.key === "0") {
    if (viewMode.value === "architecture") resetArchView();
    else resetView();
  }
}

function selectNode(node: CodeGraphNode): void {
  const next = selectedNodeId.value === node.id ? "" : node.id;
  selectedNodeId.value = next;
  if (!next) {
    focusChainOnly.value = false;
  }
}

function toggleFocusChain(): void {
  if (!selectedNodeId.value) return;
  focusChainOnly.value = !focusChainOnly.value;
  if (focusChainOnly.value) {
    void nextTick(() => fitToView());
  }
}

async function locateNodeById(nodeId: string): Promise<void> {
  if (!nodeId) return;
  viewMode.value = "graph";
  focusChainOnly.value = false;
  if (!visibleNodeIds.value.has(nodeId)) {
    showAllNodes.value = true;
  }
  selectedNodeId.value = nodeId;
  await nextTick();
  const pos = layoutData.value.positions.get(nodeId);
  const vp = viewportEl.value;
  if (pos && vp && typeof vp.scrollTo === "function") {
    vp.scrollTo({
      top: Math.max(0, pos.y * zoom.value - vp.clientHeight / 3),
      left: Math.max(0, pos.x * zoom.value - vp.clientWidth / 3),
      behavior: "smooth",
    });
  }
}

function exportGraphSvg(): void {
  if (viewMode.value === "architecture" && panorama.value) {
    const svg = buildArchitecturePanoramaSvg(panorama.value, props.projectName);
    emit("exportSvg", svg);
    const downloaded = downloadText("architecture-panorama.svg", svg, "image/svg+xml");
    exportNotice.value = downloaded
      ? `已导出 SVG 架构全景图（${panorama.value.cards.length} 模块 / ${panorama.value.relations.length} 连线）`
      : `已生成 SVG 架构全景图（${panorama.value.cards.length} 模块 / ${panorama.value.relations.length} 连线）`;
    return;
  }
  const svg = buildStandaloneCodeGraphSvg({
    width: layoutData.value.canvasWidth,
    height: layoutData.value.canvasHeight,
    nodes: visibleNodes.value,
    positions: layoutData.value.positions,
    edges: visibleEdges.value,
    selectedId: selectedNodeId.value,
  });
  emit("exportSvg", svg);
  const downloaded = downloadText("code-graph-architecture.svg", svg, "image/svg+xml");
  exportNotice.value = downloaded
    ? `已导出 SVG 链路图（${visibleNodes.value.length} 节点 / ${visibleEdges.value.length} 连线）`
    : `已生成 SVG 矢量图（${visibleNodes.value.length} 节点 / ${visibleEdges.value.length} 连线）`;
}

function impactBadgeText(impact: CodeGraphNode["impact"] | ArchCard["impact"]): string {
  if (impact === "direct") return "直接改动";
  if (impact === "transitive") return "波及影响";
  return "";
}

function bandIconName(band: ArchCard["band"]): "layout" | "server" | "database" | "cloud" {
  switch (band) {
    case "frontend":
      return "layout";
    case "backend":
      return "server";
    case "storage":
      return "database";
    case "external":
      return "cloud";
  }
}

function testSelectedNode(): void {
  const node = selectedNode.value;
  if (!node) return;
  const scopeDesc = node.layer === "api" ? `接口 ${node.label}` : `模块 ${node.module} (${node.label})`;
  emit("testAffected", `专项回归测试 ${scopeDesc}`);
}

function labelPillWidth(label: string): number {
  return Math.max(66, Math.min(136, label.length * 8 + 16));
}
</script>

<template>
  <section
    class="section code-graph-section"
    aria-label="模块依赖与调用链图"
    @keydown="onSectionKeydown"
  >
    <div class="section-head code-graph-head">
      <div>
        <h2 class="section-title">
          <span>{{ projectName ? `${projectName} · 模块依赖` : "模块依赖" }}</span>
        </h2>
        <p v-if="graph" class="code-graph-stats">
          共 <strong class="num">{{ graph.stats.totalFiles }}</strong> 文件 · 前端
          <strong class="num">{{ graph.stats.frontend }}</strong> · 接口
          <strong class="num">{{ graph.stats.api }}</strong> · 后端
          <strong class="num">{{ graph.stats.backend }}</strong> · 存储
          <strong class="num">{{ graph.stats.storage }}</strong> · 连线
          <strong class="num">{{ graph.stats.edges }}</strong>
          <template v-if="graph.stats.conflicts > 0">
            · <span class="is-conflict-text">跨层问题 {{ graph.stats.conflicts }}</span>
          </template>
        </p>
      </div>
      <div class="code-graph-mode-actions">
        <div class="btn-group" role="group" aria-label="视图切换">
          <button
            class="filter-btn"
            :class="{ 'is-active': viewMode === 'architecture' }"
            type="button"
            @click="viewMode = 'architecture'"
          >
            模块视图
          </button>
          <button
            class="filter-btn"
            :class="{ 'is-active': viewMode === 'graph' }"
            type="button"
            @click="viewMode = 'graph'"
          >
            精确链路
          </button>
          <button
            class="filter-btn"
            :class="{ 'is-active': viewMode === 'list' }"
            type="button"
            @click="viewMode = 'list'"
          >
            列表
          </button>
        </div>
        <button
          v-if="graph && allNodes.length > 0 && viewMode !== 'list'"
          class="btn btn-sm btn-ghost"
          type="button"
          @click="exportGraphSvg"
        >
          导出 SVG
        </button>
        <button class="btn btn-sm btn-ghost" type="button" @click="emit('refresh')">
          重新索引
        </button>
      </div>
    </div>

    <div v-if="!graph || allNodes.length === 0" class="code-graph-empty">
      <p class="doc-note">暂无可展示的代码链路节点，请点击上方“重新索引”扫描本地项目源码。</p>
    </div>

    <template v-else>
      <!-- 一、参考图风格：宏观系统架构全景视图 -->
      <template v-if="viewMode === 'architecture' && panorama">
        <div class="arch-story-bar" aria-label="引导视图">
          <div class="arch-story-status">
            <div class="arch-story-step-meta">
              <span class="muted">引导视图</span>
              <strong class="mono">{{ storyStepIndex }} / {{ panorama.stories.length }}</strong>
            </div>
            <div class="arch-story-progress">
              <div
                class="arch-story-progress-fill"
                :style="{ width: `${(storyStepIndex / Math.max(1, panorama.stories.length)) * 100}%` }"
              />
            </div>
            <button class="btn btn-sm btn-ghost arch-play-btn" type="button" @click="togglePlayStory">
              {{ storyPlaying ? "⏸ 暂停导览" : "▶ 播放导览" }}
            </button>
          </div>

          <div class="arch-story-pills">
            <button
              v-for="story in panorama.stories"
              :key="story.id"
              class="arch-story-pill"
              :class="{ 'is-active': activeStoryId === story.id }"
              type="button"
              @click="toggleStory(story.id)"
            >
              <span class="arch-story-head">
                <span class="arch-story-num">{{ story.stepNo }}</span>
                <span class="arch-story-title">{{ story.title }}</span>
              </span>
              <span class="arch-story-sub">{{ story.subtitle }}</span>
            </button>
          </div>
        </div>

        <p v-if="exportNotice" class="code-graph-export-note">{{ exportNotice }}</p>

        <div
          ref="archViewportEl"
          class="arch-viewport"
          :class="{ 'is-dragging': archDragging }"
          @pointerdown="onArchPointerDown"
          @pointermove="onArchPointerMove"
          @pointerup="onArchPointerUp"
          @pointercancel="onArchPointerUp"
          @wheel="onArchViewportWheel"
        >
          <div
            class="arch-stage"
            :style="{
              width: `${panorama.canvasWidth}px`,
              height: `${panorama.canvasHeight}px`,
              transform: `translate(${archPanX}px, ${archPanY}px) scale(${archZoom})`,
            }"
          >
            <svg
              class="arch-svg"
              :width="panorama.canvasWidth"
              :height="panorama.canvasHeight"
              aria-hidden="true"
            >
              <defs>
                <marker
                  id="arch-arrow-slate"
                  viewBox="0 0 10 10"
                  refX="8"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 1 2 L 9 5 L 1 8 z" fill="currentColor" />
                </marker>
                <marker
                  id="arch-arrow-red"
                  viewBox="0 0 10 10"
                  refX="8"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 1 2 L 9 5 L 1 8 z" class="cg-marker-conflict" />
                </marker>
                <marker
                  id="arch-arrow-accent"
                  viewBox="0 0 10 10"
                  refX="8"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 1 2 L 9 5 L 1 8 z" class="cg-marker-accent" />
                </marker>
              </defs>

              <g
                v-for="rel in panorama.relations"
                :key="rel.id"
                class="arch-rel-group"
                :class="{
                  'is-conflict': rel.conflict,
                  'is-external': rel.kind === 'external',
                  'is-highlighted':
                    (highlightedRelSet && highlightedRelSet.has(rel.id)) ||
                    (selectedCardId !== '' &&
                      (rel.sourceId === selectedCardId || rel.targetId === selectedCardId)),
                  'is-dimmed':
                    (highlightedRelSet !== null && !highlightedRelSet.has(rel.id)) ||
                    (selectedCardId !== '' &&
                      rel.sourceId !== selectedCardId &&
                      rel.targetId !== selectedCardId),
                }"
              >
                <path
                  class="arch-edge-path"
                  :d="rel.pathD"
                  fill="none"
                  :marker-end="
                    rel.conflict
                      ? 'url(#arch-arrow-red)'
                      : (highlightedRelSet && highlightedRelSet.has(rel.id)) ||
                          (selectedCardId !== '' &&
                            (rel.sourceId === selectedCardId || rel.targetId === selectedCardId))
                        ? 'url(#arch-arrow-accent)'
                        : 'url(#arch-arrow-slate)'
                  "
                />
                <rect
                  class="arch-edge-label-bg"
                  :x="rel.labelX - labelPillWidth(rel.label) / 2"
                  :y="rel.labelY - 10"
                  :width="labelPillWidth(rel.label)"
                  height="20"
                  rx="6"
                />
                <text
                  class="arch-edge-label-text"
                  :x="rel.labelX"
                  :y="rel.labelY + 3.5"
                  text-anchor="middle"
                >
                  {{ rel.label }}
                </text>
              </g>
            </svg>

            <button
              v-for="card in panorama.cards"
              :key="card.id"
              type="button"
              class="arch-card"
              :class="[
                `is-band-${card.band}`,
                card.conflictCount > 0 ? 'is-has-conflict' : '',
                card.impact !== 'none' ? `is-impact-${card.impact}` : '',
                selectedCardId === card.id ? 'is-selected' : '',
                (highlightedCardSet && highlightedCardSet.has(card.id)) ? 'is-highlighted' : '',
                (highlightedCardSet && !highlightedCardSet.has(card.id)) ? 'is-dimmed' : '',
              ]"
              :style="{
                left: `${card.x}px`,
                top: `${card.y}px`,
                width: `${card.w}px`,
                height: `${card.h}px`,
              }"
              @click="selectArchCard(card)"
            >
              <div class="arch-card-head">
                <span class="arch-card-icon" :class="`is-band-${card.band}`" aria-hidden="true">
                  <Icon :name="bandIconName(card.band)" :size="14" />
                </span>
                <strong class="arch-card-title">{{ card.title }}</strong>
                <span v-if="card.conflictCount > 0" class="arch-card-badge is-conflict">
                  {{ card.conflictCount }}
                </span>
                <span v-else-if="card.impact !== 'none'" class="arch-card-badge is-impact">
                  {{ impactBadgeText(card.impact) }}
                </span>
              </div>
              <span class="arch-card-sub">{{ card.subtitle }}</span>
            </button>
          </div>
        </div>

        <!-- 画布底部流式图例与缩放控制（置于滚动视口外，不遮挡卡片也不贴死边界） -->
        <div class="arch-canvas-footer">
          <div class="arch-float-legend" aria-label="架构层级图例">
            <span class="arch-legend-label">图例</span>
            <span class="arch-legend-pill is-frontend">前端 {{ panorama.bandCounts.frontend }}</span>
            <span class="arch-legend-pill is-backend">接口与后端 {{ panorama.bandCounts.backend }}</span>
            <span v-if="panorama.bandCounts.storage > 0" class="arch-legend-pill is-storage">
              存储 {{ panorama.bandCounts.storage }}
            </span>
            <span v-if="panorama.bandCounts.external > 0" class="arch-legend-pill is-external">
              外部系统 {{ panorama.bandCounts.external }}
            </span>
          </div>

          <div class="arch-float-controls" aria-label="缩放与适配控件">
            <button class="btn btn-sm btn-ghost" type="button" @click="changeArchZoom(-0.12)">
              -
            </button>
            <span class="mono">{{ Math.round(archZoom * 100) }}%</span>
            <button class="btn btn-sm btn-ghost" type="button" @click="changeArchZoom(0.12)">
              +
            </button>
            <button class="btn btn-sm btn-ghost" type="button" @click="fitArchToView">适配全图</button>
            <button class="btn btn-sm btn-ghost" type="button" @click="resetArchView">100%</button>
          </div>
        </div>

        <!-- 选中架构卡片抽屉：展示摘要与一键下钻到精确链路 -->
        <aside v-if="selectedArchCard" class="code-graph-inspector arch-inspector" aria-label="模块详情">
          <div class="section-head">
            <div>
              <strong>{{ selectedArchCard.title }}</strong>
              <span class="muted"> · {{ selectedArchCard.subtitle }}</span>
            </div>
            <div class="code-graph-inspector-actions">
              <button
                v-if="selectedArchCard.externalSource && canOpenSource"
                class="btn btn-sm btn-ghost"
                type="button"
                @click="emit('openSource', selectedArchCard.externalSource, 1)"
              >
                打开声明文件 ({{ selectedArchCard.externalSource }})
              </button>
              <button
                v-if="selectedArchCard.band !== 'external'"
                class="btn btn-sm btn-primary"
                type="button"
                @click="drillDownCard(selectedArchCard)"
              >
                下钻该模块精确链路 →
              </button>
            </div>
          </div>
          <p v-if="selectedArchCard.samplePaths.length" class="doc-note mono">
            核心接口：{{ selectedArchCard.samplePaths.join("  |  ") }}
          </p>
          <ul v-if="selectedArchCardConflicts.length" class="arch-inspector-conflicts">
            <li v-for="(c, idx) in selectedArchCardConflicts" :key="idx">
              <span class="badge" :class="c.severity === 'error' ? 'is-fail' : 'is-warn'">
                {{ c.severity === "error" ? "确定冲突" : "告警待核实" }}
              </span>
              <span>{{ c.detail }}</span>
            </li>
          </ul>
        </aside>

        <!-- 底部 3 张架构摘要卡片 -->
        <div class="arch-summary-grid" aria-label="架构摘要解读">
          <article
            v-for="col in panorama.summaryColumns"
            :key="col.title"
            class="arch-summary-card"
          >
            <h3 class="arch-summary-title">
              <i class="arch-summary-dot" :class="col.dotClass" aria-hidden="true" />
              <span>{{ col.title }}</span>
            </h3>
            <ul class="arch-summary-list">
              <li v-for="(bullet, bIdx) in col.bullets" :key="bIdx">{{ bullet }}</li>
            </ul>
          </article>
        </div>
      </template>

      <!-- 二 & 三、精确链路视图与列表明细视图 -->
      <template v-else>
        <div class="code-graph-toolbar">
          <button
            v-if="viewMode === 'graph'"
            class="filter-btn"
            type="button"
            @click="returnToArchitecture"
          >
            ← 返回架构全景
          </button>
          <input
            ref="searchEl"
            v-model="keyword"
            class="code-graph-search"
            type="search"
            placeholder="筛选路由、文件或表名（按 / 聚焦）…"
            aria-label="筛选代码图节点"
          />
          <select v-model="filterLayer" class="code-graph-select" aria-label="按层级筛选">
            <option value="all">全部 4 层</option>
            <option v-for="layer in LAYER_ORDER" :key="layer.key" :value="layer.key">
              {{ layer.label }}
            </option>
          </select>
          <select v-model="filterModule" class="code-graph-select" aria-label="按模块筛选">
            <option value="all">全部模块 ({{ modules.length }})</option>
            <option v-for="mod in modules" :key="mod" :value="mod">{{ mod }}</option>
          </select>
          <button
            class="filter-btn"
            :class="{ 'is-active': onlyImpact }"
            type="button"
            @click="onlyImpact = !onlyImpact"
          >
            仅看 Git 受影响 ({{ graph.impact.affectedNodeIds.length }})
          </button>
          <button
            class="filter-btn"
            :class="{ 'is-active': onlyConflict }"
            type="button"
            @click="onlyConflict = !onlyConflict"
          >
            仅看冲突 ({{ conflicts.length }})
          </button>
          <button
            v-if="selectedNode"
            class="filter-btn"
            :class="{ 'is-active': focusChainOnly }"
            type="button"
            @click="toggleFocusChain"
          >
            {{ focusChainOnly ? "退出聚焦" : "聚焦此链" }}
          </button>
          <div v-if="viewMode === 'graph'" class="code-graph-zoom">
            <button class="btn btn-sm btn-ghost" type="button" aria-label="缩小代码图" @click="changeZoom(-0.15)">
              -
            </button>
            <span class="mono">{{ Math.round(zoom * 100) }}%</span>
            <button class="btn btn-sm btn-ghost" type="button" aria-label="放大代码图" @click="changeZoom(0.15)">
              +
            </button>
            <button class="btn btn-sm btn-ghost" type="button" @click="fitToView">适配全图</button>
            <button class="btn btn-sm btn-ghost" type="button" @click="resetView">重置</button>
          </div>
        </div>

        <div class="code-graph-legend" aria-label="代码图状态图例">
          <span class="code-graph-legend-item">
            <i class="legend-swatch is-direct" aria-hidden="true" />
            直接改动 (Git)
          </span>
          <span class="code-graph-legend-item">
            <i class="legend-swatch is-transitive" aria-hidden="true" />
            传导受影响
          </span>
          <span class="code-graph-legend-item">
            <i class="legend-swatch is-conflict" aria-hidden="true" />
            跨层冲突
          </span>
          <span class="code-graph-legend-item">
            <i class="legend-swatch is-selected" aria-hidden="true" />
            全链路聚焦 · Esc 清除
          </span>
          <span v-if="exportNotice" class="code-graph-export-note">{{ exportNotice }}</span>
        </div>

        <p v-if="cappedNotice" class="notice is-old">
          <span>
            节点较多（匹配 {{ filteredNodes.length }} 个），已按 4 层均衡优先展示核心 {{ NODE_CAP }} 个避免卡顿。
          </span>
          <button class="link-btn" type="button" @click="showAllNodes = true">
            展开全部 {{ filteredNodes.length }} 个节点
          </button>
        </p>

        <div
          v-if="viewMode === 'graph'"
          ref="viewportEl"
          class="code-graph-viewport"
          :class="{ 'is-dragging': dragging, 'is-focus-mode': focusChainOnly }"
          @pointerdown="onPointerDown"
          @pointermove="onPointerMove"
          @pointerup="onPointerUp"
          @pointercancel="onPointerUp"
          @wheel="onViewportWheel"
        >
          <div
            class="code-graph-stage"
            :style="{
              width: `${layoutData.canvasWidth}px`,
              height: `${layoutData.canvasHeight}px`,
              transform: `translate(${panX}px, ${panY}px) scale(${zoom})`,
            }"
          >
            <div
              v-for="layer in LAYER_ORDER"
              :key="layer.key"
              class="code-graph-lane"
              :class="`is-layer-${layer.key}`"
              :style="{ left: `${COL_X[layer.key] - 12}px`, width: `${NODE_W + 24}px` }"
            >
              <div class="code-graph-lane-head">
                <span class="code-graph-lane-title">
                  <i class="code-graph-lane-dot" :class="`is-${layer.key}`" aria-hidden="true" />
                  <strong>{{ layer.label }}</strong>
                </span>
                <span class="code-graph-lane-count">{{ layoutData.byLayer[layer.key].length }}</span>
              </div>
            </div>

            <svg
              class="code-graph-svg"
              :width="layoutData.canvasWidth"
              :height="layoutData.canvasHeight"
              aria-hidden="true"
            >
              <defs>
                <marker
                  id="cg-arrow-default"
                  viewBox="0 0 10 10"
                  refX="8"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M 1 2 L 9 5 L 1 8 z" fill="currentColor" />
                </marker>
                <marker
                  id="cg-arrow-trace"
                  viewBox="0 0 10 10"
                  refX="8"
                  refY="5"
                  markerWidth="6.5"
                  markerHeight="6.5"
                  orient="auto-start-reverse"
                >
                  <path d="M 1 2 L 9 5 L 1 8 z" class="cg-marker-accent" />
                </marker>
                <marker
                  id="cg-arrow-conflict"
                  viewBox="0 0 10 10"
                  refX="8"
                  refY="5"
                  markerWidth="6.5"
                  markerHeight="6.5"
                  orient="auto-start-reverse"
                >
                  <path d="M 1 2 L 9 5 L 1 8 z" class="cg-marker-conflict" />
                </marker>
              </defs>
              <path
                v-for="item in visibleEdges"
                :key="item.edge.id"
                :d="item.d"
                class="code-graph-edge"
                :class="{
                  'is-conflict': item.edge.conflict,
                  'is-highlighted': item.highlighted,
                  'is-dimmed': item.dimmed,
                }"
                :marker-end="
                  item.edge.conflict
                    ? 'url(#cg-arrow-conflict)'
                    : item.highlighted
                      ? 'url(#cg-arrow-trace)'
                      : 'url(#cg-arrow-default)'
                "
              />
            </svg>

            <button
              v-for="node in visibleNodes"
              :key="node.id"
              type="button"
              class="code-graph-node"
              :class="[
                `is-layer-${node.layer}`,
                node.impact !== 'none' ? `is-impact-${node.impact}` : '',
                node.hasConflict ? 'is-conflict' : '',
                selectedNodeId === node.id ? 'is-selected' : '',
                relatedNodeIds.has(node.id) ? 'is-related' : '',
                selectedNodeId && !relatedNodeIds.has(node.id) ? 'is-dimmed' : '',
              ]"
              :style="{
                left: `${layoutData.positions.get(node.id)?.x ?? 0}px`,
                top: `${layoutData.positions.get(node.id)?.y ?? 0}px`,
                width: `${NODE_W}px`,
                height: `${NODE_H}px`,
              }"
              @click="selectNode(node)"
            >
              <div class="code-graph-node-top">
                <strong class="code-graph-node-label">{{ node.label }}</strong>
                <span v-if="node.hasConflict" class="code-graph-badge is-conflict">冲突</span>
                <span v-else-if="node.impact !== 'none'" class="code-graph-badge is-impact">
                  {{ impactBadgeText(node.impact) }}
                </span>
              </div>
              <span class="code-graph-node-meta">{{ node.file || node.module }}</span>
              <div class="code-graph-node-foot">
                <span class="code-graph-kind-tag">{{ node.kind }}</span>
                <span class="code-graph-degree mono">
                  &larr;{{ degreeStats.inCount.get(node.id) ?? 0 }} · {{ degreeStats.outCount.get(node.id) ?? 0 }}&rarr;
                </span>
              </div>
            </button>
          </div>
        </div>

        <div v-else class="code-graph-table">
          <table class="table">
            <thead>
              <tr>
                <th>层级</th>
                <th>节点</th>
                <th>源码位置</th>
                <th>模块 / 状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="node in visibleNodes" :key="node.id">
                <td data-label="层级">
                  <span class="badge">{{ LAYER_ORDER.find((l) => l.key === node.layer)?.label }}</span>
                </td>
                <td data-label="节点">
                  <strong>{{ node.label }}</strong>
                </td>
                <td data-label="源码位置" class="mono">
                  <button
                    v-if="node.file && canOpenSource"
                    class="link-btn mono"
                    type="button"
                    @click="emit('openSource', node.file, Math.max(1, node.line))"
                  >
                    {{ node.file }}:{{ Math.max(1, node.line) }}
                  </button>
                  <span v-else>{{ node.file || "-" }}</span>
                </td>
                <td data-label="模块 / 状态">
                  <span>{{ node.module }}</span>
                  <span v-if="node.hasConflict" class="badge is-fail">冲突</span>
                  <span v-else-if="node.impact !== 'none'" class="badge is-warn">
                    {{ impactBadgeText(node.impact) }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <aside v-if="selectedNode" class="code-graph-inspector" aria-label="节点详情">
          <div class="section-head">
            <div>
              <strong>{{ selectedNode.label }}</strong>
              <span class="muted">
                · {{ selectedNode.kind }} · 模块 {{ selectedNode.module }}
                · 上游 {{ traceResult.upstreamCount }} · 下游 {{ traceResult.downstreamCount }}
              </span>
            </div>
            <div class="code-graph-inspector-actions">
              <button
                v-if="selectedNode.file && canOpenSource"
                class="btn btn-sm btn-ghost"
                type="button"
                @click="emit('openSource', selectedNode.file, Math.max(1, selectedNode.line))"
              >
                跳到源码 ({{ selectedNode.file }}:{{ Math.max(1, selectedNode.line) }})
              </button>
              <button class="btn btn-sm btn-primary" type="button" @click="testSelectedNode">
                仅测此节点范围
              </button>
              <button
                class="btn btn-sm"
                :class="{ 'is-active': focusChainOnly }"
                type="button"
                @click="toggleFocusChain"
              >
                {{ focusChainOnly ? "退出聚焦" : "聚焦此链" }}
              </button>
            </div>
          </div>
          <div v-if="tracedPathLayers.length" class="code-graph-trace-chain" aria-label="全链路路径追踪">
            <div
              v-for="group in tracedPathLayers"
              :key="group.key"
              class="trace-layer-pill"
              :class="`is-layer-${group.key}`"
            >
              <span class="trace-layer-tag">{{ group.label }}</span>
              <button
                v-for="item in group.nodes"
                :key="item.id"
                class="link-btn mono trace-node-link"
                :class="{ 'is-current': item.id === selectedNodeId }"
                type="button"
                @click="locateNodeById(item.id)"
              >
                {{ item.label }}
              </button>
            </div>
          </div>
          <p v-else-if="selectedNeighborNodes.length" class="doc-note">
            关联上下游节点：
            <button
              v-for="nbr in selectedNeighborNodes.slice(0, 8)"
              :key="nbr.id"
              class="link-btn"
              type="button"
              @click="selectedNodeId = nbr.id"
            >
              {{ nbr.label }}
            </button>
          </p>
        </aside>
      </template>

      <details v-if="conflicts.length" class="project-map-details" open>
        <summary>
          <span>跨层契约与静态资源问题 {{ conflicts.length }} 项</span>
        </summary>
        <ul class="code-graph-conflicts">
          <li v-for="(item, idx) in conflicts" :key="`${item.kind}-${idx}`">
            <span class="badge" :class="item.severity === 'error' ? 'is-fail' : 'is-warn'">
              {{ item.severity === "error" ? "确定冲突" : "告警待核实" }}
            </span>
            <span>{{ item.detail }}</span>
            <button
              v-if="item.file && canOpenSource"
              class="link-btn mono"
              type="button"
              @click="emit('openSource', item.file, 1)"
            >
              打开 {{ item.file }}
            </button>
            <button
              v-if="item.nodeIds.length > 0"
              class="link-btn"
              type="button"
              @click="locateNodeById(item.nodeIds[0] ?? '')"
            >
              定位节点
            </button>
          </li>
        </ul>
      </details>
    </template>
  </section>
</template>
