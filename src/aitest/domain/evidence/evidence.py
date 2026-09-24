"""Evidence domain models.

Evidence provenance, integrity, trace links, mocks, and independent
verification facts are stored here. Business assertion outcomes are not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from aitest.domain.execution.sources import SourceBindingKind


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_non_negative(value: int, name: str) -> None:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


class EvidenceKind(StrEnum):
    SOURCE_CHECK = "source_check"
    COMMAND_OUTPUT = "command_output"
    HTTP_RESPONSE = "http_response"
    MODEL_RESPONSE = "model_response"
    MANUAL_STEP = "manual_step"
    ATTACHMENT = "attachment"
    TRACE_NODE = "trace_node"
    VERIFICATION = "verification"
    EXTERNAL_IMPORT = "external_import"


class EvidenceCaptureSource(StrEnum):
    PLUGIN_RUNTIME = "plugin_runtime"
    MANUAL = "manual"
    AGENT_RUNTIME = "agent_runtime"
    EXTERNAL_IMPORT = "external_import"
    SYSTEM_RECOVERY = "system_recovery"


class EvidenceIntegrity(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING_TAIL = "missing_tail"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class RedactionState(StrEnum):
    REDACTED = "redacted"
    NOT_REQUIRED = "not_required"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class ProjectionState(StrEnum):
    INTERNAL = "internal"
    DISPLAYABLE = "displayable"
    EXPORTABLE = "exportable"
    MODEL_OUTBOUND = "model_outbound"
    BLOCKED = "blocked"


class TraceNodeType(StrEnum):
    REQUEST = "request"
    SPAN = "span"
    BUSINESS_OPERATION = "business_operation"
    EXTERNAL_CALL = "external_call"
    MANUAL_STEP = "manual_step"
    UNKNOWN = "unknown"


class TraceNodeState(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class TruthClass(StrEnum):
    REAL = "real"
    MOCK = "mock"
    MIXED = "mixed"
    UNKNOWN = "unknown"


Authenticity = TruthClass


class MockDeclarationSource(StrEnum):
    DEVELOPMENT_DECLARATION = "development_declaration"
    RUNTIME_CONFIG = "runtime_config"
    STATIC_CANDIDATE = "static_candidate"


class MockVerificationState(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class VerificationObservation(StrEnum):
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    NO_RESULT = "no_result"
    QUERY_ERROR = "query_error"
    DEADLINE_REACHED = "deadline_reached"
    UNKNOWN = "unknown"


class EvidenceGapKind(StrEnum):
    MISSING_REQUEST_ID = "missing_request_id"
    MISSING_STRUCTURED_LOG = "missing_structured_log"
    SOURCE_UNVERIFIED = "source_unverified"
    OUTPUT_GAP = "output_gap"
    REDACTION_UNAVAILABLE = "redaction_unavailable"
    UNSUPPORTED_IMPORT = "unsupported_import"
    MOCK_UNVERIFIED = "mock_unverified"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"


class EvidenceLevel(StrEnum):
    FULL_LINK = "full_link"
    MANUAL_ASSOCIATED = "manual_associated"
    INSUFFICIENT = "insufficient"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CodeIdentity:
    binding_kind: SourceBindingKind
    workspace_ref: str
    commit_id: str | None = None
    file_manifest_digest: str | None = None
    revision_ref: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.workspace_ref, "workspace_ref")
        if self.binding_kind is SourceBindingKind.GIT and not self.commit_id:
            raise ValueError("git code identity requires commit_id")
        if self.binding_kind is SourceBindingKind.PLAIN and not self.file_manifest_digest:
            raise ValueError("plain code identity requires file_manifest_digest")


@dataclass(frozen=True, slots=True)
class RedactionSummary:
    policy_version: str
    applied_rule_categories: tuple[str, ...] = ()
    filtered_streams: tuple[str, ...] = ()
    filtered_ranges: tuple[str, ...] = ()
    replacement_count: int = 0
    completeness: str = "unknown"
    gap_reasons: tuple[str, ...] = ()
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.policy_version, "policy_version")
        _require_text(self.completeness, "completeness")
        _require_non_negative(self.replacement_count, "replacement_count")


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    project_id: str
    run_id: str
    step_id: str
    attempt_id: str
    evidence_kind: EvidenceKind
    capture_source: EvidenceCaptureSource
    object_digest: str
    object_size: int
    code_identity: CodeIdentity
    integrity: EvidenceIntegrity = EvidenceIntegrity.UNKNOWN
    redaction_state: RedactionState = RedactionState.UNKNOWN
    projection_state: ProjectionState = ProjectionState.INTERNAL
    redaction_summary_ref: str | None = None
    evidence_level: EvidenceLevel = EvidenceLevel.UNKNOWN
    gap_ids: tuple[str, ...] = ()
    media_type: str | None = None
    evidence_revision: int = 1
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "evidence_id",
            "project_id",
            "run_id",
            "step_id",
            "attempt_id",
            "object_digest",
        ):
            _require_text(getattr(self, name), name)
        _require_non_negative(self.object_size, "object_size")
        if self.object_size == 0:
            raise ValueError("object_size must be positive")
        if self.evidence_revision < 1:
            raise ValueError("evidence_revision must be positive")


@dataclass(frozen=True, slots=True)
class TraceNode:
    trace_node_id: str
    run_id: str
    actual_node_id: str
    node_type: TraceNodeType
    state: TraceNodeState
    attempt_id: str | None = None
    actual_parent_node_id: str | None = None
    explicit_relation_ref: str | None = None
    duration_ms: int | None = None
    assertion_refs: tuple[str, ...] = ()
    truth_class: TruthClass = TruthClass.UNKNOWN
    gap_ids: tuple[str, ...] = ()
    navigation_path: str | None = None
    code_symbol_ref: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.trace_node_id, "trace_node_id")
        _require_text(self.run_id, "run_id")
        _require_text(self.actual_node_id, "actual_node_id")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")


@dataclass(frozen=True, slots=True)
class MockDeclaration:
    mock_declaration_id: str
    content_revision: int
    project_id: str
    idempotency_key: str
    declaration_source: MockDeclarationSource
    replaced_object_ref: str
    replaced_behavior_ref: str
    scope: str
    verification_state: MockVerificationState = MockVerificationState.UNVERIFIED
    verification_conclusion_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "mock_declaration_id",
            "project_id",
            "idempotency_key",
            "replaced_object_ref",
            "replaced_behavior_ref",
            "scope",
        ):
            _require_text(getattr(self, name), name)
        if self.content_revision < 1:
            raise ValueError("content_revision must be positive")


@dataclass(frozen=True, slots=True)
class Verification:
    verification_id: str
    verification_of: str
    business_object_id: str
    query_method: str
    observation: VerificationObservation
    query_interval: str | None = None
    deadline_condition: str | None = None
    target_deployment_ref: str | None = None
    actual_result_ref: str | None = None
    key_trace_link_refs: tuple[str, ...] = ()
    covers_critical_chain_item_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    gap_ids: tuple[str, ...] = ()
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "verification_id",
            "verification_of",
            "business_object_id",
            "query_method",
        ):
            _require_text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class EvidenceGap:
    gap_id: str
    kind: EvidenceGapKind
    subject_ref: str
    reason_code: str
    safe_reason: str
    requires_user_action: bool = False
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("gap_id", "subject_ref", "reason_code", "safe_reason"):
            _require_text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class ExternalImportRef:
    import_id: str
    external_schema: str
    source_instance_id: str
    source_record_id: str
    content_digest: str
    idempotency_state: str
    attachment_refs: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "import_id",
            "external_schema",
            "source_instance_id",
            "source_record_id",
            "content_digest",
            "idempotency_state",
        ):
            _require_text(getattr(self, name), name)


__all__ = [
    "Authenticity",
    "CodeIdentity",
    "EvidenceCaptureSource",
    "EvidenceGap",
    "EvidenceGapKind",
    "EvidenceIntegrity",
    "EvidenceKind",
    "EvidenceLevel",
    "EvidenceRef",
    "ExternalImportRef",
    "MockDeclaration",
    "MockDeclarationSource",
    "MockVerificationState",
    "ProjectionState",
    "RedactionState",
    "RedactionSummary",
    "TraceNode",
    "TraceNodeState",
    "TraceNodeType",
    "TruthClass",
    "Verification",
    "VerificationObservation",
]
