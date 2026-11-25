"""独立裁判复核与三档可信度派生核心模块。

判定总原则：
1. 业务主结论永远保持严格四态（passed / failed / incomplete / not_applicable）；
2. 本模块在 core/ 内统一计算缺陷/用例的三档可信度（confirmed / suspected / blocked），
   让测试、报告与问题账本保持同一事实口径；
3. 确定性事实（后台 5xx / Traceback、确定性硬探针、复现失败）优先于大模型裁定。
"""

from __future__ import annotations

import re
from typing import Any

from aitest.core.shared_config import extract_conventions, match_convention
from aitest.errors import AiTestError
from aitest.model_config import build_client, load_project_config
from aitest.models import Case, ConfidenceTier, RunCase
from aitest.security import redact, redact_tree
from aitest.storage.workspace import Workspace

_FATAL_SERVICE_RE = re.compile(
    r"(?:后台返回 HTTP 50|后台出现未捕获异常|UnhandledPromiseRejection|CRITICAL:|FATAL:)",
    re.IGNORECASE,
)


def is_fatal_service_issue(message: str) -> bool:
    """判断服务窗口日志条目是否属于明确的产品服务端错误信号。"""
    return bool(_FATAL_SERVICE_RE.search(message))


def _matching_probe_findings(
    case: Case,
    probe_results: list[dict[str, Any]] | None,
) -> list[str]:
    """查找与当前用例路由/资产相关的确定性硬探针失败项。"""
    if not probe_results:
        return []
    haystack = " ".join(
        part
        for part in (
            case.title,
            case.expected,
            case.expectation_source,
            case.verify_method,
            case.script_content or "",
            str((case.frozen_expectation or {}).get("path") or ""),
        )
        if part
    ).lower()
    matched: list[str] = []
    for probe in probe_results:
        if not isinstance(probe, dict) or probe.get("status") != "failed":
            continue
        for finding in probe.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            target = str(
                finding.get("path") or finding.get("href") or finding.get("url") or ""
            ).strip().lower()
            detail = str(finding.get("detail") or "").strip()
            if not detail:
                continue
            if target and (target in haystack or target.lstrip("/") in haystack):
                matched.append(detail)
    return matched


def judge_case_failure(
    ws: Workspace,
    project_id: str,
    *,
    case: Case,
    outputs: list[bytes],
    message: str,
    service_log_issues: list[str],
    probe_results: list[dict[str, Any]] | None,
    secrets: list[str],
) -> dict[str, Any]:
    """对连续失败的用例做独立裁判复核（确定性证据优先，再调用可选模型裁判）。"""
    fatal_logs = [item for item in service_log_issues if is_fatal_service_issue(item)]
    if fatal_logs:
        return {
            "verdict": "confirmed_defect",
            "source": "deterministic",
            "reason": fatal_logs[0],
            "evidence_points": fatal_logs,
            "suggested_fix": "",
        }
    probe_hits = _matching_probe_findings(case, probe_results)
    if probe_hits:
        return {
            "verdict": "confirmed_defect",
            "source": "deterministic",
            "reason": probe_hits[0],
            "evidence_points": probe_hits,
            "suggested_fix": "",
        }

    tail = b"\n".join(outputs).decode("utf-8", errors="replace")[-4000:]
    try:
        proj_cfg = load_project_config(ws, project_id)
    except AiTestError:
        proj_cfg = {}
    conventions = extract_conventions(proj_cfg)
    matched_conv = match_convention(
        f"{message}\n{tail}", conventions, scope="case"
    )
    if matched_conv is not None:
        conv_detail = matched_conv.get("reason") or matched_conv.get("pattern")
        return {
            "verdict": "inconclusive",
            "source": "convention",
            "reason": f"命中项目错题本正常约定：{conv_detail}",
            "evidence_points": [str(matched_conv.get("pattern") or "")],
            "suggested_fix": "",
            "convention_id": matched_conv.get("id", ""),
        }
    judge_ctx = redact_tree(
        {
            "case_id": case.case_id,
            "acceptance_item": case.expectation_source,
            "expected": case.expected,
            "frozen_expectation": case.frozen_expectation,
            "verify_method": case.verify_method,
            "script_excerpt": (case.script_content or "")[:4000],
            "failure_message": message,
            "output_tail": tail,
            "service_log_issues": service_log_issues,
        },
        secrets,
    )
    try:
        client = build_client(ws, project_id)
    except AiTestError:
        client = None
    judger = getattr(client, "judge_failure", None) if client is not None else None
    if callable(judger):
        try:
            verdict_data = judger(judge_ctx)
            if isinstance(verdict_data, dict):
                v = str(verdict_data.get("verdict") or "")
                if v in {"confirmed_defect", "script_error", "inconclusive"}:
                    return {
                        "verdict": v,
                        "source": "model",
                        "reason": redact(str(verdict_data.get("reason") or ""), secrets),
                        "evidence_points": [
                            redact(str(p), secrets)
                            for p in (verdict_data.get("evidence_points") or [])
                            if str(p).strip()
                        ],
                        "suggested_fix": redact(
                            str(verdict_data.get("suggested_fix") or ""), secrets
                        ),
                    }
        except AiTestError:
            pass

    return {
        "verdict": "confirmed_defect",
        "source": "deterministic",
        "reason": redact(message, secrets) or "用例连续执行失败且未命中已知脚本/环境阻塞特征",
        "evidence_points": [],
        "suggested_fix": "",
    }


