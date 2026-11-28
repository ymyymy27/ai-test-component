"""独立裁判复核与三档可信度派生核心模块。

判定总原则：
1. 业务主结论永远保持严格四态（passed / failed / incomplete / not_applicable）；
2. 本模块在 core/ 内统一计算缺陷/用例的三档可信度（confirmed / suspected / blocked），
   让测试、报告与问题账本保持同一事实口径；
3. 确定性事实（后台 5xx / Traceback、确定性硬探针、复现失败）优先于大模型裁定。
"""

from __future__ import annotations

import json
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
_SCRIPT_WRITE_CALL_RE = re.compile(
    r"(?:requests\.(?:patch|put|delete)\s*\(|\.(?:patch|put|delete)\s*\(|method\s*=\s*['\"](?:PATCH|PUT|DELETE)['\"])",
    re.IGNORECASE,
)
_HARD_SCRIPT_SYNTAX_RE = re.compile(
    r"\b(?:SyntaxError|IndentationError|ImportError|ModuleNotFoundError|NameError)\b"
)
_RUNTIME_TYPE_ATTR_RE = re.compile(r"\b(?:TypeError|AttributeError)\b")
_HTTP_CONSTRUCT_ERROR_RE = re.compile(
    r"(?:引用了未定义变量|must be an object|is missing a url|only supports http/https|"
    r"json is not serializable|has an invalid method|stage must be|Field required|Input should be)",
    re.IGNORECASE,
)
_ASSERTION_OR_HTTP_FAIL_RE = re.compile(
    r"(?:AssertionError|\bassert\b|HTTP\s+50\d|status\s*==?\s*50\d|assert(?:ion)?\s+failed)",
    re.IGNORECASE,
)
_ENV_BLOCK_RE = re.compile(
    r"(?:环境|前置|未就绪|连接拒绝|ConnectionRefused|ECONNREFUSED|timed?\s*out|超时|缺少依赖|数据未就绪)",
    re.IGNORECASE,
)


def _is_script_runtime_crash(case: Case | None, text: str) -> bool:
    """区分测试脚本/请求构造错误与被测产品业务断言失败或服务端 500 崩溃。"""
    if not text:
        return False
    method = case.verify_method.strip() if case is not None and case.verify_method else ""
    if method.startswith("http:"):
        return bool(_HTTP_CONSTRUCT_ERROR_RE.search(text)) and not bool(
            _ASSERTION_OR_HTTP_FAIL_RE.search(text)
        )
    if _ASSERTION_OR_HTTP_FAIL_RE.search(text):
        return False
    if _HARD_SCRIPT_SYNTAX_RE.search(text):
        return True
    if _RUNTIME_TYPE_ATTR_RE.search(text):
        return "traceback" in text.lower() or "line " in text.lower() or ".py" in text.lower()
    return False
_BIASED_WORDS_RE = re.compile(
    r"(?:建议通过|全部通过|可以上线|建议上线|ignore\s+failure|可忽略失败|不算缺陷|should\s+pass|mark\s+as\s+passed)",
    re.IGNORECASE,
)


def is_fatal_service_issue(message: str) -> bool:
    """判断服务窗口日志条目是否属于明确的产品服务端错误信号。"""
    return bool(_FATAL_SERVICE_RE.search(message))


def is_non_retryable_write_case(case: Case | None) -> bool:
    """判断用例是否属于不可自动重跑的状态修改/写操作用例（基于结构化请求或调用，不误杀注释文本）。"""
    if case is None:
        return False
    if case.mutates_state:
        return True
    frozen = case.frozen_expectation if isinstance(case.frozen_expectation, dict) else {}
    frozen_method = str(frozen.get("method") or "").upper()
    if frozen_method in {"PATCH", "PUT", "DELETE"}:
        return True
    method = (case.verify_method or "").strip()
    if method.startswith("http:"):
        try:
            reqs = json.loads(method[len("http:") :])
        except json.JSONDecodeError:
            reqs = []
        if isinstance(reqs, list):
            for req in reqs:
                if isinstance(req, dict) and str(req.get("method") or "").upper() in {
                    "PATCH",
                    "PUT",
                    "DELETE",
                }:
                    return True
        return False
    if method.startswith("script:") and case.script_content:
        return bool(_SCRIPT_WRITE_CALL_RE.search(case.script_content))
    return False


