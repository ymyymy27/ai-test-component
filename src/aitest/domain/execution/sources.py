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


@dataclass(frozen=True, slots=True)
class L1CheckOutcome:
    """L1 检查项结论；不通过不升级为真实链路，未知保持未知。"""

    check_type: SourceCheckType
    state: SourceVerificationState
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("reason must not be empty")


def evaluate_l1_checks(
    *,
    build_descriptors: tuple[str, ...] = (),
    entry_compiled: bool | None = None,
    entry_imported: bool | None = None,
) -> tuple[L1CheckOutcome, ...]:
    """Evaluate the two L1 checks the probe facts can actually support.

    ``APPLICABLE_BUILD`` only passes when the materialized source carries a
    recognised build descriptor; otherwise it stays ``UNKNOWN`` instead of
    pretending minimal compilation equals an applicable build.
    ``MINIMAL_START`` passes when the entry was imported, or when a module-free
    entry compiled cleanly; a known failure is ``BLOCKED``, missing facts stay
    ``UNKNOWN``.
    """

    if build_descriptors:
        build = L1CheckOutcome(
            SourceCheckType.APPLICABLE_BUILD,
            SourceVerificationState.VERIFIED,
            "build descriptor present: " + ", ".join(build_descriptors),
        )
    else:
        build = L1CheckOutcome(
            SourceCheckType.APPLICABLE_BUILD,
            SourceVerificationState.UNKNOWN,
            "no build descriptor in materialized source; applicable build not proven",
        )

    if entry_imported is True:
        start = L1CheckOutcome(
            SourceCheckType.MINIMAL_START,
            SourceVerificationState.VERIFIED,
            "entry imported successfully",
        )
    elif entry_imported is False:
        start = L1CheckOutcome(
            SourceCheckType.MINIMAL_START,
            SourceVerificationState.BLOCKED,
            "entry import failed",
        )
    elif entry_compiled is True:
        start = L1CheckOutcome(
            SourceCheckType.MINIMAL_START,
            SourceVerificationState.VERIFIED,
            "module-free entry compiled without starting side effects",
        )
    elif entry_compiled is False:
        start = L1CheckOutcome(
            SourceCheckType.MINIMAL_START,
            SourceVerificationState.BLOCKED,
            "entry did not compile",
        )
    else:
        start = L1CheckOutcome(
            SourceCheckType.MINIMAL_START,
            SourceVerificationState.UNKNOWN,
            "no entry import or compile fact was captured",
        )
    return (build, start)


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
    #: L1 检查项结论（适用构建、最小启动）；缺事实时保持 UNKNOWN。
    l1_checks: tuple[L1CheckOutcome, ...] = ()

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
    "SourceCheckType",
    "SourceFile",
    "SourceSnapshot",
    "SourceVerificationState",
]