def compute_case_confidence(
    case: Case | None,
    run_case: RunCase | None,
    *,
    effective_result: str,
    probe_results: list[dict[str, Any]] | None = None,
) -> tuple[ConfidenceTier | None, str]:
    """统一计算单条用例的三档可信度标签（confirmed / suspected / blocked）。"""
    if effective_result in {"blocked", "error", "not_run", "manual_pending"}:
        last_msg = (
            run_case.attempts[-1].message
            if run_case is not None and run_case.attempts
            else ""
        )
        return "blocked", last_msg or f"状态为 {effective_result}，尚不能视为有效结论"

    if effective_result == "passed":
        if run_case is not None:
            had_prior_fail = any(att.result == "failed" for att in run_case.attempts[:-1])
            if had_prior_fail:
                return "suspected", "首次失败后重跑通过（疑似不稳定抖动，已保留多次尝试证据）"
            if run_case.service_log_issues:
                return "suspected", "用例返回通过，但后台服务日志存在异常或零命中告警"
        return None, ""

    # effective_result == "failed"
    if run_case is None:
        return "suspected", "缺少执行记录，标记为疑似问题"
    if any(is_fatal_service_issue(item) for item in run_case.service_log_issues):
        return "confirmed", run_case.service_log_issues[0]
    if case is not None:
        probe_hits = _matching_probe_findings(case, probe_results)
        if probe_hits:
            return "confirmed", f"确定性探针交叉印证：{probe_hits[0]}"
    if (
        isinstance(run_case.judgement, dict)
        and run_case.judgement.get("verdict") == "confirmed_defect"
    ):
        reason = str(run_case.judgement.get("reason") or "").strip()
        return "confirmed", reason or "独立裁判结合证据确认为产品缺陷"
    failed_attempts = sum(1 for att in run_case.attempts if att.result == "failed")
    if failed_attempts >= 2:
        last_msg = run_case.attempts[-1].message if run_case.attempts else ""
        return "confirmed", f"连续 {failed_attempts} 次复现失败：{last_msg or '结果稳定失败'}"
    last_msg = run_case.attempts[-1].message if run_case.attempts else ""
    return "suspected", last_msg or "单次失败且缺少复现或确定性佐证"


def summarize_confidence(case_details: list[dict[str, Any]]) -> dict[str, int]:
    """汇总报告用例明细中的三档可信度计数（铁证 / 疑似 / 阻塞）。"""
    counts = {"confirmed": 0, "suspected": 0, "blocked": 0}
    for detail in case_details:
        tier = detail.get("confidence_tier")
        if tier in counts:
            counts[tier] += 1
    return counts