def _normalize_model_hypotheses(
    case_id: str,
    model_data: dict[str, Any],
    secrets: list[str],
) -> tuple[list[dict[str, Any]], str, list[str], str, str]:
    """将模型返回的假设（或旧测试桩结构）归一为统一假设数组与建议提示，剥离模型终局裁决权。"""
    hypotheses: list[dict[str, Any]] = []
    raw_list = model_data.get("hypotheses")
    legacy_verdict = str(model_data.get("verdict") or "").strip()
    rationale = redact(
        str(model_data.get("rationale") or model_data.get("reason") or "").strip(),
        secrets,
    )
    suggested_fix = redact(str(model_data.get("suggested_fix") or "").strip(), secrets)
    evidence_points = [
        redact(str(p).strip(), secrets)
        for p in (model_data.get("evidence_points") or [])
        if str(p).strip()
    ]

    if isinstance(raw_list, list):
        for entry in raw_list:
            if not isinstance(entry, dict):
                continue
            text = redact(
                str(entry.get("hypothesis") or entry.get("reason") or "").strip(),
                secrets,
            )
            if not text:
                continue
            ev = [
                redact(str(p).strip(), secrets)
                for p in (entry.get("evidence") or entry.get("evidence_points") or [])
                if str(p).strip()
            ]
            cat = str(entry.get("category") or "unknown").strip().lower()
            if cat not in {"product_defect", "script_error", "environment_blocked", "unknown"}:
                cat = "unknown"
            conf = str(entry.get("confidence") or "low").strip().lower()
            if conf not in {"high", "medium", "low"}:
                conf = "low"
            if not ev:
                conf = "low"
            nxt = redact(str(entry.get("next_check") or "").strip(), secrets)
            fix = redact(str(entry.get("suggested_fix") or "").strip(), secrets)
            if fix and not suggested_fix:
                suggested_fix = fix
            for item in ev:
                if item not in evidence_points:
                    evidence_points.append(item)
            hypotheses.append(
                {
                    "case_id": case_id,
                    "hypothesis": text,
                    "category": cat,
                    "evidence": ev,
                    "confidence": conf,
                    "next_check": nxt,
                    "suggested_fix": fix,
                }
            )
    elif rationale:
        cat_map = {
            "confirmed_defect": "product_defect",
            "script_error": "script_error",
            "inconclusive": "environment_blocked",
        }
        hypotheses.append(
            {
                "case_id": case_id,
                "hypothesis": rationale,
                "category": cat_map.get(legacy_verdict, "unknown"),
                "evidence": list(evidence_points),
                "confidence": "medium" if evidence_points else "low",
                "next_check": "",
                "suggested_fix": suggested_fix,
            }
        )

    return hypotheses, rationale, evidence_points, suggested_fix, legacy_verdict


def build_run_hypotheses(case_id: str, judgement: dict[str, Any]) -> list[dict[str, Any]]:
    """从单条用例裁决提取 F07（Run.ai_hypotheses）标准结构条目。"""
    raw = judgement.get("hypotheses")
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        hyp = str(entry.get("hypothesis") or "").strip()
        if not hyp:
            continue
        items.append(
            {
                "case_id": str(entry.get("case_id") or case_id),
                "hypothesis": hyp,
                "evidence": [str(x) for x in (entry.get("evidence") or []) if str(x).strip()],
                "confidence": str(entry.get("confidence") or "low"),
                "next_check": str(entry.get("next_check") or ""),
            }
        )
    return items


_URL_PATH_RE = re.compile(r"https?://[^/\s'\")\]>]+(/[^?\s'\")\]>]*)", re.IGNORECASE)
_REL_API_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])(/api(?:/[A-Za-z0-9_{}$.-]+)+)", re.IGNORECASE)
_NON_404_HTTP_RE = re.compile(
    r"(?:HTTP\s+(?:400|401|403|405|422|500|502|503)|\"code\"\s*:\s*(?:400|401|403|422|500))",
    re.IGNORECASE,
)


def _norm_api_path(raw: str) -> str:
    clean = raw.strip().split("?", 1)[0].rstrip("/").lower()
    if not clean:
        return "/"
    clean = re.sub(r"\$\{[^}]+\}", "{param}", clean)
    clean = re.sub(r"\{[^}]+\}", "{param}", clean)
    return clean


