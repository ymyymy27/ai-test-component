import type { CodeGraphEdge, CodeGraphLayer, CodeGraphNode } from "@/domain/types";

export const LAYER_ORDER: ReadonlyArray<{
  key: CodeGraphLayer;
  label: string;
  shortLabel: string;
  hint: string;
  accentHex: string;
}> = [
  {
    key: "frontend",
    label: "前端页面 / 调用",
    shortLabel: "前端",
    hint: "HTML · Vue · TSX · Fetch",
    accentHex: "#22D3EE",
  },
  {
    key: "api",
    label: "API 接口契约",
    shortLabel: "接口",
    hint: "HTTP Route · WS · OpenAPI",
    accentHex: "#60A5FA",
  },
  {
    key: "backend",
    label: "后端业务模块",
    shortLabel: "后端",
    hint: "Handler · Service",
    accentHex: "#34D399",
  },
  {
    key: "storage",
    label: "数据存储模型",
    shortLabel: "存储",
    hint: "ORM · Table · SQL",
    accentHex: "#A78BFA",
  },
];

export const NODE_CAP = 80;
export const COL_X: Record<CodeGraphLayer, number> = {
  frontend: 28,
  api: 280,
  backend: 532,
  storage: 784,
};
export const NODE_W = 196;
export const NODE_H = 74;
export const ROW_GAP = 16;

export interface PositionedNode {
  node: CodeGraphNode;
  x: number;
  y: number;
}

export interface TraceResult {
  nodeIds: Set<string>;
  edgeIds: Set<string>;
  upstreamCount: number;
  downstreamCount: number;
}

export function computeNodeDegrees(edges: readonly CodeGraphEdge[]): {
  inCount: Map<string, number>;
  outCount: Map<string, number>;
  total: Map<string, number>;
} {
  const inCount = new Map<string, number>();
  const outCount = new Map<string, number>();
  const total = new Map<string, number>();
  for (const edge of edges) {
    outCount.set(edge.source, (outCount.get(edge.source) ?? 0) + 1);
    inCount.set(edge.target, (inCount.get(edge.target) ?? 0) + 1);
    total.set(edge.source, (total.get(edge.source) ?? 0) + 1);
    total.set(edge.target, (total.get(edge.target) ?? 0) + 1);
  }
  return { inCount, outCount, total };
}

function isBoilerplateNode(node: CodeGraphNode): boolean {
  const fileLower = node.file.toLowerCase();
  return fileLower.endsWith("/__init__.py") || fileLower === "__init__.py" || Boolean(node.meta?.boilerplate);
}

export function nodePriorityScore(node: CodeGraphNode, degreeMap: Map<string, number>): number {
  let score = 0;
  if (node.impact === "direct") score += 4000;
  else if (node.impact === "transitive") score += 2000;
  if (node.hasConflict) score += 3000;
  score += Math.min(800, (degreeMap.get(node.id) ?? 0) * 15);
  if (isBoilerplateNode(node)) score -= 500;
  return score;
}

/**
 * 分层均衡优先级抽样：当大项目节点超过 NODE_CAP 时，确保 4 层都有核心代表节点，
 * 避免线性切片导致整列空白或被底层样板文件占满。
 */
export function pickBalancedNodes(
  filteredNodes: readonly CodeGraphNode[],
  cap: number,
  degreeMap: Map<string, number>,
): CodeGraphNode[] {
  const sortedAll = [...filteredNodes].sort((a, b) => {
    const diff = nodePriorityScore(b, degreeMap) - nodePriorityScore(a, degreeMap);
    return diff !== 0 ? diff : a.id.localeCompare(b.id);
  });
  if (sortedAll.length <= cap) {
    return sortedAll;
  }

  const buckets = new Map<CodeGraphLayer, CodeGraphNode[]>([
    ["frontend", []],
    ["api", []],
    ["backend", []],
    ["storage", []],
  ]);
  for (const node of sortedAll) {
    buckets.get(node.layer)?.push(node);
  }

  const activeLayers = LAYER_ORDER.map((l) => l.key).filter((k) => (buckets.get(k)?.length ?? 0) > 0);
  if (activeLayers.length === 0) {
    return [];
  }

  const picked: CodeGraphNode[] = [];
  const cursors = new Map<CodeGraphLayer, number>(activeLayers.map((k) => [k, 0]));
  const baseQuota = Math.max(1, Math.floor(cap / activeLayers.length));

  for (const layerKey of activeLayers) {
    const bucket = buckets.get(layerKey) ?? [];
    const take = Math.min(baseQuota, bucket.length, cap - picked.length);
    for (let i = 0; i < take; i += 1) {
      const node = bucket[i];
      if (node) picked.push(node);
    }
    cursors.set(layerKey, take);
  }

  // 用剩余配额按轮询补足还有余量的层级
  let progress = true;
  while (picked.length < cap && progress) {
    progress = false;
    for (const layerKey of activeLayers) {
      if (picked.length >= cap) break;
      const bucket = buckets.get(layerKey) ?? [];
      const cur = cursors.get(layerKey) ?? 0;
      if (cur < bucket.length) {
        const node = bucket[cur];
        if (node) {
          picked.push(node);
          cursors.set(layerKey, cur + 1);
          progress = true;
        }
      }
    }
  }
  return picked;
}

/**
 * 双向全链路 BFS 追踪（参考 Archify 链路聚焦模式）：
 * 沿有向边分别向上溯源调用方、向下追踪处理模块与数据实体。
 */
