import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { describe, expect, it } from "vitest";

import { buildArchitecturePanorama } from "@/components/codeGraphArchitecture";
import type { CodeGraph } from "@/domain/types";
import type { ProjectRepository } from "@/repositories/ProjectRepository";
import { setRepository, useWorkspaceStore } from "@/stores/workspace";
import ArchitectureView from "@/views/ArchitectureView.vue";
import { seedProject } from "../fixtures";

function graphWithDuplicateModuleNames(): CodeGraph {
  return {
    generatedAt: "2026-11-25T10:00:00Z",
    sourceHash: "abc123",
    stats: {
      totalFiles: 3,
      frontend: 1,
      api: 0,
      backend: 1,
      storage: 1,
      edges: 2,
      conflicts: 0,
    },
    nodes: [
      {
        id: "fe_1",
        layer: "frontend",
        label: "OrderPage",
        file: "src/views/OrderPage.vue",
        line: 1,
        module: "frontend",
        kind: "component",
        meta: {},
        impact: "direct",
        hasConflict: false,
      },
      {
        id: "be_1",
        layer: "backend",
        label: "OrderService",
        file: "src/backend/order.py",
        line: 12,
        module: "backend",
        kind: "service",
        meta: {},
        impact: "transitive",
        hasConflict: false,
      },
      {
        id: "st_1",
        layer: "storage",
        label: "orders_table",
        file: "src/backend/models.py",
        line: 8,
        module: "backend",
        kind: "table",
        meta: {},
        impact: "none",
        hasConflict: false,
      },
    ],
    edges: [
      {
        id: "e1",
        source: "fe_1",
        target: "be_1",
        kind: "http_call",
        label: "POST /orders",
        conflict: false,
      },
      {
        id: "e2",
        source: "be_1",
        target: "st_1",
        kind: "reads_writes",
        label: "orders",
        conflict: false,
      },
    ],
    conflicts: [],
    moduleChunks: [],
    impact: {
      changedFiles: ["src/views/OrderPage.vue"],
      directNodeIds: ["fe_1"],
      affectedNodeIds: ["fe_1", "be_1"],
      affectedModules: ["frontend", "backend"],
      affectedRoutes: ["/orders"],
    },
  };
}

describe("ArchitectureView & buildArchitecturePanorama", () => {
  it("deduplicates identical module names across different bands with Chinese band prefixes", () => {
    const panorama = buildArchitecturePanorama(graphWithDuplicateModuleNames(), "商城");
    const titles = panorama.cards.map((card) => card.title);
    expect(new Set(titles).size).toBe(titles.length);
    expect(titles).toContain("后端 · backend");
    expect(titles).toContain("存储 · backend");
    expect(titles).not.toContain("backend");
  });

  it("mounts ArchitectureView inside full-screen route and offers navigation back to overview", async () => {
    const seeded = await seedProject("D:/work/arch-demo", "架构演示");
    const graph = graphWithDuplicateModuleNames();
    const full = { ...seeded.project, codeGraph: graph };
    setRepository({
      mode: "local",
      capabilities: seeded.repo.capabilities,
      listProjects: async () => [full],
      getProject: async () => full,
      getLastProjectId: async () => full.id,
      setLastProjectId: async () => undefined,
      refreshProject: async () => full,
      createProject: seeded.repo.createProject.bind(seeded.repo),
      saveAnalysis: seeded.repo.saveAnalysis.bind(seeded.repo),
      beginAnalysis: seeded.repo.beginAnalysis.bind(seeded.repo),
      failAnalysis: seeded.repo.failAnalysis.bind(seeded.repo),
    } as unknown as ProjectRepository);

    const workspace = useWorkspaceStore();
    await workspace.bootstrap();

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/projects/:projectId/overview", component: { template: "<div />" } },
        { path: "/projects/:projectId/architecture", component: ArchitectureView },
      ],
    });
    await router.push(`/projects/${full.id}/architecture`);
    await router.isReady();

    const wrapper = mount(ArchitectureView, {
      global: { plugins: [router] },
    });
    expect(wrapper.text()).toContain("项目架构");
    expect(wrapper.text()).toContain("返回概览");
    expect(wrapper.findAll(".arch-card").length).toBeGreaterThanOrEqual(3);
  });
});