def _case_api_paths(case: Case) -> set[str]:
    texts = [
        case.verify_method or "",
        case.script_content or "",
        str((case.frozen_expectation or {}).get("path") or ""),
    ]
    paths: set[str] = set()
    for text in texts:
        for m in _URL_PATH_RE.findall(text):
            paths.add(_norm_api_path(m))
        for m in _REL_API_PATH_RE.findall(text):
            paths.add(_norm_api_path(m))
        frozen_p = (case.frozen_expectation or {}).get("path")
        if isinstance(frozen_p, str) and frozen_p.startswith("/"):
            paths.add(_norm_api_path(frozen_p))
    return paths


def _matching_probe_findings(
    case: Case,
    probe_results: list[dict[str, Any]] | None,
    *,
    message: str = "",
    outputs: list[bytes] | None = None,
) -> list[str]:
    """查找与当前用例路由/资产精确匹配的确定性硬探针失败项。"""
    if not probe_results:
        return []
    case_paths = _case_api_paths(case)
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
    combined_fail = (
        message
        + "\n"
        + b"\n".join(outputs or []).decode("utf-8", errors="replace")[:2000]
    )
    got_non_404_http = bool(_NON_404_HTTP_RE.search(combined_fail))
    matched: list[str] = []
    for probe in probe_results:
        if not isinstance(probe, dict) or probe.get("status") != "failed":
            continue
        for finding in probe.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            if str(finding.get("severity") or "").lower() == "warning":
                continue
            kind = str(finding.get("kind") or "").lower()
            if kind == "missing_route" and got_non_404_http:
                continue
            target = str(
                finding.get("path") or finding.get("href") or finding.get("url") or ""
            ).strip().lower()
            detail = str(finding.get("detail") or "").strip()
            if not detail or not target:
                continue
            norm_target = _norm_api_path(target)
            segs = [s for s in norm_target.strip("/").split("/") if s]
            if len(segs) < 2 or norm_target in {"/", "/api", "/api/v1", "/api/v2"}:
                continue
            if case_paths:
                if norm_target in case_paths:
                    matched.append(detail)
            else:
                escaped = re.escape(norm_target)
                if re.search(rf"(?<![a-z0-9_/]){escaped}(?![a-z0-9_/])", haystack):
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
    probe_hits = _matching_probe_findings(
        case, probe_results, message=message, outputs=outputs
    )
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
            raw_model = judger(judge_ctx)
            if isinstance(raw_model, dict):
                (
                    hypotheses,
                    rationale,
                    evidence_points,
                    suggested_fix,
                    legacy_verdict,
                ) = _normalize_model_hypotheses(case.case_id, raw_model, secrets)
                # 裁判权责拆分：模型只产出假设，由确定性核心基于事实证据裁定最终 verdict
                primary_cat = hypotheses[0]["category"] if hypotheses else "unknown"
                combined_text = f"{message}\n{tail}"
                model_text = f"{rationale}\n" + "\n".join(
                    str(h.get("hypothesis") or "") for h in hypotheses
                )
                has_script_crash = _is_script_runtime_crash(case, combined_text)
                # 仅以客观执行产物作为环境阻塞依据，绝不拿模型自产文案给模型假设背书。
                has_env_signal = bool(_ENV_BLOCK_RE.search(combined_text))
                is_biased = bool(_BIASED_WORDS_RE.search(model_text))
                has_assertion_fail = bool(_ASSERTION_OR_HTTP_FAIL_RE.search(combined_text))
                is_write_case = is_non_retryable_write_case(case)

                verdict = "suspected_defect"
                overridden = False
                if is_biased:
                    overridden = True
                    if has_assertion_fail:
                        verdict = "confirmed_defect"
                    elif has_script_crash:
                        verdict = "script_error"
                    elif has_env_signal:
                        verdict = "inconclusive"
                    else:
                        verdict = "suspected_defect"
                elif (
                    (primary_cat == "script_error" or legacy_verdict == "script_error")
                    and (has_script_crash or bool(suggested_fix))
                ):
                    verdict = "script_error"
                elif (
                    (primary_cat == "environment_blocked" or legacy_verdict == "inconclusive")
                    and has_env_signal
                    and bool(evidence_points)
                ):
                    verdict = "inconclusive"
                elif (
                    (primary_cat == "product_defect" or legacy_verdict == "confirmed_defect")
                    and (bool(evidence_points) or has_assertion_fail)
                ):
                    verdict = "confirmed_defect"
                elif primary_cat in {"script_error", "environment_blocked"} or (
                    legacy_verdict in {"script_error", "inconclusive"}
                ):
                    overridden = True
                    # 有明确断言失败或写用例真实失败被无据推诿时维持 confirmed_defect，其余落为疑似
                    verdict = (
                        "confirmed_defect"
                        if (has_assertion_fail or is_write_case)
                        else "suspected_defect"
                    )

                final_reason = (
                    (redact(message, secrets) or "确定性规则拦截无依据偏向结论，维持客观疑似待核查")
                    if overridden
                    else (rationale or redact(message, secrets) or "用例执行失败")
                )
                return {
                    "verdict": verdict,
                    "source": (
                        "deterministic" if overridden else "deterministic_with_hypotheses"
                    ),
                    "reason": final_reason,
                    "evidence_points": evidence_points,
                    "suggested_fix": "" if overridden else suggested_fix,
                    "hypotheses": hypotheses,
                    "model_overridden": overridden,
                }
        except AiTestError:
            pass

    combined_fallback = f"{message}\n{tail}"
    if _is_script_runtime_crash(case, combined_fallback):
        return {
            "verdict": "script_error",
            "source": "deterministic",
            "reason": redact(message, secrets) or "检测到脚本或测试请求构造错误",
            "evidence_points": [],
            "suggested_fix": "",
            "hypotheses": [],
            "model_overridden": False,
        }
    if _ENV_BLOCK_RE.search(combined_fallback) and not _ASSERTION_OR_HTTP_FAIL_RE.search(
        combined_fallback
    ):
        return {
            "verdict": "inconclusive",
            "source": "deterministic",
            "reason": redact(message, secrets) or "检测到前置/连接环境阻塞特征",
            "evidence_points": [],
            "suggested_fix": "",
            "hypotheses": [],
            "model_overridden": False,
        }
    return {
        "verdict": "suspected_defect",
        "source": "deterministic",
        "reason": redact(message, secrets) or "用例执行失败，尚缺少确定性归因证据（标记疑似）",
        "evidence_points": [],
        "suggested_fix": "",
        "hypotheses": [],
        "model_overridden": False,
    }


