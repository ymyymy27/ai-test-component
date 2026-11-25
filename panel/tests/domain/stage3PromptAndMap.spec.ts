import { describe, expect, it } from "vitest";

import {
  buildCaseFixPrompt,
  buildIssueFixPrompt,
  buildMinimalReproCommand,
} from "@/domain/aiPrompt";
import {
  clearHostDiagnostics,
  onHostWorkspaceDirty,
  openHostSource,
  publishHostDiagnostics,
} from "@/host/bridge";
import {
  extractSourceLocation,
  mapCodeGraph,
  mapConfidenceSummary,
  mapLocalGit,
  mapPreflight,
  mapProbes,
} from "@/host/map";

describe("Stage 3 prompt builder, mappers and host bridge fallbacks", () => {
  it("builds minimal repro command and enriched AI fix task packages", () => {
    expect(
      buildMinimalReproCommand({
        testNodes: ["tests/test_refund.py::test_dup_refund"],
        method: "pytest",
      }),
    ).toBe("python -m pytest tests/test_refund.py::test_dup_refund -v --tb=short");

    expect(buildMinimalReproCommand({ method: "cmd: npx playwright test e2e/login.spec.ts" })).toBe(
      "npx playwright test e2e/login.spec.ts",
    );

    const casePrompt = buildCaseFixPrompt({
      projectName: "订单服务",
      projectRoot: "E:/orders",
      testCase: {
        id: "case_1",
        title: "重复退款",
        method: "pytest",
        expected: "409",
        actual: "200",
        message: "status mismatch",
        errorType: "AssertionError",
        stackKey: 'File "orders/routes.py", line 42',
        evidence: [],
        testNodes: ["tests/test_orders.py::test_dup"],
        confidence: "confirmed",
        confidenceReason: "连续 2 次稳定复现且伴随服务异常",
        phaseFailed: "verify",
        serviceLogIssues: ["ERROR: ZeroDivisionError in orders/routes.py:42"],
        judgement: {
          verdict: "confirmed_defect",
          source: "model",
          reason: "幂等键校验遗漏",
          evidencePoints: ["routes.py:42"],
          suggestedFix: "对同一 order_id 加唯一约束检查",
        },
        file: "orders/routes.py",
        line: 42,
      },
    });
    expect(casePrompt).toContain("铁证缺陷");
    expect(casePrompt).toContain("最小复现命令");
    expect(casePrompt).toContain("orders/routes.py:42");
    expect(casePrompt).toContain("窗口期后台服务异常日志");
    expect(casePrompt).toContain("幂等键校验遗漏");

    const issuePrompt = buildIssueFixPrompt({
      projectName: "订单服务",
      reproCommand: "python -m pytest tests/test_orders.py::test_dup -v",
      issue: {
        id: "iss_1",
        title: "退款幂等失效",
        severity: "P1",
        status: "pending",
        expected: "409",
        actual: "200",
        steps: ["提交两次退款"],
        signature: "orders/routes.py:42",
        evidenceRefs: ["ev_1"],
        confidence: "confirmed",
        file: "orders/routes.py",
        line: 42,
      },
    });
    expect(issuePrompt).toContain("铁证缺陷");
    expect(issuePrompt).toContain("orders/routes.py:42");
    expect(issuePrompt).toContain("最小复现命令");
  });

  it("extracts source location and maps stage 1B/2 DTO structures cleanly", () => {
    expect(extractSourceLocation('File "backend/api.py", line 88, in handler')).toEqual({
      file: "backend/api.py",
      line: 88,
    });
    expect(extractSourceLocation("backend/service.ts:15:4 unexpected token")).toEqual({
      file: "backend/service.ts",
      line: 15,
    });
    expect(extractSourceLocation("https://127.0.0.1:8000/api/orders")).toBeNull();

    const git = mapLocalGit({
      is_git: true,
      available: true,
      branch: "main",
      short_head: "1234567",
      dirty: true,
      changed_files: ["backend/api.py"],
      diff_stat: [{ path: "backend/api.py", additions: 3, deletions: 1, status: "M" }],
    });
    expect(git?.branch).toBe("main");
    expect(git?.diffStat[0]?.additions).toBe(3);

    const graph = mapCodeGraph({
      stats: { total_files: 3, frontend: 1, api: 1, backend: 1, storage: 0, edges: 1, conflicts: 0 },
      nodes: [
        { id: "n1", label: "GET /x", layer: "api", file: "backend/api.py", line: 10, impact: "direct" },
      ],
      impact: { affected_routes: ["GET /x"], affected_modules: ["backend"] },
    });
    expect(graph?.nodes[0]?.impact).toBe("direct");
    expect(graph?.impact.affectedRoutes).toEqual(["GET /x"]);

    const preflight = mapPreflight({ ok: false, blocking_reasons: ["端口 8000 被占用"] });
    expect(preflight?.ok).toBe(false);
    expect(preflight?.blockingReasons).toEqual(["端口 8000 被占用"]);

    const probes = mapProbes([
      {
        probe_id: "static_assets",
        name: "静态资源探针",
        status: "passed",
        suppressed_findings: [{ detail: "dev chunk skipped", convention_matched: true }],
      },
    ]);
    expect(probes[0]?.suppressedFindings).toHaveLength(1);

    expect(mapConfidenceSummary({ confirmed: 2, suspected: 1, blocked: 0 })).toEqual({
      confirmed: 2,
      suspected: 1,
      blocked: 0,
    });
  });

  it("degrades new host bridge helpers cleanly without window.aitestHost", async () => {
    await expect(openHostSource("backend/api.py", 10)).rejects.toThrow("不支持一键跳源码");
    expect(await publishHostDiagnostics([{ file: "backend/api.py", line: 1, message: "err" }])).toBe(0);
    expect(await clearHostDiagnostics()).toBe(false);
    const unsub = onHostWorkspaceDirty(() => undefined);
    unsub();
  });
});
