"""Saved-report public types, errors and constants shared by service modules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.application.ports import RecordQuery
from aitest.application.review.report_codec import ReportManagementError
from aitest.application.review.reuse_consumption import ReuseOutcome
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.review.reports import DecisionResult

REPORT_AGGREGATE = "report"
REPORT_INTENT_AGGREGATE = "report_intent"
LOCAL_REVIEW_AGGREGATE = "local_review"
REPORT_SCHEMA = "aitest.report/1.0"
LOCAL_REVIEW_SCHEMA = "aitest.local-review/1.0"
REPORT_INTENT_SCHEMA = "aitest.report-intent/1.0"

#: 一期尚无带版本的判定策略来源；与 `DecisionContextFacts` 默认值一致。
DEFAULT_POLICY_VERSION = "1.0"

#: 一期动作表里本模块接管的动作。
SUPPORTED_ACTIONS = ("create_report", "record_local_review")

__all__ = [
    "DEFAULT_POLICY_VERSION",
    "LOCAL_REVIEW_AGGREGATE",
    "LOCAL_REVIEW_SCHEMA",
    "REPORT_AGGREGATE",
    "REPORT_INTENT_AGGREGATE",
    "REPORT_INTENT_SCHEMA",
    "REPORT_SCHEMA",
    "SUPPORTED_ACTIONS",
    "ReportActorReader",
    "ReportActorRequired",
    "ReportClock",
    "ReportGuardRefused",
    "ReportIntentConflict",
    "ReportManagementError",
    "ReportNotSaved",
    "ReportRecordRepository",
    "ReportRevisionConflict",
    "ReportRuntimeFactsReader",
    "ReportUnitOfWork",
]


class ReportRevisionConflict(ReportManagementError):
    code = "RECORD_REVISION_CONFLICT"

    def __init__(self, *, expected: int, current: int) -> None:
        self.expected, self.current = expected, current
        super().__init__(f"report revision conflict: expected {expected}, current {current}")


class ReportNotSaved(ReportManagementError):
    code = "RECORD_NOT_FOUND"


class ReportIntentConflict(ReportManagementError):
    code = "INTENT_CONFLICT"


class ReportGuardRefused(ReportManagementError):
    code = "INVALID_REQUEST"


class ReportActorRequired(ReportManagementError):
    code = "AWAITING_USER_CONFIRMATION"


class ReportUnitOfWork(Protocol):
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


class ReportRecordRepository(Protocol):
    def current_revision(self, *, aggregate_kind: str, record_id: str) -> int: ...
    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> Any: ...
    def query(self, query: RecordQuery) -> Any: ...


class ReportRuntimeFactsReader(Protocol):
    def read_runtime_revision_facts(self, *, project_id: str, run_id: str) -> ExecutionFacts: ...


class ReportActorReader(Protocol):
    def current(self) -> Any: ...


class ReportClock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class _ReuseReading:
    """一次 R 消费的读数：`candidates is None` ＝ 候选来源未接（不是"0 条"）。"""

    outcome: ReuseOutcome | None
    candidates: int | None

    def payload(self) -> dict[str, object]:
        if self.outcome is None:
            return {"candidates": None, "reason": "reuse_candidate_source_not_configured"}
        return {
            "candidates": self.candidates,
            "reused": sorted(self.outcome.reused),
            "references": [item.model_dump(mode="json") for item in self.outcome.references],
            "source_snapshots": [
                {
                    "run_id": item.run_id,
                    "snapshot_ref": SnapshotContentRef.of(item).model_dump(mode="json"),
                }
                for item in self.outcome.source_snapshots
            ],
            "denials": [
                {"case_id": case_id, "reasons": list(reasons)}
                for case_id, reasons in self.outcome.named_reasons
            ],
        }


@dataclass(frozen=True, slots=True)
class _LiveDecision:
    """一次实时判定的全部输入与结果（报告与面板 DTO 共用）。"""

    facts: ExecutionFacts
    prepared: Any
    scope: Any
    issues: tuple[Any, ...]
    reuse: _ReuseReading
    decision: DecisionResult
    source_sequence: int
    blocking_issue_ids: frozenset[str]
