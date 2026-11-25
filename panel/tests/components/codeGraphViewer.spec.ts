import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CodeGraphViewer from "@/components/CodeGraphViewer.vue";
import { NODE_CAP, pickBalancedNodes } from "@/components/codeGraphHelpers";
import type { CodeGraph, CodeGraphLayer, CodeGraphNode } from "@/domain/types";

const sampleGraph: CodeGraph = {
  generatedAt: "2026-11-24T12:00:00Z",
  sourceHash: "abc123",
  stats: {
    totalFiles: 4,
    frontend: 1,
    api: 1,
    backend: 1,
    storage: 1,
    edges: 3,
    conflicts: 1,
  },
  nodes: [
    {
      id: "fe:app.js",
      label: "app.js",
      layer: "frontend",
      kind: "js",
      file: "frontend/app.js",
      line: 2,
      module: "frontend",
      meta: {},
      hasConflict: false,
      impact: "transitive",
    },
    {
      id: "api:GET:/api/orders",
      label: "GET /api/orders",
      layer: "api",
      kind: "get",
      file: "backend/routes.py",
      line: 14,
      module: "backend",
      meta: {},
      hasConflict: true,
      impact: "direct",
    },
    {
      id: "be:backend/routes.py",
      label: "routes.py",
      layer: "backend",
      kind: "python",
      file: "backend/routes.py",
      line: 1,
      module: "backend",
      meta: {},
      hasConflict: false,
      impact: "direct",
    },
    {
      id: "st:orders",
      label: "orders",
      layer: "storage",
      kind: "table",
      file: "backend/models.py",
      line: 3,
      module: "backend",
      meta: {},
      hasConflict: false,
      impact: "none",
    },
  ],
  edges: [
    {
      id: "e1",
      source: "fe:app.js",
      target: "api:GET:/api/orders",
      kind: "calls_api",
      label: "GET /api/orders",
      conflict: false,
    },
    {
      id: "e2",
      source: "api:GET:/api/orders",
      target: "be:backend/routes.py",
      kind: "handled_by",
      label: "处理",
      conflict: false,
    },
    {
      id: "e3",
      source: "be:backend/routes.py",
      target: "st:orders",
      kind: "reads_writes",
      label: "orders",
      conflict: false,
    },
  ],
  conflicts: [
    {
      kind: "contract_mismatch",
      severity: "error",
      detail: "GET /api/ghost 未在后端路由定义",
      file: "frontend/app.js",
      nodeIds: ["fe:app.js"],
    },
  ],
  moduleChunks: [],
  externalSystems: [
    {
      id: "ext:postgres",
      name: "PostgreSQL",
      category: "database",
      detail: "依赖标识 psycopg",
      source: "pyproject.toml",
    },
  ],
  impact: {
    changedFiles: ["backend/routes.py"],
    directNodeIds: ["api:GET:/api/orders", "be:backend/routes.py"],
    affectedNodeIds: ["fe:app.js", "api:GET:/api/orders", "be:backend/routes.py"],
    affectedModules: ["backend", "frontend"],
    affectedRoutes: ["GET /api/orders"],
  },
};