def compute_case_confidence(
    case: Case | None,
    run_case: RunCase | None,
    *,
    effective_result: str,
    probe_results: list[dict[str, Any]] | None = None,
) -> tuple[ConfidenceTier | None, str]:
    """确定性计算单条用例的三档可信度标签（confirmed / suspected / blocked）。

    严格遵守 D4：单次写操作（PATCH/PUT/DELETE）真实失败保持 failed，绝不降级为 blocked；
    可信度仅依据执行阶段、服务日志、确定性探针、脚本错误特征与复现次数决定，不盲信模型结论。
    """
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
    last_attempt = run_case.attempts[-1] if run_case.attempts else None
    last_msg = last_attempt.message if last_attempt is not None else ""
    if last_attempt is not None and last_attempt.stage == "setup":
        return "blocked", last_msg or "前置 setup 阶段失败，属于环境/前置阻塞"
    if any(is_fatal_service_issue(item) for item in run_case.service_log_issues):
        return "confirmed", run_case.service_log_issues[0]
    if case is not None:
        probe_hits = _matching_probe_findings(
            case, probe_results, message=last_msg
        )
        if probe_hits:
            return "confirmed", f"确定性探针交叉印证：{probe_hits[0]}"
    if _is_script_runtime_crash(case, last_msg):
        return "blocked", f"脚本运行异常（语法/导入/类型错误）：{last_msg}"
    failed_attempts = sum(1 for att in run_case.attempts if att.result == "failed")
    if failed_attempts >= 2:
        return "confirmed", f"连续 {failed_attempts} 次复现失败：{last_msg or '结果稳定失败'}"
    if is_non_retryable_write_case(case):
        return "confirmed", last_msg or "不可重跑写操作首次确定性断言失败"
    if (
        isinstance(run_case.judgement, dict)
        and run_case.judgement.get("verdict") == "confirmed_defect"
        and run_case.judgement.get("source") in {"deterministic", "deterministic_with_hypotheses"}
    ):
        reason = str(run_case.judgement.get("reason") or "").strip()
        return "confirmed", reason or "确定性规则结合执行证据确认为产品缺陷"
    return "suspected", last_msg or "单次失败且缺少复现或确定性佐证"


def summarize_confidence(case_details: list[dict[str, Any]]) -> dict[str, int]:
    """汇总报告用例明细中的三档可信度计数（铁证 / 疑似 / 阻塞）。"""
    counts = {"confirmed": 0, "suspected": 0, "blocked": 0}
    for detail in case_details:
        tier = detail.get("confidence_tier")
        if tier in counts:
            counts[tier] += 1
    return counts
