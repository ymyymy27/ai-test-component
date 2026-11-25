import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import GitStatusBar from "@/components/GitStatusBar.vue";
import type { CodeGraph, LocalGitInfo } from "@/domain/types";

const gitInfo: LocalGitInfo = {
  isGit: true,
  available: true,
  reason: "",
  branch: "feat/stage3",
  detached: false,
  head: "abcdef123456",
  shortHead: "abcdef1",
  headAuthor: "tester",
  headTime: "2026-11-24T12:00:00Z",
  headSubject: "feat: support git status bar",
  baseBranch: "main",
  remoteUrl: "https://example.com/repo.git",
  latestTag: "v0.5.0",
  ahead: 2,
  behind: 1,
  dirty: true,
  stagedFiles: ["src/a.py"],
  unstagedFiles: ["src/b.py"],
  untrackedFiles: [],
  changedFiles: ["src/a.py", "src/b.py"],
  diffStat: [
    { path: "src/a.py", additions: 10, deletions: 3, status: "M" },
    { path: "src/b.py", additions: 5, deletions: 1, status: "M" },
  ],
  changedHunks: [{ path: "src/a.py", start: 28, count: 4 }],
  recentCommits: [],
  hotspots: [],
};

const impactGraph: CodeGraph = {
  generatedAt: "2026-11-24T12:00:00Z",
  sourceHash: "h1",
  stats: { totalFiles: 2, frontend: 0, api: 1, backend: 1, storage: 0, edges: 1, conflicts: 0 },
  nodes: [],
  edges: [],
  conflicts: [],
  moduleChunks: [],
  impact: {
    changedFiles: ["src/a.py"],
    directNodeIds: ["be:src/a.py"],
    affectedNodeIds: ["be:src/a.py", "api:POST:/orders"],
    affectedModules: ["orders"],
    affectedRoutes: ["POST /orders"],
  },
};

describe("GitStatusBar", () => {
  it("displays git branch, ahead/behind, diff totals, dirty files and triggers targeted testing & openSource", async () => {
    const wrapper = mount(GitStatusBar, {
      props: {
        localGit: gitInfo,
        codeGraph: impactGraph,
        dirtyFiles: ["src/a.py"],
      },
    });

    expect(wrapper.text()).toContain("feat/stage3");
    expect(wrapper.text()).toContain("abcdef1");
    expect(wrapper.text()).toContain("↑2 ↓1");
    expect(wrapper.text()).toContain("+15");
    expect(wrapper.text()).toContain("-4");
    expect(wrapper.text()).toContain("源码已保存更新（1 个文件）");

    const affectedBtn = wrapper
      .findAll("button")
      .find((b) => b.text().includes("测受影响范围"));
    await affectedBtn!.trigger("click");
    const targeted = wrapper.emitted("testAffected")?.[0]?.[0] as string;
    expect(targeted).toContain("orders");
    expect(targeted).toContain("POST /orders");

    // 展开变动文件抽屉并跳转对应变更行
    const toggleBtn = wrapper.findAll("button.link-btn").find((b) => b.text().includes("变动文件"));
    await toggleBtn!.trigger("click");
    const fileChip = wrapper.find(".git-file-chip");
    await fileChip.trigger("click");
    expect(wrapper.emitted("openSource")?.[0]).toEqual(["src/a.py", 28]);
  });

  it("degrades smoothly when repository is not a git repo", async () => {
    const wrapper = mount(GitStatusBar, {
      props: {
        localGit: { ...gitInfo, isGit: false, available: false },
        codeGraph: null,
        dirtyFiles: [],
      },
    });
    expect(wrapper.text()).toContain("未检测到 Git 仓库");
    await wrapper.find("button.btn-ghost").trigger("click");
    expect(wrapper.emitted("refresh")).toHaveLength(1);
  });
});
