"""Source identity verification facts owned by the execution domain.

Source planning belongs to package B. This module only records what package C
actually verified about the materialized execution source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from aitest.domain.evidence.objects import StoredObjectRef
from aitest.domain.execution.runs import AdapterKind, FailureClass, PlanRevisionRef
from aitest.domain.execution.runtime_source import RuntimeSourcePlan


class SourceBindingKind(StrEnum):
    GIT = "git"
    PLAIN = "plain"


class SourceVerificationState(StrEnum):
    VERIFIED = "verified"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class SourceCheckType(StrEnum):
    SYNTAX = "syntax"
    APPLICABLE_BUILD = "applicable_build"
    LOAD = "load"
    MINIMAL_START = "minimal_start"


class SourceCheckStatusState(StrEnum):
    """单个源码检查子项的实际结论；`not_applicable` 必须带原因（DEC-021）。"""

    VERIFIED = "verified"
    NOT_APPLICABLE = "not_applicable"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SourceCheckStatus:
    """一次 L1 子检查的可持久化结论，不把"未执行"折叠成"通过"。"""

    check_type: SourceCheckType
    state: SourceCheckStatusState
    reason: str | None = None
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if self.state is SourceCheckStatusState.NOT_APPLICABLE and not self.reason:
            raise ValueError("a not_applicable source check requires a reason")


@dataclass(frozen=True, slots=True)
class SourceCommandRequest:
    """一条显式登记、由受控命令执行器运行的源码检查（DEC-021）。

    只传 argv 与判据；解释器替换、cwd 约束、环境白名单、进程树回收由基础设施
    适配器执行。应用层不拼接 shell 字符串。
    """

    check_type: SourceCheckType
    check_id: str
    project_id: str
    command: tuple[str, ...]
    workdir: str
    interpreter: str
    timeout_ms: int
    descriptor: str | None = None
    ready_when: str | None = None

    def __post_init__(self) -> None:
        for name in ("check_id", "project_id", "workdir", "interpreter"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if not self.command:
            raise ValueError("a source command check requires an argv command")
        if self.timeout_ms < 1:
            raise ValueError("timeout_ms must be positive")


@dataclass(frozen=True, slots=True)
class SourceCommandOutcome:
    """一条源码检查的实际结论；未执行/未就绪不得写成通过。"""

    state: SourceCheckStatusState
    failure_class: FailureClass
    reason: str | None = None
    evidence_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SourceFile:
    relative_path: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        if not self.relative_path.strip():
            raise ValueError("relative_path must not be empty")
        if self.size < 0:
            raise ValueError("size must be non-negative")
        if not self.sha256.strip():
            raise ValueError("sha256 must not be empty")


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    snapshot_id: str
    project_id: str
    purpose: str
    binding_revision: int
    files: tuple[SourceFile, ...]
    content_identity: str

    def __post_init__(self) -> None:
        if not self.snapshot_id.strip() or not self.project_id.strip():
            raise ValueError("source snapshot requires identity")
        if not self.purpose.strip() or not self.content_identity.strip():
            raise ValueError("source snapshot requires purpose and content identity")
        if self.binding_revision < 0:
            raise ValueError("binding_revision must be non-negative")


@dataclass(frozen=True, slots=True)
class ExecutionSourceVerification:
    verification_id: str
    project_id: str
    plan_revision_ref: PlanRevisionRef
    expected_source_content_digest: str
    materialized_snapshot_ref: str
    observed_source_digest: str
    state: SourceVerificationState
    observed_entry_ref: str | None = None
    observed_import_ref: str | None = None
    observed_interpreter_ref: str | None = None
    failure_class: FailureClass | None = None
    gap_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    verified_at: datetime | None = None
    raw_output_object: StoredObjectRef | None = None
    source_instance_id: str | None = None
    capture_plan: RuntimeSourcePlan | None = None
    checks: tuple[SourceCheckStatus, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "verification_id",
            "project_id",
            "expected_source_content_digest",
            "materialized_snapshot_ref",
            "observed_source_digest",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")


@dataclass(frozen=True, slots=True)
class SourceCheckResult:
    check_result_id: str
    attempt_id: str
    check_type: SourceCheckType
    scope: str
    source_snapshot_ref: str
    environment_ref: str
    rules_revision: str
    adapter_version: str
    failure_class: FailureClass
    adapter_kind: AdapterKind = AdapterKind.PYTHON_CHECKS
    entry_ref: str | None = None
    argument_refs: tuple[str, ...] = ()
    raw_output_evidence_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "check_result_id",
            "attempt_id",
            "scope",
            "source_snapshot_ref",
            "environment_ref",
            "rules_revision",
            "adapter_version",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")


__all__ = [
    "ExecutionSourceVerification",
    "SourceBindingKind",
    "SourceCheckResult",
    "SourceCheckStatus",
    "SourceCheckStatusState",
    "SourceCheckType",
    "SourceCommandOutcome",
    "SourceCommandRequest",
    "SourceFile",
    "SourceSnapshot",
    "SourceVerificationState",
]
