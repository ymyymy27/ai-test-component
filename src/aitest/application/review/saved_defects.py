"""Persisted issue lifecycle on the workspace unit of work (P1-FR15).

Contracts: phase-one architecture 03 (issue transitions, original criteria,
new regression and actual fix version), architecture 04 (finite issue indexes),
and CORE-001 (caller identities do not grant eligibility).

Close eligibility comes from saved current execution and readable evidence,
the effective Case and controlled basis confirmation, the original Attempt's
exact step content, and the shared assertion-outcome producer. A caller outcome
is only a consistency check. The observed source digest must prove fix_version;
an unbound version label or missing original criteria refuses closure.

request_regression saves the original case/run/attempt, fix version, method and
criteria. It does not create an Attempt, authorize execution or close an issue.
The controlled session supplies confirmed_by. Issue review_state remains
UNREVIEWED until independent issue-fact confirmation records exist.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import fields
from typing import Any, Protocol, cast

from aitest.application.execution.run_record import read_run_record
from aitest.application.execution.step_content import StepContent, StepContentReader
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.substrate import RecordReader
from aitest.application.review import defect_management as facade
from aitest.application.review.authoritative_basis import SavedCaseBasisCheck
from aitest.application.review.execution_decision_adapter import (
    assertion_outcomes_from_facts,
    build_decision_facts,
)
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.evidence.evidence import StoredObjectRef
from aitest.domain.execution.runs import Step, StepLevel, StepRevisionRef
from aitest.domain.review.defects import (
    IssueClosureEvidence,
    IssueDisposition,
    IssueRecord,
    IssueSeverity,
    IssueStatus,
    effective_severity,
    resolve_canonical_issue_id,
)
from aitest.domain.review.issue_queries import IssueFilterMask, IssueReviewState
from aitest.domain.review.reports import SourceIdentityState

ISSUE_AGGREGATE = "issue"
ISSUE_INTENT_AGGREGATE = "issue_intent"
ISSUE_SCHEMA = "aitest.issue/1.0"
ISSUE_INTENT_SCHEMA = "aitest.issue-intent/1.0"

#: 一期动作表里本模块接管的动作（`contracts/commands.py` 的 PHASE_ONE_ACTIONS 子集）。
SUPPORTED_ACTIONS = ("update_issue", "record_fix", "close_issue")

_ISSUE_FIELDS = tuple(field.name for field in fields(IssueRecord))


class IssueUnitOfWork(Protocol):
    """问题写入需要的短事务能力（A 侧 `StageableWorkspaceUnitOfWork` 的形状）。"""

    def begin(self, request_id: str, project_id: str, *, intent_id: str | None = None) -> Any: ...
    def next_commit_seq(self) -> str: ...
    def stage_record(
        self,
        *,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> Any: ...
    def commit(self, request_id: str | None = None) -> Any: ...
    def rollback(self, request_id: str | None = None) -> Any: ...


class IssueRecordRepository(Protocol):
    """按准确修订读写问题记录；不提供"读最新"。"""

    def current_revision(self, *, aggregate_kind: str, record_id: str) -> int: ...
    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> Any: ...


class RuntimeFactsReader(Protocol):
    """读取同一提交边界下的运行事实（C 的执行事实装配）。"""

    def read_runtime_revision_facts(self, *, project_id: str, run_id: str) -> ExecutionFacts: ...


class ControlledActorReader(Protocol):
    """受控会话身份来源；确认人只能取它，不能取参数。"""

    def current(self) -> Any: ...


class EvidenceBytesReader(Protocol):
    """内容寻址对象读取：读回时必须自行核对摘要与长度。"""

    def read_bytes(self, ref: StoredObjectRef) -> bytes: ...


CaseBasisCheck = Callable[[str, str, str], bool]


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
    }


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


#: 固定 14 掩码的顺序即 `domain.review.issue_queries.IssueFilterMask` 的声明顺序；
#: 数值不在这里另起定义，而是由枚举顺序派生（并与存储侧的 `_issue_mask` 对照测试锁定）。
_MASK_ORDER: tuple[IssueFilterMask, ...] = tuple(IssueFilterMask)
_FACET_MASKS: Mapping[str, tuple[IssueFilterMask, IssueFilterMask]] = {
    "module": (IssueFilterMask.MODULE, IssueFilterMask.MODULE_SEVERITY),
    "layer": (IssueFilterMask.LAYER, IssueFilterMask.LAYER_SEVERITY),
    "review_state": (IssueFilterMask.REVIEW_STATE, IssueFilterMask.REVIEW_STATE_SEVERITY),
    "workflow_state": (IssueFilterMask.WORKFLOW_STATE, IssueFilterMask.WORKFLOW_STATE_SEVERITY),
    "disposition": (IssueFilterMask.DISPOSITION, IssueFilterMask.DISPOSITION_SEVERITY),
    "blocking": (IssueFilterMask.BLOCKING, IssueFilterMask.BLOCKING_SEVERITY),
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
        "attempt_ids": sorted(issue.attempt_ids),
        "links": {
            "module_ids": list(cast(tuple[str, ...], links["module_ids"])),
            "layers": list(cast(tuple[str, ...], links["layers"])),
            "case_id": links["case_id"],
            "run_id": links["run_id"],
            "attempt_id": links["attempt_id"],
            # 回归关联清单：安排回归写入，**不能被静默丢弃**（否则调用方以为已安排）。
            "regressions": [dict(item) for item in _regression_links(links)],
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


class SavedDefectManagement:
    """问题生命周期的生产用例：权威读取、领域守卫、同一工作单元持久化。

    `update_issue` 负责**已定义**的状态迁移与处置（架构 03 §4 迁移表、§9 处置表）；
    动作名到迁移的映射在三分册里**未定义**，本模块按下面的显式参数固定下来并加测试锁定：

    - 新建：`expected_revision = 0` + `title`（草稿）；
    - `awaiting_confirmation`：草稿 → 待确认（需 `evidence_refs`）；
    - `confirm`：待确认 → 已确认（需 `severity`/`owner`，`confirmed_by` 取受控会话）；
    - `start`：已确认 → 处理中；
    - `disposition`：非缺陷/重复/暂缓/撤销（需 `reason`/`evidence_refs`，可给
      `canonical_issue_id`）。
    """

    def __init__(
        self,
        *,
        unit: IssueUnitOfWork,
        reader: RecordReader,
        records: IssueRecordRepository,
        clock: object,
        actors: ControlledActorReader,
        runtime: RuntimeFactsReader,
        workspace_id: str,
        objects: EvidenceBytesReader | None = None,
        basis_proof: object | None = None,
        case_basis: CaseBasisCheck | None = None,
    ) -> None:
        for name, value in (
            ("unit", unit),
            ("reader", reader),
            ("records", records),
            ("clock", clock),
            ("actors", actors),
            ("runtime", runtime),
        ):
            if value is None:
                raise DefectManagementError(
                    f"issue use cases require the {name} capability", code="CAPABILITY_UNAVAILABLE"
                )
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise DefectManagementError("issue use cases require a workspace identity")
        self._unit, self._reader, self._records = unit, reader, records
        self._clock, self._actors, self._runtime = clock, actors, runtime
        self._workspace_id = workspace_id
        self._objects = objects
        #: 用例依据确认核对的可替换接缝：默认走「已保存准备 + 受控确认链」；
        #: 装配注入默认实现，测试可注入受控替身以隔离外部确认材料。
        self._case_basis = case_basis
        self._bases_check = SavedCaseBasisCheck(
            reader=reader,
            records=records,
            workspace_id=workspace_id,
            basis_proof=basis_proof,
            override=case_basis,
        )

    # ------------------------------------------------------------ 动作入口

    def update_issue(self, command: object) -> Mapping[str, object]:
        return self._apply(command, action="update_issue")

    def record_fix(self, command: object) -> Mapping[str, object]:
        return self._apply(command, action="record_fix")

    def close_issue(self, command: object) -> Mapping[str, object]:
        return self._apply(command, action="close_issue")

    def request_regression(self, command: object) -> Mapping[str, object]:
        """安排回归（P1-FR15）：登记**回归关联**，不执行、不授权、不关闭问题。

        分册依据：功能 §542「登记修复、**安排回归**及核对关闭依据」；架构 03 §66
        「处理中 → 待复测：登记修复版本与回归方式」；§117「回归关联：原证据、事实/假设/
        待验证项、修复版本、新Attempt/证据、原关闭条件」；§149「修复关闭读取指定**新回归
        Attempt** 及可读证据」。因此本动作只把"要重跑什么、按什么标准、原来哪次尝试"写成
        关联，新的实际执行仍必须由受控执行入口产生（本动作不创建 Attempt）。
        """

        return self._apply(command, action="request_regression")

    # ------------------------------------------------------------ 事务骨架

    def _apply(self, command: object, *, action: str) -> Mapping[str, object]:
        project_id = _text(getattr(command, "project_id", None), "project_id")
        intent_id = _text(getattr(command, "intent_id", None), "intent_id")
        request_id = _text(getattr(command, "request_id", None), "request_id")
        expected_revision = getattr(command, "expected_revision", None)
        if type(expected_revision) is not int or expected_revision < 0:
            raise DefectManagementError("expected_revision must be a nonnegative integer")
        parameters = getattr(command, "parameters", None)
        if not isinstance(parameters, Mapping):
            raise DefectManagementError("parameters must be an object")
        issue_id = _text(parameters.get("issue_id"), "issue_id")

        fingerprint = _digest([action, issue_id, expected_revision, dict(parameters)])
        intent_record_id = "issue-intent-" + _digest([project_id, intent_id])[7:]
        recalled = self._recall(project_id, intent_record_id, action, issue_id, fingerprint)
        if recalled is not None:
            return recalled

        current = self._current(project_id, issue_id)
        if current is None:
            if expected_revision != 0:
                raise IssueNotSaved(f"issue {issue_id} is not saved")
            issue = self._create_issue(project_id, issue_id, parameters)
            links = self._initial_links(parameters)
        else:
            if expected_revision != current.revision:
                raise IssueRevisionConflict(expected=expected_revision, current=current.revision)
            issue = issue_from_payload(current.payload)
            if issue.issue_id != issue_id or issue.project_id != project_id:
                raise DefectManagementError("saved issue identity or owner differs")
            links = issue_links(current.payload)
            issue, links = self._transition(action, issue, links, parameters)

        open_state = self._open_state(project_id, issue)
        blocking = self._blocking(project_id, issue)
        sequence = self._next_sequence()
        payload = issue_to_payload(
            issue,
            links=links,
            updated_sequence=sequence,
            open_state=open_state,
            blocking=blocking,
        )
        return self._commit(
            action=action,
            project_id=project_id,
            issue_id=issue_id,
            intent_id=intent_id,
            request_id=request_id,
            intent_record_id=intent_record_id,
            fingerprint=fingerprint,
            issue=issue,
            payload=payload,
            expected_revision=current.revision if current is not None else 0,
        )

    # ------------------------------------------------------------ 安排回归

    def _arrange_regression(
        self,
        issue: IssueRecord,
        links: dict[str, object],
        parameters: Mapping[str, object],
    ) -> tuple[IssueRecord, dict[str, object]]:
        """登记回归关联（架构 03 §117）：只写安排，不执行、不授权、不关闭。"""

        unknown = set(parameters) - {"issue_id", "note"}
        if unknown:
            raise DefectManagementError(
                f"request_regression received unknown parameters: {sorted(unknown)}"
            )
        note = parameters.get("note")
        note_text = None if note is None else _text(note, "note")
        original_attempt_id = _optional_text(links.get("attempt_id"), "attempt_id")
        if original_attempt_id is None:
            raise IssueGuardRefused(
                "arranging a regression requires the original attempt (reproduction entry)"
            )
        case_id = _optional_text(links.get("case_id"), "case_id")
        run_id = _optional_text(links.get("run_id"), "run_id")
        if case_id is None or run_id is None:
            raise IssueGuardRefused(
                "arranging a regression requires the original case and run links"
            )
        request_id = (
            "regression-" + _digest([issue.project_id, issue.issue_id, issue.revision])[7:31]
        )
        try:
            updated = facade.request_regression(
                issue, expected_revision=issue.revision, regression_request_id=request_id
            )
        except DefectManagementError:
            raise
        except ValueError as error:
            raise IssueGuardRefused(f"the issue guard refused this transition: {error}") from error
        existing = links.get("regressions")
        regressions = (
            [dict(item) for item in existing] if isinstance(existing, (list, tuple)) else []
        )
        regressions.append(
            {
                "regression_request_id": request_id,
                "status": "arranged",
                "fix_version": updated.fix_version,
                "regression_method": updated.regression_method,
                "closure_criteria": updated.closure_criteria,
                "original_case_id": case_id,
                "original_run_id": run_id,
                "original_attempt_id": original_attempt_id,
                "note": note_text,
            }
        )
        return updated, {**links, "regressions": regressions}

    # ------------------------------------------------------------ 迁移

    def _create_issue(
        self, project_id: str, issue_id: str, parameters: Mapping[str, object]
    ) -> IssueRecord:
        title = _text(parameters.get("title"), "title")
        return IssueRecord(issue_id=issue_id, project_id=project_id, revision=1, title=title)

    def _initial_links(self, parameters: Mapping[str, object]) -> dict[str, object]:
        raw = parameters.get("links") or {}
        if not isinstance(raw, Mapping):
            raise DefectManagementError("links must be an object")
        return {
            "module_ids": _text_tuple(raw.get("module_ids", ()), "module_ids"),
            "layers": _text_tuple(raw.get("layers", ()), "layers"),
            "case_id": _optional_text(raw.get("case_id"), "case_id"),
            "run_id": _optional_text(raw.get("run_id"), "run_id"),
            "attempt_id": _optional_text(raw.get("attempt_id"), "attempt_id"),
            "regressions": [],
        }

    def _transition(
        self,
        action: str,
        issue: IssueRecord,
        links: dict[str, object],
        parameters: Mapping[str, object],
    ) -> tuple[IssueRecord, dict[str, object]]:
        """调用领域守卫；领域拒绝是**业务拒绝**，必须带结构化错误码上抛。"""

        try:
            if action == "record_fix":
                updated = facade.record_fix(
                    issue,
                    expected_revision=issue.revision,
                    fix_version=_text(parameters.get("fix_version"), "fix_version"),
                    regression_method=_text(
                        parameters.get("regression_method"), "regression_method"
                    ),
                    closure_criteria=_text(parameters.get("closure_criteria"), "closure_criteria"),
                )
                return updated, links
            if action == "close_issue":
                if not all(links.get(name) for name in ("case_id", "run_id", "attempt_id")):
                    raise IssueGuardRefused("original reproduction context cannot be verified")
                if parameters.get("case_id") != links["case_id"]:
                    raise IssueGuardRefused("regression must verify the original case")
                if parameters.get("attempt_id") == links["attempt_id"]:
                    raise IssueGuardRefused("closure requires a new actual regression attempt")
                self._original_criteria(issue, links, parameters)
                evidence = self._closure_evidence(issue, parameters)
                if not evidence.satisfies_original_criteria:
                    return facade.record_failed_regression(
                        issue,
                        expected_revision=issue.revision,
                        attempt_id=evidence.attempt_id,
                        evidence_refs=evidence.evidence_refs,
                    ), links
                updated = facade.close_issue(
                    issue, expected_revision=issue.revision, evidence=evidence
                )
                return updated, links
            if action == "request_regression":
                return self._arrange_regression(issue, links, parameters)
            return self._update_issue(issue, links, parameters)
        except DefectManagementError:
            raise
        except ValueError as error:
            raise IssueGuardRefused(f"the issue guard refused this transition: {error}") from error

    def _update_issue(
        self,
        issue: IssueRecord,
        links: dict[str, object],
        parameters: Mapping[str, object],
    ) -> tuple[IssueRecord, dict[str, object]]:
        keys = set(parameters)
        known = {"issue_id", "title", "evidence_refs", "links"}
        confirm = parameters.get("confirm")
        disposition = parameters.get("disposition")
        selected = [
            name
            for name, present in (
                ("awaiting_confirmation", parameters.get("awaiting_confirmation") is not None),
                ("confirm", confirm is not None),
                ("start", parameters.get("start") is not None),
                ("disposition", disposition is not None),
            )
            if present
        ]
        if len(selected) != 1:
            raise DefectManagementError(
                "update_issue requires exactly one transition: "
                "awaiting_confirmation, confirm, start or disposition"
            )
        choice = selected[0]
        if not keys <= known | {choice}:
            raise DefectManagementError(f"update_issue received unknown parameters: {sorted(keys)}")
        actor = self._actor()
        if choice == "awaiting_confirmation":
            updated = facade.mark_awaiting_confirmation(
                issue,
                expected_revision=issue.revision,
                evidence_refs=_text_tuple(parameters.get("evidence_refs", ()), "evidence_refs"),
            )
        elif choice == "confirm":
            if not isinstance(confirm, Mapping):
                raise DefectManagementError("confirm must be an object")
            updated = facade.confirm_issue(
                issue,
                expected_revision=issue.revision,
                severity=IssueSeverity(_text(confirm.get("severity"), "severity")),
                owner=_text(confirm.get("owner"), "owner"),
                confirmed_by=actor,
            )
        elif choice == "start":
            if parameters.get("start") is not True:
                raise DefectManagementError("start must be true")
            updated = facade.start_issue(issue, expected_revision=issue.revision)
        else:
            if not isinstance(disposition, Mapping):
                raise DefectManagementError("disposition must be an object")
            kind = IssueDisposition(_text(disposition.get("kind"), "disposition.kind"))
            canonical_id = _optional_text(
                disposition.get("canonical_issue_id"), "canonical_issue_id"
            )
            canonical = None
            issues: dict[str, IssueRecord] | None = None
            if kind is IssueDisposition.DUPLICATE:
                if canonical_id is None:
                    raise DefectManagementError("duplicate disposition requires canonical_issue_id")
                canonical, issues = self._issue_graph(issue.project_id, canonical_id)
            updated = facade.update_issue_disposition(
                issue,
                expected_revision=issue.revision,
                disposition=kind,
                reason=_text(disposition.get("reason"), "disposition.reason"),
                evidence_refs=_text_tuple(
                    disposition.get("evidence_refs", ()), "disposition.evidence_refs"
                ),
                confirmed_by=actor,
                canonical_issue=canonical,
                issues=issues,
            )
        return updated, links

    def _issue_graph(
        self, project_id: str, canonical_id: str
    ) -> tuple[IssueRecord, dict[str, IssueRecord]]:
        """读取主问题及其归并链上的当前记录（拒绝悬空引用与跨项目）。"""

        graph: dict[str, IssueRecord] = {}
        canonical: IssueRecord | None = None
        target = canonical_id
        while True:
            saved = self._current(project_id, target)
            if saved is None:
                raise DefectManagementError("canonical issue is missing or belongs elsewhere")
            record = issue_from_payload(saved.payload)
            graph[record.issue_id] = record
            if canonical is None:
                canonical = record
            if record.disposition is not IssueDisposition.DUPLICATE:
                break
            if not record.canonical_issue_id:
                raise DefectManagementError("duplicate canonical issue has no reference")
            target = record.canonical_issue_id
            if target in graph:
                raise DefectManagementError("duplicate issue chain contains a cycle")
        return canonical, graph

    # ------------------------------------------------------------ 权威关闭依据

    def _attempt_case(self, project_id: str, run_id: str, attempt_id: str) -> tuple[Any, str]:
        """Historical criteria come from that Attempt's exact step content."""
        facts = self._facts(project_id, run_id)
        attempt = next((item for item in facts.attempts if item.attempt_id == attempt_id), None)
        if attempt is None:
            raise IssueGuardRefused("original regression attempt cannot be read")
        fact = next((item for item in facts.steps if item.step_id == attempt.step_id), None)
        if fact is None:
            raise IssueGuardRefused("original case step cannot be read")
        reference = attempt.step_revision_ref
        stored = self._records.read(
            aggregate_kind="step_revision",
            record_id=reference.step_revision_id,
            revision=reference.revision_no,
        )
        raw = stored.payload
        if payload_digest(raw) != reference.digest:
            raise IssueGuardRefused("original assertion content digest differs")
        content = StepContent.model_validate_json(json.dumps(raw), strict=True)
        saved_run = self._records.read(aggregate_kind="run", record_id=run_id, revision=1)
        run = read_run_record(saved_run.payload)
        if run.project_id != project_id:
            raise IssueGuardRefused("original regression run belongs to another project")
        step = Step(
            step_id=fact.step_id,
            run_id=run_id,
            ordinal=fact.ordinal,
            case_id=fact.case_id,
            level=StepLevel(content.frozen_step.layer),
            step_revision_ref=StepRevisionRef(**reference.model_dump()),
        )
        checked = StepContentReader(self._records).read(run=run, step=step)  # type: ignore[arg-type]
        return checked.checked_case(), facts.run.rules_revision

    def _original_criteria(
        self,
        issue: IssueRecord,
        links: Mapping[str, object],
        parameters: Mapping[str, object],
    ) -> None:
        try:
            original, rules = self._attempt_case(
                issue.project_id,
                str(links["run_id"]),
                str(links["attempt_id"]),
            )
            current, current_rules = self._attempt_case(
                issue.project_id,
                str(parameters["run_id"]),
                str(parameters["attempt_id"]),
            )
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise IssueGuardRefused("original closure criteria cannot be verified") from error
        if (
            original.case_id != current.case_id
            or original.case_id != links["case_id"]
            or rules != current_rules
            or original.expected != current.expected
            or original.layer != current.layer
            or original.verification_method != current.verification_method
            or original.independent_verification != current.independent_verification
            or original.assertion_basis.text_digest != current.assertion_basis.text_digest
            or original.links.acceptance_item_ids != current.links.acceptance_item_ids
        ):
            raise IssueGuardRefused("new regression differs from the original closure standard")

    def _closure_evidence(
        self, issue: IssueRecord, parameters: Mapping[str, object]
    ) -> IssueClosureEvidence:
        """从已提交事实派生关闭依据；调用方只能给标识与显式断言结果。"""

        allowed = {
            "issue_id",
            "case_id",
            "run_id",
            "attempt_id",
            "assertion_ref",
            "assertion_outcome",
        }
        unknown = set(parameters) - allowed
        if unknown:
            raise DefectManagementError(
                f"close_issue received unknown parameters: {sorted(unknown)}"
            )
        case_id = _text(parameters.get("case_id"), "case_id")
        run_id = _text(parameters.get("run_id"), "run_id")
        attempt_id = _text(parameters.get("attempt_id"), "attempt_id")
        assertion_ref = _text(parameters.get("assertion_ref"), "assertion_ref")
        claimed_outcome = parameters.get("assertion_outcome")
        if claimed_outcome is not None and claimed_outcome not in {"passed", "failed"}:
            raise DefectManagementError("assertion_outcome must be passed or failed")

        facts = self._facts(issue.project_id, run_id)
        attempts = {item.attempt_id: item for item in facts.attempts}
        attempt = attempts.get(attempt_id)
        if attempt is None:
            raise IssueGuardRefused("the specified regression attempt is not in the saved run")
        step = next((item for item in facts.steps if item.step_id == attempt.step_id), None)
        if step is None or step.case_id != case_id:
            raise IssueGuardRefused("the regression attempt does not belong to the given case")
        current = facts.current_attempt_by_step.get(step.step_id, step.current_attempt_id)
        if current != attempt_id or not attempt.is_current:
            raise IssueGuardRefused("closure requires the current attempt of the case step")
        if attempt.state.value != "completed":
            raise IssueGuardRefused("closure requires a completed regression attempt")
        if attempt.capture_completeness.value != "complete":
            raise IssueGuardRefused("closure requires complete captured output")
        if any(
            invalidation.affected_step_id == step.step_id
            or invalidation.affected_attempt_id == attempt_id
            for invalidation in facts.dependency_invalidations
        ):
            raise IssueGuardRefused("closure requires valid upstream dependencies")
        if assertion_ref not in step.assertion_refs:
            raise IssueGuardRefused("the referenced assertion is not part of the saved step")

        decision = build_decision_facts(facts)
        if decision.source_identity_state is not SourceIdentityState.MATCHED:
            raise IssueGuardRefused("closure requires a matched source identity")
        if not self._bases_check.for_facts(facts, case_id):
            raise IssueBasisUnconfirmed("closure requires a confirmed assertion basis for the case")

        evidence_ids = self._attempt_evidence(facts, attempt_id)
        basis_text = self._bases_check.text(facts, case_id)
        if basis_text is None or issue.closure_criteria != basis_text:
            raise IssueGuardRefused(
                "original closure criteria do not match the saved assertion basis"
            )
        # A human tag/annotation in an evidence record is not a measured
        # version binding. Only the verified source observation can close it.
        executed_versions = {
            item.observed_source_digest
            for item in facts.source_verifications
            if not item.gap_ids and item.state.value == "verified"
        }
        if issue.fix_version not in executed_versions:
            raise IssueGuardRefused(
                "the actual regression source does not verify the recorded fix version"
            )
        outcomes = assertion_outcomes_from_facts(
            facts,
            basis_revision_for=lambda identity: self._bases_check.revision(
                issue.project_id,
                facts.run.intent_id,
                identity,
                facts=facts,
            ),
            basis_confirmed_for=lambda identity: self._bases_check.for_facts(facts, identity),
        )
        matching = [
            item
            for item in outcomes
            if item.attempt_id == attempt_id
            and item.assertion_ref == assertion_ref
            and item.case_id == case_id
        ]
        if not matching:
            raise IssueGuardRefused("closure has no authoritative assertion verification")
        derived = build_decision_facts(facts, assertion_outcomes=outcomes)
        satisfies = case_id in derived.coverage.passed
        if claimed_outcome is not None and claimed_outcome != ("passed" if satisfies else "failed"):
            raise IssueGuardRefused("caller assertion outcome differs from saved verification")
        return IssueClosureEvidence(
            case_id=case_id,
            attempt_id=attempt_id,
            evidence_refs=evidence_ids,
            actual_execution=True,
            current_regression_attempt=True,
            evidence_saved=True,
            satisfies_original_criteria=satisfies,
            basis_confirmed=True,
            source_identity_matched=True,
            dependencies_valid=True,
        )

    def _attempt_evidence(self, facts: object, attempt_id: str) -> tuple[str, ...]:
        """该尝试下**已保存且可读**的证据标识；任何一项不可读都拒绝关闭。"""

        items = [
            item
            for item in getattr(facts, "evidence_refs", ())
            if getattr(item, "attempt_id", None) == attempt_id
        ]
        if not items:
            raise IssueGuardRefused("the regression attempt has no saved evidence")
        identities: list[str] = []
        for item in items:
            if (
                item.integrity.value != "complete"
                or item.redaction_state.value == "blocked"
                or item.projection_state.value == "blocked"
                or item.gap_ids
            ):
                raise IssueEvidenceUnreadable("saved evidence is incomplete or blocked")
            revision = self._records.current_revision(
                aggregate_kind="evidence_ref", record_id=item.evidence_id
            )
            if type(revision) is not int or revision < 1:
                raise IssueEvidenceUnreadable("saved evidence reference is missing")
            self._read_object(item)
            identities.append(item.evidence_id)
        return tuple(sorted(set(identities)))

    def _read_object(self, item: object) -> None:
        if self._objects is None:
            return
        digest = getattr(item, "object_digest", None)
        size = getattr(item, "object_size", None)
        project = getattr(item, "project_id", None)
        media = getattr(item, "media_type", None) or "application/octet-stream"
        if not isinstance(digest, str) or type(size) is not int or not isinstance(project, str):
            raise IssueEvidenceUnreadable("saved evidence object identity is unreadable")
        try:
            content = self._objects.read_bytes(
                StoredObjectRef(
                    project,
                    digest,
                    size,
                    media,
                    f"objects/{project}/{digest.removeprefix('sha256:')}",
                )
            )
        except (OSError, ValueError) as error:
            raise IssueEvidenceUnreadable(
                f"saved evidence bytes are unreadable: {error}"
            ) from error
        if type(content) is not bytes or len(content) != size:
            raise IssueEvidenceUnreadable("saved evidence bytes differ from their reference")

    def _basis_confirmed(self, project_id: str, run_intent_id: str, case_id: str) -> bool:
        """用例依据确认：走唯一的受控核对实现（见 `authoritative_basis`）。"""

        return self._bases_check(project_id, run_intent_id, case_id)

    # ------------------------------------------------------------ 读写与提交

    def _actor(self) -> str:
        """受控人工确认来源：绑定的会话身份，**不从参数取**。"""

        try:
            actor = self._actors.current()
        except (LookupError, RuntimeError, ValueError) as error:
            raise DefectManagementError(
                "issue confirmation requires a controlled interactive session",
                code="AWAITING_USER_CONFIRMATION",
            ) from error
        session_id = getattr(actor, "session_id", None)
        entry_kind = getattr(actor, "entry_kind", None)
        if getattr(entry_kind, "value", None) == "agent_relay":
            raise DefectManagementError(
                "issue confirmation cannot come from an AI relay session",
                code="AWAITING_USER_CONFIRMATION",
            )
        if not getattr(actor, "interactive", False) or not isinstance(session_id, str):
            raise DefectManagementError(
                "issue confirmation requires an interactive controlled session",
                code="AWAITING_USER_CONFIRMATION",
            )
        return f"session:{session_id}"

    def _facts(self, project_id: str, run_id: str) -> ExecutionFacts:
        try:
            facts = self._runtime.read_runtime_revision_facts(project_id=project_id, run_id=run_id)
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise IssueGuardRefused(f"saved run facts are unavailable: {error}") from error
        if (getattr(facts, "project_id", None), getattr(facts, "run_id", None)) != (
            project_id,
            run_id,
        ):
            raise IssueGuardRefused("saved run facts belong to another project")
        return facts

    def _current(self, project_id: str, issue_id: str) -> Any | None:
        revision = self._records.current_revision(
            aggregate_kind=ISSUE_AGGREGATE, record_id=issue_id
        )
        if type(revision) is not int or revision < 0:
            raise DefectManagementError("issue warehouse revision cannot be verified")
        if revision == 0:
            return None
        record = self._records.read(
            aggregate_kind=ISSUE_AGGREGATE, record_id=issue_id, revision=revision
        )
        if (
            getattr(record, "aggregate_kind", None),
            getattr(record, "record_id", None),
            getattr(record, "revision", None),
        ) != (ISSUE_AGGREGATE, issue_id, revision) or not isinstance(
            getattr(record, "payload", None), Mapping
        ):
            raise DefectManagementError("saved issue envelope cannot be verified")
        if dict(record.payload).get("project_id") != project_id:
            raise IssueNotSaved("saved issue belongs to another project")
        return record

    def _open_state(self, project_id: str, issue: IssueRecord) -> bool:
        """OPEN 定义见存储合同 §issues.list：active/deferred 与未解决主问题的 duplicate。"""

        if issue.disposition in {IssueDisposition.ACTIVE, IssueDisposition.DEFERRED}:
            return True
        if issue.disposition is IssueDisposition.DUPLICATE:
            if not issue.canonical_issue_id:
                return True
            try:
                graph: dict[str, IssueRecord] = {}
                target = issue.canonical_issue_id
                while True:
                    saved = self._current(project_id, target)
                    if saved is None:
                        return True
                    record = issue_from_payload(saved.payload)
                    graph[record.issue_id] = record
                    if record.disposition is not IssueDisposition.DUPLICATE:
                        break
                    if not record.canonical_issue_id or record.canonical_issue_id in graph:
                        return True
                    target = record.canonical_issue_id
                root = graph[
                    resolve_canonical_issue_id(issue.issue_id, {**graph, issue.issue_id: issue})
                ]
                return root.disposition in {IssueDisposition.ACTIVE, IssueDisposition.DEFERRED}
            except (DefectManagementError, ValueError):
                return True
        return False

    def _blocking(self, project_id: str, issue: IssueRecord) -> str:
        """按最终主问题计算阻塞状态（duplicate 沿主问题，且保留最强已确认级别）。"""

        if issue.disposition is not IssueDisposition.DUPLICATE:
            return blocking_state(issue)
        if not issue.canonical_issue_id:
            return "unknown"
        try:
            root, _graph = self._issue_graph(project_id, issue.canonical_issue_id)
        except DefectManagementError:
            return "unknown"
        return blocking_state(issue, root=root)

    def _next_sequence(self) -> int:
        value = self._unit.next_commit_seq()
        try:
            return int(value)
        except (TypeError, ValueError) as error:
            raise DefectManagementError("commit sequence cannot be verified") from error

    def _recall(
        self,
        project_id: str,
        intent_record_id: str,
        action: str,
        issue_id: str,
        fingerprint: str,
    ) -> Mapping[str, object] | None:
        revision = self._records.current_revision(
            aggregate_kind=ISSUE_INTENT_AGGREGATE, record_id=intent_record_id
        )
        if type(revision) is not int or revision < 0:
            raise DefectManagementError("issue intent revision cannot be verified")
        if revision == 0:
            return None
        record = self._records.read(
            aggregate_kind=ISSUE_INTENT_AGGREGATE, record_id=intent_record_id, revision=revision
        )
        payload = getattr(record, "payload", None)
        if (
            getattr(record, "aggregate_kind", None),
            getattr(record, "record_id", None),
            getattr(record, "revision", None),
        ) != (ISSUE_INTENT_AGGREGATE, intent_record_id, revision) or not isinstance(
            payload, Mapping
        ):
            raise DefectManagementError("saved issue intent cannot be verified")
        if (
            payload.get("schema_version") != ISSUE_INTENT_SCHEMA
            or payload.get("project_id") != project_id
            or payload.get("action") != action
            or payload.get("issue_id") != issue_id
        ):
            raise IssueIntentConflict("issue intent belongs to different inputs")
        if payload.get("input_digest") != fingerprint:
            raise IssueIntentConflict("issue intent has different frozen input")
        result = payload.get("result")
        if not isinstance(result, Mapping) or set(result) != {
            "aggregate_kind",
            "record_id",
            "revision",
        }:
            raise DefectManagementError("saved issue intent result is unreadable")
        return dict(result)

    def _commit(
        self,
        *,
        action: str,
        project_id: str,
        issue_id: str,
        intent_id: str,
        request_id: str,
        intent_record_id: str,
        fingerprint: str,
        issue: IssueRecord,
        payload: dict[str, object],
        expected_revision: int,
    ) -> Mapping[str, object]:
        result = {
            "aggregate_kind": ISSUE_AGGREGATE,
            "record_id": issue.issue_id,
            "revision": issue.revision,
        }
        self._unit.begin(request_id, project_id, intent_id=intent_id)
        try:
            staged = self._unit.stage_record(
                aggregate_kind=ISSUE_AGGREGATE,
                record_id=issue.issue_id,
                expected_revision=expected_revision,
                payload=payload,
            )
            staged_revision = getattr(staged, "revision", staged)
            if type(staged_revision) is not int or staged_revision != issue.revision:
                raise DefectManagementError("staged issue revision cannot be verified")
            self._unit.stage_record(
                aggregate_kind=ISSUE_INTENT_AGGREGATE,
                record_id=intent_record_id,
                expected_revision=0,
                payload={
                    "schema_version": ISSUE_INTENT_SCHEMA,
                    "project_id": project_id,
                    "intent_id": intent_id,
                    "action": action,
                    "issue_id": issue_id,
                    "input_digest": fingerprint,
                    "result": dict(result),
                },
            )
            self._unit.commit(request_id)
        except BaseException:
            self._unit.rollback(request_id)
            raise
        return dict(result)


__all__ = [
    "DefectManagementError",
    "ISSUE_AGGREGATE",
    "ISSUE_INTENT_AGGREGATE",
    "ISSUE_SCHEMA",
    "IssueBasisUnconfirmed",
    "IssueEvidenceUnreadable",
    "IssueGuardRefused",
    "IssueIntentConflict",
    "IssueNotSaved",
    "IssueRevisionConflict",
    "SUPPORTED_ACTIONS",
    "SavedDefectManagement",
    "issue_from_payload",
    "issue_index_entries",
    "issue_links",
    "issue_to_payload",
]
