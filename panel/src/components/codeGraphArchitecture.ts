import type {
  CodeGraph,
  CodeGraphConflict,
  CodeGraphEdge,
  CodeGraphNode,
} from "@/domain/types";

export type ArchBand = "frontend" | "backend" | "storage" | "external";

export interface ArchCard {
  id: string;
  band: ArchBand;
  title: string;
  subtitle: string;
  moduleKey: string;
  isAggregateOthers?: boolean;
  fileCount: number;
  apiCount: number;
  backendCount: number;
  storageCount: number;
  conflictCount: number;
  errorCount: number;
  warningCount: number;
  impact: "none" | "direct" | "transitive";
  nodeIds: string[];
  samplePaths: string[];
  externalSource?: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ArchRelation {
  id: string;
  sourceId: string;
  targetId: string;
  kind: "api" | "data" | "external" | "conflict";
  label: string;
  count: number;
  conflict: boolean;
  pathD: string;
  labelX: number;
  labelY: number;
}

export interface ArchStory {
  id: "overview" | "main_flow" | "risk_hotspots" | "git_impact";
  stepNo: string;
  title: string;
  subtitle: string;
  addedCount: number;
  highlightedCardIds: string[];
  highlightedRelIds: string[];
}

export interface ArchSummaryColumn {
  dotClass: "is-blue" | "is-green" | "is-amber";
  title: string;
  bullets: string[];
}

export interface ArchPanoramaModel {
  cards: ArchCard[];
  relations: ArchRelation[];
  stories: ArchStory[];
  summaryColumns: ArchSummaryColumn[];
  bandCounts: Record<ArchBand, number>;
  canvasWidth: number;
  canvasHeight: number;
}

const CARD_W = 224;
const CARD_H = 84;
const COL_GAP = 44;
const ROW_GAP = 100;
const PAD_X = 52;
const PAD_TOP = 40;
const PAD_BOTTOM = 78;
const MAX_CARDS_PER_BAND = 5;

function inferArchBand(node: CodeGraphNode): ArchBand {
  if (node.layer === "frontend") return "frontend";
  if (node.layer === "storage") return "storage";
  return "backend";
}

function normalizeDomainModule(rawModule: string, band: ArchBand): string {
  const cleaned = rawModule.trim();
  if (!cleaned) {
    if (band === "frontend") return "前端交互层";
    if (band === "storage") return "数据存储层";
    if (band === "external") return "外部系统";
    return "后端核心服务";
  }
  const lower = cleaned.toLowerCase();
  if (lower === "api" || lower === "routes" || lower === "routers") {
    return "API 接口聚合";
  }
  if (lower === "src" || lower === "app") {
    return band === "frontend" ? "前端应用主视图" : "核心应用服务";
  }
  return cleaned;
}

function buildRoundedOrthoPath(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  midY: number,
): { pathD: string; labelX: number; labelY: number } {
  const dx = x2 - x1;
  const isBridgeU = Math.abs(y1 - y2) < 4;
  if (!isBridgeU && Math.abs(dx) < 3) {
    return {
      pathD: `M ${x1} ${y1} L ${x2} ${y2}`,
      labelX: x1,
      labelY: Math.round((y1 + y2) / 2),
    };
  }
  const dirX = dx >= 0 ? 1 : -1;
  const dirY1 = midY >= y1 ? 1 : -1;
  const dirY2 = y2 >= midY ? 1 : -1;
  const maxR = Math.min(
    10,
    Math.floor(Math.abs(dx) / 2),
    Math.max(2, Math.floor(Math.abs(midY - y1) / 2)),
    Math.max(2, Math.floor(Math.abs(y2 - midY) / 2)),
  );
  const r = Math.max(3, maxR);
  const seg1Y = midY - dirY1 * r;
  const seg2X1 = x1 + dirX * r;
  const seg2X2 = x2 - dirX * r;
  const seg3Y = midY + dirY2 * r;
  const pathD = [
    `M ${x1} ${y1}`,
    `L ${x1} ${seg1Y}`,
    `Q ${x1} ${midY} ${seg2X1} ${midY}`,
    `L ${seg2X2} ${midY}`,
    `Q ${x2} ${midY} ${x2} ${seg3Y}`,
    `L ${x2} ${y2}`,
  ].join(" ");
  return {
    pathD,
    labelX: Math.round((x1 + x2) / 2),
    labelY: Math.round(midY),
  };
}

/** 构造跨越多行卡片时的绕行正交折线（先在空余一侧绕过中间行卡片，再垂直穿过）。 */
function buildDetourOrthoPath(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  detourX: number,
  topCorridorY: number,
  botCorridorY: number,
): { pathD: string; labelX: number; labelY: number } {
  const r = 8;
  const dirX1 = detourX >= x1 ? 1 : -1;
  const dirX2 = x2 >= detourX ? 1 : -1;
  const parts = [`M ${x1} ${y1}`];
  if (Math.abs(detourX - x1) >= r * 2) {
    parts.push(
      `L ${x1} ${topCorridorY - r}`,
      `Q ${x1} ${topCorridorY} ${x1 + dirX1 * r} ${topCorridorY}`,
      `L ${detourX - dirX1 * r} ${topCorridorY}`,
      `Q ${detourX} ${topCorridorY} ${detourX} ${topCorridorY + r}`,
    );
  }
  if (Math.abs(x2 - detourX) >= r * 2) {
    parts.push(
      `L ${detourX} ${botCorridorY - r}`,
      `Q ${detourX} ${botCorridorY} ${detourX + dirX2 * r} ${botCorridorY}`,
      `L ${x2 - dirX2 * r} ${botCorridorY}`,
      `Q ${x2} ${botCorridorY} ${x2} ${botCorridorY + r}`,
    );
  }
  parts.push(`L ${x2} ${y2}`);
  return {
    pathD: parts.join(" "),
    labelX: Math.round((detourX + x2) / 2),
    labelY: Math.round(botCorridorY),
  };
}

interface RawBucket {
  band: ArchBand;
  key: string;
  title: string;
  moduleKey: string;
  nodes: CodeGraphNode[];
  conflicts: CodeGraphConflict[];
}

function buildCardSubtitle(bucket: RawBucket): string {
  if (bucket.band === "frontend") {
    const apiCallCount = bucket.nodes.filter((n) => n.kind !== "html").length;
    return `${bucket.nodes.length} 文件 · ${apiCallCount} 组件/请求`;
  }
  if (bucket.band === "storage") {
    const sample = bucket.nodes
      .slice(0, 2)
      .map((n) => n.label)
      .join(" · ");
    return bucket.nodes.length <= 2 ? sample || "数据表" : `${bucket.nodes.length} 实体 · ${sample}`;
  }
  const apis = bucket.nodes.filter((n) => n.layer === "api").length;
  const bes = bucket.nodes.filter((n) => n.layer === "backend").length;
  const parts: string[] = [];
  if (apis > 0) parts.push(`${apis} 接口`);
  if (bes > 0) parts.push(`${bes} 模块`);
  const firstRoute = bucket.nodes.find((n) => n.layer === "api")?.meta?.path;
  if (typeof firstRoute === "string" && firstRoute) {
    parts.push(firstRoute.slice(0, 22));
  }
  return parts.join(" · ") || `${bucket.nodes.length} 单元`;
}

function mergeOverflowBuckets(buckets: RawBucket[], band: ArchBand): RawBucket[] {
  if (buckets.length <= MAX_CARDS_PER_BAND) return buckets;
  const sorted = [...buckets].sort((a, b) => {
    const scoreA = a.conflicts.length * 100 + a.nodes.length;
    const scoreB = b.conflicts.length * 100 + b.nodes.length;
    return scoreB - scoreA || a.title.localeCompare(b.title);
  });
  const kept = sorted.slice(0, MAX_CARDS_PER_BAND - 1);
  const rest = sorted.slice(MAX_CARDS_PER_BAND - 1);
  const mergedNodes = rest.flatMap((r) => r.nodes);
  const mergedConflicts = rest.flatMap((r) => r.conflicts);
  kept.push({
    band,
    key: `${band}:__others__`,
    title: `其他${band === "frontend" ? "前端" : band === "storage" ? "存储" : "业务"} (+${rest.length})`,
    moduleKey: "__all__",
    nodes: mergedNodes,
    conflicts: mergedConflicts,
  });
  return kept;
}

export function buildArchitecturePanorama(
  graph: CodeGraph,
  projectName = "",
): ArchPanoramaModel {
  const bucketMap = new Map<string, RawBucket>();
  const nodeToBucketKey = new Map<string, string>();
  const conflictsByNode = new Map<string, CodeGraphConflict[]>();

  for (const conflict of graph.conflicts) {
    for (const nid of conflict.nodeIds) {
      const list = conflictsByNode.get(nid);
      if (list) list.push(conflict);
      else conflictsByNode.set(nid, [conflict]);
    }
  }

  // 把同一业务域的 api + backend 尽量按模块聚合成一张业务服务卡，避免碎片化
  for (const node of graph.nodes) {
    const band = inferArchBand(node);
    const domain = normalizeDomainModule(node.module, band);
    const bKey = `${band}:${domain}`;
    nodeToBucketKey.set(node.id, bKey);
    let bucket = bucketMap.get(bKey);
    if (!bucket) {
      bucket = {
        band,
        key: bKey,
        title: domain,
        moduleKey: node.module,
        nodes: [],
        conflicts: [],
      };
      bucketMap.set(bKey, bucket);
    }
    bucket.nodes.push(node);
    for (const c of conflictsByNode.get(node.id) ?? []) {
      if (!bucket.conflicts.includes(c)) {
        bucket.conflicts.push(c);
      }
    }
  }

  const feBuckets = mergeOverflowBuckets(
    [...bucketMap.values()].filter((b) => b.band === "frontend"),
    "frontend",
  );
  const beBuckets = mergeOverflowBuckets(
    [...bucketMap.values()].filter((b) => b.band === "backend"),
    "backend",
  );
  const stBuckets = mergeOverflowBuckets(
    [...bucketMap.values()].filter((b) => b.band === "storage"),
    "storage",
  );

  // 更新溢出节点到聚合卡 ID
  for (const bucket of [...feBuckets, ...beBuckets, ...stBuckets]) {
    for (const n of bucket.nodes) {
      nodeToBucketKey.set(n.id, bucket.key);
    }
  }

  const cardByKey = new Map<string, ArchCard>();
  const externalCards: ArchCard[] = [];
  const rawExts = (graph.externalSystems ?? []).slice(0, MAX_CARDS_PER_BAND);
  for (const ext of rawExts) {
    externalCards.push({
      id: `arch:${ext.id}`,
      band: "external",
      title: ext.name,
      subtitle: `${ext.detail} · ${ext.source}`.slice(0, 32),
      moduleKey: "__external__",
      fileCount: 0,
      apiCount: 0,
      backendCount: 0,
      storageCount: 0,
      conflictCount: 0,
      errorCount: 0,
      warningCount: 0,
      impact: "none",
      nodeIds: [],
      samplePaths: [],
      externalSource: ext.source,
      x: 0,
      y: 0,
      w: CARD_W,
      h: CARD_H,
    });
  }

  const allDomainCards: ArchCard[] = [];
  for (const bucket of [...feBuckets, ...beBuckets, ...stBuckets]) {
    const fileSet = new Set(bucket.nodes.map((n) => n.file).filter(Boolean));
    const hasDirect = bucket.nodes.some((n) => n.impact === "direct");
    const hasTransitive = bucket.nodes.some((n) => n.impact === "transitive");
    const errorCount = bucket.conflicts.filter((c) => c.severity === "error").length;
    const warningCount = bucket.conflicts.length - errorCount;
    const card: ArchCard = {
      id: `arch:${bucket.key}`,
      band: bucket.band,
      title: bucket.title,
      subtitle: buildCardSubtitle(bucket),
      moduleKey: bucket.moduleKey,
      isAggregateOthers: bucket.key.endsWith("__others__"),
      fileCount: fileSet.size,
      apiCount: bucket.nodes.filter((n) => n.layer === "api").length,
      backendCount: bucket.nodes.filter((n) => n.layer === "backend").length,
      storageCount: bucket.nodes.filter((n) => n.layer === "storage").length,
      conflictCount: bucket.conflicts.length,
      errorCount,
      warningCount,
      impact: hasDirect ? "direct" : hasTransitive ? "transitive" : "none",
      nodeIds: bucket.nodes.map((n) => n.id),
      samplePaths: bucket.nodes
        .filter((n) => n.layer === "api")
        .slice(0, 6)
        .map((n) => n.label),
      x: 0,
      y: 0,
      w: CARD_W,
      h: CARD_H,
    };
    cardByKey.set(bucket.key, card);
    allDomainCards.push(card);
  }

  // 对跨分层出现同名的模块标题加上中文层级前缀，避免卡片视图出现重复命名（如重复的 backend）
  const titleCounts = new Map<string, number>();
  for (const card of allDomainCards) {
    titleCounts.set(card.title, (titleCounts.get(card.title) ?? 0) + 1);
  }
  const BAND_PREFIX: Record<ArchBand, string> = {
    frontend: "前端",
    backend: "后端",
    storage: "存储",
    external: "外部",
  };
  for (const card of allDomainCards) {
    if ((titleCounts.get(card.title) ?? 0) > 1 && !card.isAggregateOthers) {
      card.title = `${BAND_PREFIX[card.band]} · ${card.title}`;
    }
  }

  // 分层自适应纵向排布（跳过空层，避免白占画布高度）
  const bandsInOrder: Array<{ band: ArchBand; list: ArchCard[] }> = [
    { band: "frontend" as ArchBand, list: allDomainCards.filter((c) => c.band === "frontend") },
    { band: "backend" as ArchBand, list: allDomainCards.filter((c) => c.band === "backend") },
    { band: "storage" as ArchBand, list: allDomainCards.filter((c) => c.band === "storage") },
    { band: "external" as ArchBand, list: externalCards },
  ].filter((row) => row.list.length > 0);

  const maxRowCards = Math.max(1, ...bandsInOrder.map((r) => r.list.length));
  const canvasWidth = Math.max(820, PAD_X * 2 + maxRowCards * CARD_W + (maxRowCards - 1) * COL_GAP);

  let curY = PAD_TOP;
  const bandRowY = new Map<ArchBand, number>();
  for (const row of bandsInOrder) {
    bandRowY.set(row.band, curY);
    const rowW = row.list.length * CARD_W + (row.list.length - 1) * COL_GAP;
    const startX = Math.round((canvasWidth - rowW) / 2);
    row.list.forEach((card, idx) => {
      card.x = startX + idx * (CARD_W + COL_GAP);
      card.y = curY;
    });
    curY += CARD_H + ROW_GAP;
  }
  const canvasHeight = Math.max(300, curY - ROW_GAP + PAD_BOTTOM);
  const allCards = [...allDomainCards, ...externalCards];
  const cardById = new Map(allCards.map((c) => [c.id, c]));

  // 聚合跨卡连线：相同 (sourceCardId, targetCardId) 合并成 1 条带计数和状态的正交线
  interface RelAcc {
    sourceId: string;
    targetId: string;
    count: number;
    hasConflict: boolean;
    kinds: Set<CodeGraphEdge["kind"]>;
  }
  const relMap = new Map<string, RelAcc>();
  for (const edge of graph.edges) {
    const srcBucket = nodeToBucketKey.get(edge.source);
    const tgtBucket = nodeToBucketKey.get(edge.target);
    if (!srcBucket || !tgtBucket || srcBucket === tgtBucket) continue;
    const srcId = `arch:${srcBucket}`;
    const tgtId = `arch:${tgtBucket}`;
    const rKey = `${srcId}->${tgtId}`;
    let acc = relMap.get(rKey);
    if (!acc) {
      acc = { sourceId: srcId, targetId: tgtId, count: 0, hasConflict: false, kinds: new Set() };
      relMap.set(rKey, acc);
    }
    acc.count += 1;
    acc.kinds.add(edge.kind);
    if (edge.conflict) acc.hasConflict = true;
  }

  // 若存储卡与外部数据库依赖同时存在，从后端卡或存储卡建立优雅的数据持久化连线到外部系统层
  if (externalCards.length > 0) {
    const storageCard = allDomainCards.find((c) => c.band === "storage");
    const backendAnchor = allDomainCards.find((c) => c.band === "backend");
    for (const ext of externalCards) {
      const extKind = ext.id.replace("arch:ext:", "");
      const anchor =
        (["postgres", "mysql", "sqlite", "mongodb", "redis"].includes(extKind) && storageCard) ||
        backendAnchor;
      if (anchor) {
        const rKey = `${anchor.id}->${ext.id}`;
        if (!relMap.has(rKey)) {
          relMap.set(rKey, {
            sourceId: anchor.id,
            targetId: ext.id,
            count: 1,
            hasConflict: false,
            kinds: new Set(["external"]),
          });
        }
      }
    }
  }

  // 为每条聚合边分配正交起止锚点与错峰走廊 midY，避免标签重叠
  const sortedAcc = [...relMap.values()].sort(
    (a, b) => Number(b.hasConflict) - Number(a.hasConflict) || b.count - a.count,
  );
  // 走廊分组键："{sourceRowY}->{targetRowY}"
  const corridorSlots = new Map<string, number>();
  const relations: ArchRelation[] = [];

  for (const acc of sortedAcc.slice(0, 24)) {
    const src = cardById.get(acc.sourceId);
    const tgt = cardById.get(acc.targetId);
    if (!src || !tgt) continue;
    const sameRow = Math.abs(src.y - tgt.y) < 4;
    const downward = src.y <= tgt.y;
    const x1 = Math.round(src.x + src.w / 2);
    const y1 = sameRow ? src.y + src.h : downward ? src.y + src.h : src.y;
    const x2 = Math.round(tgt.x + tgt.w / 2);
    const y2 = sameRow ? tgt.y + tgt.h : downward ? tgt.y : tgt.y + tgt.h;

    const corridorKey = sameRow
      ? `row:${src.y}`
      : `${Math.min(src.y, tgt.y)}->${Math.max(src.y, tgt.y)}`;
    const slot = corridorSlots.get(corridorKey) ?? 0;
    corridorSlots.set(corridorKey, slot + 1);

    let geom: { pathD: string; labelX: number; labelY: number };
    if (sameRow) {
      // 同层模块化调用：走卡片下方净空桥廊，彻底避免标签压到窄缝中
      const bridgeY = src.y + src.h + 22 + (slot % 3) * 18;
      geom = buildRoundedOrthoPath(x1, y1, x2, y2, bridgeY);
    } else if (downward && tgt.y - src.y > CARD_H + ROW_GAP + 10) {
      // 跨过中间层（例如 后端 -> 外部系统 越过 存储层）：动态避让中间卡片投影
      const middleCards = allCards.filter(
        (c) => c.id !== src.id && c.id !== tgt.id && c.y > src.y && c.y < tgt.y,
      );
      const hitsMiddle = (px: number): ArchCard | undefined =>
        middleCards.find((mc) => px >= mc.x - 14 && px <= mc.x + mc.w + 14);
      const topCorridor = src.y + src.h + 24 + (slot % 3) * 12;
      const botCorridor = tgt.y - Math.round(ROW_GAP * 0.44) + ((slot % 3) - 1) * 14;
      const srcObst = hitsMiddle(x1);
      const tgtObst = hitsMiddle(x2);

      if (!tgtObst) {
        geom = buildRoundedOrthoPath(x1, y1, x2, y2, topCorridor);
      } else if (!srcObst) {
        geom = buildRoundedOrthoPath(x1, y1, x2, y2, botCorridor);
      } else {
        const minLeft = Math.min(...middleCards.map((c) => c.x));
        const maxRight = Math.max(...middleCards.map((c) => c.x + c.w));
        const detourLeft = Math.max(24, minLeft - 24 - (slot % 2) * 14);
        const detourRight = Math.min(canvasWidth - 24, maxRight + 24 + (slot % 2) * 14);
        const detourX =
          Math.abs(x2 - detourLeft) <= Math.abs(detourRight - x2) ? detourLeft : detourRight;
        geom = buildDetourOrthoPath(x1, y1, x2, y2, detourX, topCorridor, botCorridor);
      }
    } else {
      const midBase = (y1 + y2) / 2;
      const offset = ((slot % 3) - 1) * 18;
      const midY = Math.round(midBase + offset);
      geom = buildRoundedOrthoPath(x1, y1, x2, y2, midY);
    }
    const { pathD, labelX, labelY } = geom;

    let relKind: ArchRelation["kind"] = "api";
    let labelPrefix = "HTTP API";
    if (acc.hasConflict || (src.conflictCount > 0 && tgt.conflictCount > 0)) {
      relKind = "conflict";
      labelPrefix = "契约风险";
    } else if (tgt.band === "external") {
      relKind = "external";
      labelPrefix = "外部依赖";
    } else if (acc.kinds.has("reads_writes") || tgt.band === "storage") {
      relKind = "data";
      labelPrefix = "数据读写";
    } else if (acc.kinds.has("imports")) {
      relKind = "data";
      labelPrefix = "模块调用";
    }

    const label = acc.kinds.has("external") ? labelPrefix : `${labelPrefix} · ${acc.count}`;
    relations.push({
      id: `rel:${acc.sourceId}->${acc.targetId}`,
      sourceId: acc.sourceId,
      targetId: acc.targetId,
      kind: relKind,
      label,
      count: acc.count,
      conflict: relKind === "conflict",
      pathD,
      labelX,
      labelY,
    });
  }

  // 构建 3 条基于项目真实数据的引导视图故事
  const mainFlowCardIds = allDomainCards
    .filter((c) => c.band !== "frontend" || c.apiCount > 0 || relations.some((r) => r.sourceId === c.id))
    .map((c) => c.id);
  const mainFlowRelIds = relations.filter((r) => r.kind !== "external").map((r) => r.id);

  const riskCardIds = allDomainCards.filter((c) => c.conflictCount > 0).map((c) => c.id);
  const riskRelIds = relations
    .filter((r) => riskCardIds.includes(r.sourceId) || riskCardIds.includes(r.targetId))
    .map((r) => r.id);

  const impactCardIds = allDomainCards.filter((c) => c.impact !== "none").map((c) => c.id);
  const impactRelIds = relations
    .filter((r) => impactCardIds.includes(r.sourceId) && impactCardIds.includes(r.targetId))
    .map((r) => r.id);

  const stories: ArchStory[] = [
    {
      id: "main_flow",
      stepNo: "01",
      title: "核心调用主链路",
      subtitle: `${graph.stats.frontend} 前端 · ${graph.stats.api} 接口 · ${graph.stats.backend} 业务 · ${graph.stats.storage} 存储`,
      addedCount: mainFlowCardIds.length,
      highlightedCardIds: mainFlowCardIds,
      highlightedRelIds: mainFlowRelIds,
    },
    {
      id: "risk_hotspots",
      stepNo: "02",
      title: "跨层契约风险点",
      subtitle:
        graph.conflicts.length > 0
          ? `${graph.conflicts.filter((c) => c.severity === "error").length} 阻断 · ${graph.conflicts.filter((c) => c.severity !== "error").length} 告警待核实`
          : "全链路 0 契约冲突",
      addedCount: riskCardIds.length,
      highlightedCardIds: riskCardIds,
      highlightedRelIds: riskRelIds,
    },
    {
      id: "git_impact",
      stepNo: "03",
      title: "Git 变更波及面",
      subtitle:
        graph.impact.changedFiles.length > 0
          ? `${graph.impact.changedFiles.length} 文件变更 · ${graph.impact.affectedModules.length} 模块受影响`
          : "当前工作区无未提交代码改动",
      addedCount: impactCardIds.length,
      highlightedCardIds: impactCardIds,
      highlightedRelIds: impactRelIds,
    },
  ];

  // 底部 3 张架构摘要卡片
  const extListText =
    externalCards.length > 0
      ? externalCards.map((e) => e.title).slice(0, 4).join("、")
      : "未检测到外部中间件依赖";
  const errCount = graph.conflicts.filter((c) => c.severity === "error").length;
  const warnCount = graph.conflicts.length - errCount;
  const summaryColumns: ArchSummaryColumn[] = [
    {
      dotClass: "is-blue",
      title: "交付形态与规模",
      bullets: [
        `${projectName || "当前项目"} 索引 ${graph.stats.totalFiles} 个源码文件`,
        `前端 ${graph.stats.frontend} · API ${graph.stats.api} · 后端 ${graph.stats.backend} · 存储 ${graph.stats.storage}`,
        `外部依赖与运行设施：${extListText}`,
      ],
    },
    {
      dotClass: "is-green",
      title: "依赖流向与契约",
      bullets: [
        "前端入口 → API 接口契约 → 后端业务处理 → 数据存储实体",
        `已建立 ${graph.stats.edges} 条跨层调用与依赖边，聚合 ${relations.length} 条主通路`,
        "点击任意模块卡可下钻到精确文件与源码定位视图",
      ],
    },
    {
      dotClass: "is-amber",
      title: "风险与影响范围",
      bullets: [
        graph.conflicts.length > 0
          ? `检测到 ${errCount} 条确定冲突，${warnCount} 条告警待运行态确认`
          : "静态契约与资源核对无异常冲突",
        graph.impact.changedFiles.length > 0
          ? `Git 变更涉及 ${graph.impact.changedFiles.length} 个文件、${graph.impact.affectedRoutes.length} 条接口`
          : "当前 Git 工作区处于洁净基线状态",
        "静态图仅作定位与影响推理，真实通过结论以运行证据为准",
      ],
    },
  ];

  const bandCounts: Record<ArchBand, number> = {
    frontend: allDomainCards.filter((c) => c.band === "frontend").length,
    backend: allDomainCards.filter((c) => c.band === "backend").length,
    storage: allDomainCards.filter((c) => c.band === "storage").length,
    external: externalCards.length,
  };

  return {
    cards: allCards,
    relations,
    stories,
    summaryColumns,
    bandCounts,
    canvasWidth,
    canvasHeight,
  };
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function bandSvgColors(band: ArchBand, hasConflict: boolean): {
  fill: string;
  stroke: string;
  titleColor: string;
} {
  if (hasConflict) {
    return { fill: "#FFF5F5", stroke: "#EF4444", titleColor: "#991B1B" };
  }
  switch (band) {
    case "frontend":
      return { fill: "#ECFEFF", stroke: "#06B6D4", titleColor: "#155E75" };
    case "backend":
      return { fill: "#ECFDF5", stroke: "#10B981", titleColor: "#065F46" };
    case "storage":
      return { fill: "#F5F3FF", stroke: "#8B5CF6", titleColor: "#5B21B6" };
    case "external":
      return { fill: "#F1F5F9", stroke: "#94A3B8", titleColor: "#334155" };
  }
}

export function buildArchitecturePanoramaSvg(
  model: ArchPanoramaModel,
  projectName = "",
): string {
  const relLines = model.relations
    .map((rel) => {
      const stroke = rel.conflict ? "#EF4444" : rel.kind === "external" ? "#94A3B8" : "#64748B";
      const marker = rel.conflict ? "url(#arch-arrow-red)" : "url(#arch-arrow-slate)";
      const labelW = Math.max(54, rel.label.length * 7.5 + 14);
      return [
        `<path d="${escapeXml(rel.pathD)}" fill="none" stroke="${stroke}" stroke-width="1.6" marker-end="${marker}"/>`,
        `<rect x="${rel.labelX - labelW / 2}" y="${rel.labelY - 10}" width="${labelW}" height="20" rx="6" fill="#FFFFFF" stroke="#E2E8F0"/>`,
        `<text x="${rel.labelX}" y="${rel.labelY + 4}" text-anchor="middle" font-size="10.5" fill="${stroke}">${escapeXml(rel.label)}</text>`,
      ].join("");
    })
    .join("\n  ");

  const cardBoxes = model.cards
    .map((card) => {
      const c = bandSvgColors(card.band, card.conflictCount > 0);
      return [
        `<g>`,
        `<rect x="${card.x}" y="${card.y}" width="${card.w}" height="${card.h}" rx="14" fill="${c.fill}" stroke="${c.stroke}" stroke-width="1.6"/>`,
        `<text x="${card.x + 14}" y="${card.y + 32}" font-size="13" font-weight="600" fill="${c.titleColor}">${escapeXml(card.title.slice(0, 22))}</text>`,
        `<text x="${card.x + 14}" y="${card.y + 56}" font-size="11" fill="#475569">${escapeXml(card.subtitle.slice(0, 28))}</text>`,
        `</g>`,
      ].join("");
    })
    .join("\n  ");

  const titleLine = projectName ? `${projectName} 系统架构全景图` : "全栈系统架构全景图";
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${model.canvasWidth}" height="${model.canvasHeight}" viewBox="0 0 ${model.canvasWidth} ${model.canvasHeight}" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif">`,
    `<rect width="100%" height="100%" fill="#F6F8FC"/>`,
    `<text x="28" y="24" font-size="14" font-weight="600" fill="#0F172A">${escapeXml(titleLine)}</text>`,
    `<defs>`,
    `<marker id="arch-arrow-slate" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 1 2 L 9 5 L 1 8 z" fill="#64748B"/></marker>`,
    `<marker id="arch-arrow-red" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 1 2 L 9 5 L 1 8 z" fill="#EF4444"/></marker>`,
    `</defs>`,
    `  ${relLines}`,
    `  ${cardBoxes}`,
    `</svg>`,
  ].join("\n");
}
