import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createProjectRecord } from "@/domain/factory";
import type { Project } from "@/domain/types";
import type { ProjectRepository } from "@/repositories/ProjectRepository";
import { setRepository, useWorkspaceStore } from "@/stores/workspace";

function project(id: string): Project {
  const record = createProjectRecord({ id, name: id, root: `D:/work/${id}` });
  return { ...record, analysisStatus: "ready", analysisStartedAt: "" };
}

function fakeRepository(overrides: Partial<ProjectRepository> = {}): ProjectRepository {
  const base = {
    mode: "local" as const,
    capabilities: {
      hostGithub: false,
      evidenceFiles: true,
      browserBackup: true,
      linkJson: true,
    },
    listProjects: vi.fn(async () => [project("p1")]),
    getProject: vi.fn(async (id: string) => project(id)),
    getLastProjectId: vi.fn(async () => null),
    setLastProjectId: vi.fn(async () => undefined),
    beginAnalysis: vi.fn(async (id: string) => ({
      ...project(id),
      analysisStatus: "analyzing" as const,
    })),
    saveAnalysis: vi.fn(async (id: string) => project(id)),
    failAnalysis: vi.fn(async (id: string, message: string) => ({
      ...project(id),
      analysisStatus: "failed" as const,
      analysisError: message,
    })),
  };
  return { ...base, ...overrides } as unknown as ProjectRepository;
}

describe("workspace store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    setRepository(null);
  });

  it("persists a failed analysis so the project does not stay analyzing", async () => {
    const repo = fakeRepository({
      saveAnalysis: vi.fn(async () => {
        throw new Error("CLI 超时");
      }),
    });
    setRepository(repo);
    const store = useWorkspaceStore();
    await store.bootstrap();
    await expect(store.analyzeProject("p1")).rejects.toThrow("CLI 超时");
    expect(repo.failAnalysis).toHaveBeenCalledWith("p1", "CLI 超时");
    expect(store.currentProject?.analysisStatus).toBe("failed");
  });

  it("returns the full project when switching and does not block on last-project persistence", async () => {
    const summary = { ...project("p2"), tasks: [], summaryOnly: true };
    const full = project("p2");
    const repo = fakeRepository({
      listProjects: vi.fn(async () => [project("p1"), summary]),
      getProject: vi.fn(async (id: string) => (id === "p2" ? full : project(id))),
      setLastProjectId: vi.fn(async () => {
        throw new Error("quota");
      }),
    });
    setRepository(repo);
    const store = useWorkspaceStore();
    await store.bootstrap();
    const result = await store.setCurrentProject("p2");
    expect(result).toBe(full);
    expect(result?.summaryOnly).toBeUndefined();
    expect(store.currentProjectId).toBe("p2");
    expect(store.currentProject).toStrictEqual(full);
  });
});
