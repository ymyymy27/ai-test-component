"""Source identity verification facts owned by the execution domain.

Source planning belongs to package B. This module only records what package C
actually verified about the materialized execution source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from aitest.domain.execution.runs import AdapterKind, FailureClass, PlanRevisionRef


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
    expected_source_binding_digest: str
    materialized_snapshot_ref: str
    observed_source_digest: str
    state: SourceVerificationState
    observed_entry_ref: str | None = None
    observed_import_ref: str | None = None
    failure_class: FailureClass | None = None
    gap_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    verified_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "verification_id",
            "project_id",
            "expected_source_binding_digest",
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
