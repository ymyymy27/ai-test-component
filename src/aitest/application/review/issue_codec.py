"""问题记录的 payload 编解码与索引投影（从 saved_defects.py 拆出）。

只做形状、摘要与 mask 投影，不做状态迁移；服务层 re-export 公开函数。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields
from typing import Any, cast

from aitest.application.evidence.pytest_failures import (
    PYTEST_ISSUE_DRAFT_VERSION,
)
from aitest.domain.review.defects import (
    IssueDisposition,
    IssueRecord,
    IssueSeverity,
    IssueStatus,
    effective_severity,
)
from aitest.domain.review.issue_queries import IssueFilterMask, IssueReviewState

_MASK_ORDER: tuple[IssueFilterMask, ...] = tuple(IssueFilterMask)
_FACET_MASKS: Mapping[str, tuple[IssueFilterMask, IssueFilterMask]] = {
    "module": (IssueFilterMask.MODULE, IssueFilterMask.MODULE_SEVERITY),
    "layer": (IssueFilterMask.LAYER, IssueFilterMask.LAYER_SEVERITY),
    "review_state": (IssueFilterMask.REVIEW_STATE, IssueFilterMask.REVIEW_STATE_SEVERITY),
    "workflow_state": (IssueFilterMask.WORKFLOW_STATE, IssueFilterMask.WORKFLOW_STATE_SEVERITY),
    "disposition": (IssueFilterMask.DISPOSITION, IssueFilterMask.DISPOSITION_SEVERITY),
    "blocking": (IssueFilterMask.BLOCKING, IssueFilterMask.BLOCKING_SEVERITY),
}

ISSUE_AGGREGATE = "issue"
ISSUE_INTENT_AGGREGATE = "issue_intent"
ISSUE_SCHEMA = "aitest.issue/1.0"
ISSUE_INTENT_SCHEMA = "aitest.issue-intent/1.0"

#: 一期动作表里本模块接管的动作（`contracts/commands.py` 的 PHASE_ONE_ACTIONS 子集）。
SUPPORTED_ACTIONS = ("update_issue", "record_fix", "close_issue")

_ISSUE_FIELDS = tuple(field.name for field in fields(IssueRecord))



class DefectManagementError(ValueError):
    """D 侧问题用例错误；`code` 取自 `contracts/errors.py` 的既有错误码集合。

    入口层用 `getattr(exc, "code", "INTERNAL_ERROR")` 取码，因此这里必须带 `code`。
    未新造协议词汇：只复用已登记的错误码。
    """

    code = "INVALID_REQUEST"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        if code is not None:
            self.code = code
        super().__init__(message)

class IssueRevisionConflict(DefectManagementError):
    code = "RECORD_REVISION_CONFLICT"

    def __init__(self, *, expected: int, current: int) -> None:
        self.expected, self.current = expected, current
        super().__init__(f"issue revision conflict: expected {expected}, current {current}")

class IssueNotSaved(DefectManagementError):
    code = "RECORD_NOT_FOUND"

class IssueIntentConflict(DefectManagementError):
    code = "INTENT_CONFLICT"

class IssueGuardRefused(DefectManagementError):
    """权威事实不支持该迁移（不是参数格式问题，也不是修订冲突）。"""

    code = "INVALID_REQUEST"

class IssueEvidenceUnreadable(DefectManagementError):
    code = "INTEGRITY_CHECK_FAILED"

class IssueBasisUnconfirmed(DefectManagementError):
    code = "AWAITING_USER_CONFIRMATION"

def _digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()

def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DefectManagementError(f"{name} must be nonempty text")
    return value

def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _text(value, name)

def _text_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise DefectManagementError(f"{name} must be a list of strings")
    items = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise DefectManagementError(f"{name} must be a list of nonempty strings")
    if len(set(items)) != len(items):
        raise DefectManagementError(f"{name} must not repeat values")
    return items

def _revision_mapping(
    value: object, evidence_refs: tuple[str, ...], name: str
) -> dict[str, int]:
    """Parse the optional evidence-id → revision map; old records return {}."""

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DefectManagementError(f"{name} must be an object")
    revisions: dict[str, int] = {}
    for identity, revision in value.items():
        if not isinstance(identity, str) or not identity.strip():
            raise DefectManagementError(f"{name} keys must be nonempty strings")
        if identity not in evidence_refs:
            raise DefectManagementError(f"{name} references undeclared evidence")
        if type(revision) is not int or revision < 1:
            raise DefectManagementError(f"{name} values must be positive integers")
        revisions[identity] = revision
    return revisions

def issue_links(payload: Mapping[str, object]) -> dict[str, object]:
    """问题记录的最小关联（模块/层级/用例/运行/尝试/回归关联）；未知显式表示。"""

    raw = payload.get("links")
    if raw is None:
        return {
            "module_ids": (),
            "layers": (),
            "case_id": None,
            "run_id": None,
            "attempt_id": None,
            "regressions": [],
            "pytest_cluster": None,
            "split_from": None,
        }
    if not isinstance(raw, Mapping):
        raise DefectManagementError("saved issue links are unreadable")
    return {
        "module_ids": _text_tuple(raw.get("module_ids", ()), "module_ids"),
        "layers": _text_tuple(raw.get("layers", ()), "layers"),
        "case_id": _optional_text(raw.get("case_id"), "case_id"),
        "run_id": _optional_text(raw.get("run_id"), "run_id"),
        "attempt_id": _optional_text(raw.get("attempt_id"), "attempt_id"),
        # 回归关联原样读回：字段形状由 `_arrange_regression` 写死，读侧不补造。
        "regressions": _regression_links(raw),
        # pytest 失败簇是服务端从已保存 JUnit 重算后写入的冻结投影；旧记录没有此键。
        "pytest_cluster": _pytest_cluster_links(raw),
        "split_from": _optional_text(raw.get("split_from"), "split_from"),
    }

def _pytest_cluster_links(raw: Mapping[str, object]) -> dict[str, object] | None:
    """读回已冻结的 pytest 失败簇；形状不符即拒绝，不静默丢细节。"""

    value = raw.get("pytest_cluster")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DefectManagementError("saved pytest cluster must be an object")
    if value.get("schema_version") != PYTEST_ISSUE_DRAFT_VERSION:
        raise DefectManagementError("saved pytest cluster schema cannot be verified")
    failures = value.get("failures")
    if not isinstance(failures, (list, tuple)) or not failures:
        raise DefectManagementError("saved pytest cluster must list its failures")
    if any(not isinstance(item, Mapping) for item in failures):
        raise DefectManagementError("saved pytest cluster failure entry must be an object")
    return {str(key): item for key, item in value.items()}

def _payload_cluster(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DefectManagementError("pytest cluster links must be an object")
    return {str(key): item for key, item in value.items()}

def _regression_links(raw: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    """读回归关联清单；形状不符即拒绝（不静默丢掉安排结果）。"""

    entries = raw.get("regressions")
    if entries is None:
        return ()
    # 读侧拿到的是元组（`issue_links` 的形状），写侧拿到的是列表：两者都必须接受。
    if not isinstance(entries, (list, tuple)):
        raise DefectManagementError("saved issue regressions must be a list")
    parsed: list[dict[str, object]] = []
    for item in entries:
        if not isinstance(item, Mapping):
            raise DefectManagementError("saved issue regression entry must be an object")
        unknown = set(item) - {
            "regression_request_id",
            "status",
            "fix_version",
            "regression_method",
            "closure_criteria",
            "original_case_id",
            "original_run_id",
            "original_attempt_id",
            "note",
        }
        if unknown:
            raise DefectManagementError(
                f"saved issue regression entry has unknown fields: {sorted(unknown)}"
            )
        request_id = _text(item.get("regression_request_id"), "regression_request_id")
        if _text(item.get("status"), "status") != "arranged":
            raise DefectManagementError("saved issue regression status cannot be verified")
        parsed.append(
            {
                "regression_request_id": request_id,
                "status": "arranged",
                "fix_version": _text(item.get("fix_version"), "fix_version"),
                "regression_method": _text(item.get("regression_method"), "regression_method"),
                "closure_criteria": _text(item.get("closure_criteria"), "closure_criteria"),
                "original_case_id": _text(item.get("original_case_id"), "original_case_id"),
                "original_run_id": _text(item.get("original_run_id"), "original_run_id"),
                "original_attempt_id": _text(
                    item.get("original_attempt_id"), "original_attempt_id"
                ),
                "note": _optional_text(item.get("note"), "note"),
            }
        )
    return tuple(parsed)

def issue_from_payload(payload: Mapping[str, object]) -> IssueRecord:
    """从已保存 payload 还原问题记录；字段集合不符即拒绝，不猜缺失字段。"""

    if not isinstance(payload, Mapping) or payload.get("schema_version") != ISSUE_SCHEMA:
        raise DefectManagementError("saved issue schema cannot be verified")
    values: dict[str, Any] = {}
    for name in _ISSUE_FIELDS:
        values[name] = payload.get(name)
    values["status"] = IssueStatus(_text(payload.get("status"), "status"))
    severity = payload.get("severity")
    values["severity"] = None if severity is None else IssueSeverity(_text(severity, "severity"))
    values["disposition"] = IssueDisposition(_text(payload.get("disposition"), "disposition"))
    values["evidence_refs"] = _text_tuple(payload.get("evidence_refs", ()), "evidence_refs")
    values["evidence_revisions"] = _revision_mapping(
        payload.get("evidence_revisions"), values["evidence_refs"], "evidence_revisions"
    )
    values["attempt_ids"] = frozenset(_text_tuple(payload.get("attempt_ids", ()), "attempt_ids"))
    values["revision"] = payload.get("revision")
    for name in ("owner", "confirmed_by", "disposition_reason", "canonical_issue_id"):
        values[name] = _optional_text(payload.get(name), name)
    for name in ("fix_version", "regression_method", "closure_criteria"):
        values[name] = _optional_text(payload.get(name), name)
    try:
        return IssueRecord(**values)
    except (TypeError, ValueError) as error:
        raise DefectManagementError(f"saved issue record is unreadable: {error}") from error

def _facet_values(
    issue: IssueRecord, links: Mapping[str, object], blocking: str
) -> dict[str, tuple[str, ...]]:
    """问题在固定 facet 上的取值；未知用 `unknown` 显式分支，不用空串通配。"""

    modules = tuple(cast(tuple[str, ...], links["module_ids"])) or ("unknown",)
    layers = tuple(cast(tuple[str, ...], links["layers"])) or ("unknown",)
    return {
        "module": modules,
        "layer": layers,
        "review_state": (IssueReviewState.UNREVIEWED.value,),
        "workflow_state": (issue.status.value,),
        "disposition": (issue.disposition.value,),
        "blocking": (blocking,),
    }

def _mask_number(mask: IssueFilterMask) -> int:
    return _MASK_ORDER.index(mask)

def issue_index_entries(
    issue: IssueRecord,
    *,
    links: Mapping[str, object],
    open_state: bool,
    blocking: str,
) -> list[dict[str, object]]:
    """构造 `issues.list` 需要的全部索引入口（固定 28 种入口形态 × 实际取值）。

    每个入口形如 `{"view": "OPEN"|"ALL", "mask": 0..13, "facet_value"?, "severity"?}`，
    与 `infrastructure/file_store/index.py` 的 `_issue_physical_keys` / `_plan`
    逐字对应：查询前缀是 `(project, view, mask, facet_value|ALL, severity|ALL)`。
    """

    facets = _facet_values(issue, links, blocking)
    severities: tuple[str | None, ...] = (
        (IssueSeverity.P0.value,) if issue.severity is None else (issue.severity.value,)
    )
    if issue.severity is None:
        severities = ("unknown",)
    entries: list[dict[str, object]] = []
    views = ("ALL", "OPEN") if open_state else ("ALL",)
    for view in views:
        for severity in (None, *severities):
            if severity is None:
                entries.append({"view": view, "mask": _mask_number(IssueFilterMask.NONE)})
            else:
                entries.append(
                    {
                        "view": view,
                        "mask": _mask_number(IssueFilterMask.SEVERITY),
                        "severity": severity,
                    }
                )
            for facet, values in facets.items():
                plain, with_severity = _FACET_MASKS[facet]
                for value in values:
                    entry: dict[str, object] = {
                        "view": view,
                        "mask": _mask_number(with_severity if severity is not None else plain),
                        "facet_value": value,
                    }
                    if severity is not None:
                        entry["severity"] = severity
                    entries.append(entry)
    # 同一 (view, mask, facet_value, severity) 只保留一条：索引键即身份。
    unique: dict[tuple[object, ...], dict[str, object]] = {}
    for entry in entries:
        unique[(entry["view"], entry["mask"], entry.get("facet_value"), entry.get("severity"))] = (
            entry
        )
    return [unique[key] for key in sorted(unique, key=lambda item: tuple(str(x) for x in item))]

def issue_to_payload(
    issue: IssueRecord,
    *,
    links: Mapping[str, object],
    updated_sequence: int,
    open_state: bool,
    blocking: str,
) -> dict[str, object]:
    """不可变问题记录 payload；同时给出 `issues.list` 的最小摘要字段与索引入口。"""

    if type(updated_sequence) is not int or updated_sequence < 0:
        raise DefectManagementError("updated_sequence must be a nonnegative integer")
    entries = issue_index_entries(issue, links=links, open_state=open_state, blocking=blocking)
    payload: dict[str, object] = {
        "schema_version": ISSUE_SCHEMA,
        "issue_id": issue.issue_id,
        "project_id": issue.project_id,
        "revision": issue.revision,
        "title": issue.title,
        "status": issue.status.value,
        "severity": issue.severity.value if issue.severity is not None else None,
        "disposition": issue.disposition.value,
        "owner": issue.owner,
        "confirmed_by": issue.confirmed_by,
        "disposition_reason": issue.disposition_reason,
        "canonical_issue_id": issue.canonical_issue_id,
        "fix_version": issue.fix_version,
        "regression_method": issue.regression_method,
        "closure_criteria": issue.closure_criteria,
        # 最近一次"安排回归"的身份（架构 03 §117 回归关联）；未安排时为 None。
        "regression_request_id": issue.regression_request_id,
        # 证据引用是**引用集合**：调用方重复声明同一证据时必须合并，而不是写出
        # 一份读不回来的记录（读了会被 `issue_from_payload` 判为重复值而整体拒绝）。
        "evidence_refs": list(dict.fromkeys(issue.evidence_refs)),
        "evidence_revisions": dict(issue.evidence_revisions),
        "attempt_ids": sorted(issue.attempt_ids),
        "links": {
            "module_ids": list(cast(tuple[str, ...], links["module_ids"])),
            "layers": list(cast(tuple[str, ...], links["layers"])),
            "case_id": links["case_id"],
            "run_id": links["run_id"],
            "attempt_id": links["attempt_id"],
            # 回归关联清单：安排回归写入，**不能被静默丢弃**（否则调用方以为已安排）。
            "regressions": [dict(item) for item in _regression_links(links)],
            # pytest 失败簇只由服务端从保存的 JUnit 重算后固化；旧记录为 None。
            "pytest_cluster": _payload_cluster(links.get("pytest_cluster")),
            "split_from": links.get("split_from"),
        },
        # 以下键是存储侧最小摘要白名单（`index.py::_SUMMARY_*`）使用的事实。
        "updated_sequence": updated_sequence,
        "workflow_state": issue.status.value,
        "review_state": IssueReviewState.UNREVIEWED.value,
        "blocking": blocking,
        "layer": (tuple(cast(tuple[str, ...], links["layers"])) or ("unknown",))[0],
        "module_ids": list(cast(tuple[str, ...], links["module_ids"])) or ["unknown"],
        "issue_open": open_state,
        "issue_index_entries": entries,
    }
    return payload

def blocking_state(
    issue: IssueRecord,
    *,
    root: IssueRecord | None = None,
    issues: Mapping[str, IssueRecord] | None = None,
) -> str:
    """单条问题的阻塞状态（P0/P1 且仍未解决）；归并沿最终主问题并保留最强级别。

    处置表见功能文档 §12.2 / 架构 03 §9：active/deferred 的 P0/P1 阻塞；
    duplicate 沿最终主问题计一次阻塞且不能降低已确认级别；non_defect/fixed 不阻塞。
    """

    subject = root if root is not None else issue
    if subject.disposition in {
        IssueDisposition.NON_DEFECT,
        IssueDisposition.FIXED,
    }:
        return "non_blocking"
    if subject.disposition is IssueDisposition.DUPLICATE:
        return "unknown"
    severity = (
        issue.severity
        if root is None
        else effective_severity(
            issue.issue_id, issues or {issue.issue_id: issue, root.issue_id: root}
        )
    )
    if severity is None:
        return "unknown"
    if severity in {IssueSeverity.P0, IssueSeverity.P1}:
        return "blocking"
    return "non_blocking"