describe("CodeGraphViewer", () => {
  it("renders 4 layers, switches graph/list views, filters impact & conflicts, and emits openSource/testAffected", async () => {
    const wrapper = mount(CodeGraphViewer, {
      props: { graph: sampleGraph, canOpenSource: true },
    });

    const graphModeBtn = wrapper
      .findAll(".code-graph-mode-actions .filter-btn")
      .find((b) => b.text() === "精确链路");
    expect(graphModeBtn).toBeDefined();
    await graphModeBtn!.trigger("click");

    expect(wrapper.findAll(".code-graph-lane")).toHaveLength(4);
    expect(wrapper.findAll(".code-graph-node")).toHaveLength(4);
    expect(wrapper.findAll("svg.code-graph-svg path.code-graph-edge")).toHaveLength(3);

    // 点击冲突列表中的打开源码按钮
    await wrapper.find(".code-graph-conflicts .link-btn").trigger("click");
    expect(wrapper.emitted("openSource")?.[0]).toEqual(["frontend/app.js", 1]);

    // 点击节点打开详情栏并发出跳源码与专项测试事件
    const apiNodeBtn = wrapper.findAll(".code-graph-node").find((b) => b.text().includes("GET /api/orders"));
    expect(apiNodeBtn).toBeDefined();
    await apiNodeBtn!.trigger("click");
    expect(wrapper.find(".code-graph-inspector").text()).toContain("backend/routes.py:14");

    const inspectorBtns = wrapper.findAll(".code-graph-inspector-actions button");
    await inspectorBtns[0]!.trigger("click");
    expect(wrapper.emitted("openSource")?.[1]).toEqual(["backend/routes.py", 14]);

    await inspectorBtns[1]!.trigger("click");
    expect(wrapper.emitted("testAffected")?.[0]?.[0]).toContain("GET /api/orders");

    // 仅看 Git 受影响筛选应过滤掉未受影响的 orders 存储表
    const impactBtn = wrapper
      .findAll(".code-graph-toolbar .filter-btn")
      .find((b) => b.text().includes("仅看 Git 受影响"));
    await impactBtn!.trigger("click");
    expect(wrapper.findAll(".code-graph-node")).toHaveLength(3);

    // 切换到列表视图
    const listBtn = wrapper
      .findAll(".code-graph-mode-actions .filter-btn")
      .find((b) => b.text() === "列表");
    await listBtn!.trigger("click");
    expect(wrapper.find("table.table").exists()).toBe(true);
  });

  it("picks balanced nodes across all 4 layers when total exceeds NODE_CAP", () => {
    const layers: Array<{ layer: CodeGraphLayer; count: number }> = [
      { layer: "frontend", count: 120 },
      { layer: "api", count: 10 },
      { layer: "backend", count: 10 },
      { layer: "storage", count: 10 },
    ];
    const denseNodes: CodeGraphNode[] = [];
    for (const group of layers) {
      for (let i = 0; i < group.count; i += 1) {
        denseNodes.push({
          id: `${group.layer}:n${i}`,
          label: `n${i}`,
          layer: group.layer,
          kind: "mod",
          file: `src/${group.layer}/n${i}.ts`,
          line: 1,
          module: group.layer,
          meta: {},
          hasConflict: i === 0,
          impact: i === 1 ? "direct" : "none",
        });
      }
    }
    const picked = pickBalancedNodes(denseNodes, NODE_CAP, new Map());
    expect(picked).toHaveLength(NODE_CAP);
    for (const group of layers) {
      expect(picked.some((n) => n.layer === group.layer)).toBe(true);
    }
  });

  it("supports bidirectional trace focus, conflict locate, fitToView and SVG export", async () => {
    const wrapper = mount(CodeGraphViewer, {
      props: { graph: sampleGraph, canOpenSource: true },
    });

    // 通过冲突栏“定位节点”跳转并高亮对应节点
    const locateBtn = wrapper
      .findAll(".code-graph-conflicts .link-btn")
      .find((b) => b.text().includes("定位节点"));
    expect(locateBtn).toBeDefined();
    await locateBtn!.trigger("click");
    expect(wrapper.find(".code-graph-inspector").text()).toContain("app.js");
    expect(wrapper.find(".code-graph-trace-chain").exists()).toBe(true);
    expect(wrapper.findAll(".trace-layer-pill").length).toBeGreaterThanOrEqual(4);

    // 切换“聚焦此链” / “退出聚焦”
    const focusBtn = wrapper
      .findAll(".code-graph-inspector-actions button")
      .find((b) => b.text().includes("聚焦此链"));
    expect(focusBtn).toBeDefined();
    await focusBtn!.trigger("click");
    expect(wrapper.find(".code-graph-viewport").classes()).toContain("is-focus-mode");
    await focusBtn!.trigger("click");
    expect(wrapper.find(".code-graph-viewport").classes()).not.toContain("is-focus-mode");

    // 适配全图与导出 SVG
    const fitBtn = wrapper
      .findAll(".code-graph-zoom button")
      .find((b) => b.text() === "适配全图");
    expect(fitBtn).toBeDefined();
    await fitBtn!.trigger("click");

    const exportBtn = wrapper
      .findAll(".code-graph-mode-actions button")
      .find((b) => b.text() === "导出 SVG");
    expect(exportBtn).toBeDefined();
    await exportBtn!.trigger("click");
    const exported = wrapper.emitted("exportSvg");
    expect(exported).toHaveLength(1);
    const svgPayload = exported?.[0]?.[0];
    expect(typeof svgPayload).toBe("string");
    expect(String(svgPayload)).toContain("<svg");
    expect(String(svgPayload)).toContain("GET /api/orders");
  });

  it("renders architecture panorama by default with stories, summary cards, drill-down and panorama SVG export", async () => {
    const wrapper = mount(CodeGraphViewer, {
      props: { graph: sampleGraph, projectName: "Orders Demo", canOpenSource: true },
    });

    // 默认应渲染架构全景卡片、正交折线、引导视图条与底部 3 张摘要卡
    expect(wrapper.find(".arch-viewport").exists()).toBe(true);
    expect(wrapper.findAll(".arch-card").length).toBeGreaterThanOrEqual(4);
    expect(wrapper.findAll(".arch-edge-path").length).toBeGreaterThanOrEqual(2);
    expect(wrapper.findAll(".arch-story-pill")).toHaveLength(3);
    expect(wrapper.findAll(".arch-summary-card")).toHaveLength(3);
    expect(wrapper.text()).toContain("PostgreSQL");

    // 切换引导视图至“跨层契约风险点”并核对高亮状态
    const riskStory = wrapper.findAll(".arch-story-pill").find((b) => b.text().includes("跨层契约风险点"));
    expect(riskStory).toBeDefined();
    await riskStory!.trigger("click");
    expect(riskStory!.classes()).toContain("is-active");
    expect(wrapper.findAll(".arch-card.is-highlighted").length).toBeGreaterThanOrEqual(1);

    // 导出全景架构 SVG
    const exportBtn = wrapper.findAll(".code-graph-mode-actions button").find((b) => b.text() === "导出 SVG");
    await exportBtn!.trigger("click");
    const archSvg = wrapper.emitted("exportSvg")?.[0]?.[0];
    expect(String(archSvg)).toContain("Orders Demo 系统架构全景图");
    expect(String(archSvg)).toContain("PostgreSQL");

    // 点击业务卡片打开详情并一键下钻到“精确链路”，随后可返回全景
    const backendCard = wrapper.findAll(".arch-card.is-band-backend").find((b) => b.text().includes("backend"));
    expect(backendCard).toBeDefined();
    await backendCard!.trigger("click");
    const drillBtn = wrapper.findAll(".arch-inspector button").find((b) => b.text().includes("下钻该模块精确链路"));
    expect(drillBtn).toBeDefined();
    await drillBtn!.trigger("click");
    expect(wrapper.find(".code-graph-viewport").exists()).toBe(true);

    const backBtn = wrapper.findAll(".code-graph-toolbar button").find((b) => b.text().includes("返回架构全景"));
    expect(backBtn).toBeDefined();
    await backBtn!.trigger("click");
    expect(wrapper.find(".arch-viewport").exists()).toBe(true);
  });
});