export function traceBidirectionalChain(
  selectedId: string,
  edges: readonly CodeGraphEdge[],
): TraceResult {
  const nodeIds = new Set<string>();
  const edgeIds = new Set<string>();
  if (!selectedId) {
    return { nodeIds, edgeIds, upstreamCount: 0, downstreamCount: 0 };
  }

  const outMap = new Map<string, CodeGraphEdge[]>();
  const inMap = new Map<string, CodeGraphEdge[]>();
  for (const edge of edges) {
    const outList = outMap.get(edge.source);
    if (outList) outList.push(edge);
    else outMap.set(edge.source, [edge]);

    const inList = inMap.get(edge.target);
    if (inList) inList.push(edge);
    else inMap.set(edge.target, [edge]);
  }

  const upVisited = new Set<string>([selectedId]);
  const upQueue: Array<{ id: string; depth: number }> = [{ id: selectedId, depth: 0 }];
  while (upQueue.length > 0 && upVisited.size < 200) {
    const current = upQueue.shift();
    if (!current || current.depth >= 6) continue;
    for (const edge of inMap.get(current.id) ?? []) {
      edgeIds.add(edge.id);
      if (!upVisited.has(edge.source)) {
        upVisited.add(edge.source);
        upQueue.push({ id: edge.source, depth: current.depth + 1 });
      }
    }
  }

  const downVisited = new Set<string>([selectedId]);
  const downQueue: Array<{ id: string; depth: number }> = [{ id: selectedId, depth: 0 }];
  while (downQueue.length > 0 && downVisited.size < 200) {
    const current = downQueue.shift();
    if (!current || current.depth >= 6) continue;
    for (const edge of outMap.get(current.id) ?? []) {
      edgeIds.add(edge.id);
      if (!downVisited.has(edge.target)) {
        downVisited.add(edge.target);
        downQueue.push({ id: edge.target, depth: current.depth + 1 });
      }
    }
  }

  for (const id of upVisited) nodeIds.add(id);
  for (const id of downVisited) nodeIds.add(id);
  return {
    nodeIds,
    edgeIds,
    upstreamCount: Math.max(0, upVisited.size - 1),
    downstreamCount: Math.max(0, downVisited.size - 1),
  };
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/** 导出当前可见视图的确定性、自包含 Archify 风格 SVG 矢量链路图。 */
export function buildStandaloneCodeGraphSvg(params: {
  width: number;
  height: number;
  nodes: readonly CodeGraphNode[];
  positions: Map<string, PositionedNode>;
  edges: ReadonlyArray<{ edge: CodeGraphEdge; d: string; highlighted: boolean; dimmed: boolean }>;
  selectedId: string;
}): string {
  const { width, height, nodes, positions, edges, selectedId } = params;
  const accentByLayer = new Map<CodeGraphLayer, string>(LAYER_ORDER.map((l) => [l.key, l.accentHex]));
  const parts: string[] = [];
  parts.push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="JetBrains Mono, Consolas, ui-monospace, monospace">`,
  );
  parts.push(`<rect width="${width}" height="${height}" fill="#020617" />`);
  parts.push(
    `<defs>` +
      `<marker id="exp-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="#475569" /></marker>` +
      `<marker id="exp-arrow-hi" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="#22D3EE" /></marker>` +
      `<marker id="exp-arrow-bad" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="#FB7185" /></marker>` +
      `</defs>`,
  );

  for (const layer of LAYER_ORDER) {
    const lx = COL_X[layer.key] - 12;
    const lw = NODE_W + 24;
    parts.push(
      `<rect x="${lx}" y="8" width="${lw}" height="${height - 16}" rx="8" fill="#0F172A" stroke="#1E293B" stroke-dasharray="4 4" />`,
    );
    parts.push(
      `<text x="${lx + 14}" y="30" fill="${layer.accentHex}" font-size="12" font-weight="700">${escapeXml(layer.label)}</text>`,
    );
  }

  for (const item of edges) {
    const stroke = item.edge.conflict ? "#FB7185" : item.highlighted ? "#22D3EE" : "#475569";
    const strokeW = item.highlighted ? 2.4 : 1.4;
    const opacity = item.dimmed ? 0.18 : item.highlighted ? 1 : 0.7;
    const dash = item.edge.conflict ? ' stroke-dasharray="4 3"' : "";
    const marker = item.edge.conflict
      ? "url(#exp-arrow-bad)"
      : item.highlighted
        ? "url(#exp-arrow-hi)"
        : "url(#exp-arrow)";
    parts.push(
      `<path d="${item.d}" fill="none" stroke="${stroke}" stroke-width="${strokeW}" stroke-opacity="${opacity}"${dash} marker-end="${marker}" />`,
    );
  }

  for (const node of nodes) {
    const pos = positions.get(node.id);
    if (!pos) continue;
    const layerAccent = accentByLayer.get(node.layer) ?? "#94A3B8";
    const stroke = node.hasConflict
      ? "#FB7185"
      : node.id === selectedId
        ? "#22D3EE"
        : node.impact === "direct"
          ? "#FBBF24"
          : "#1E293B";
    const title = node.label.length > 22 ? `${node.label.slice(0, 21)}…` : node.label;
    const coord = `${node.file || node.module}${node.line > 0 ? `:${node.line}` : ""}`;
    const coordShort = coord.length > 26 ? `…${coord.slice(-25)}` : coord;
    parts.push(
      `<g transform="translate(${pos.x},${pos.y})">` +
        `<rect width="${NODE_W}" height="${NODE_H}" rx="8" fill="#0F172A" stroke="${stroke}" stroke-width="1.5" />` +
        `<rect width="4" height="${NODE_H}" rx="2" fill="${layerAccent}" />` +
        `<text x="14" y="24" fill="#FFFFFF" font-size="12" font-weight="600">${escapeXml(title)}</text>` +
        `<text x="14" y="44" fill="#94A3B8" font-size="10">${escapeXml(coordShort)}</text>` +
        `</g>`,
    );
  }
  parts.push("</svg>");
  return parts.join("\n");
}
