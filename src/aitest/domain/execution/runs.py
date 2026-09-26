"""Execution domain models.

This module stores execution facts only. It does not calculate business
assertions, report outcomes, or own connectivity retry policy.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_positive(value: int, name: str) -> None:
    if value < 1:
        raise ValueError(f"{name} must be positive")


def _require_non_negative(value: int, name: str) -> None:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


class RunTier(StrEnum):
    FULL = "full"
    QUICK = "quick"
    ON_DEMAND = "on_demand"


class RunControlState(StrEnum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLING = "cancelling"
    RECOVERING = "recovering"
    PENDING_VERIFICATION = "pending_verification"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXECUTION_ERROR = "execution_error"


class StepLevel(StrEnum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class StepState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    PENDING_VERIFICATION = "pending_verification"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"
    EXECUTION_ERROR = "execution_error"


class AttemptState(StrEnum):
    INTENT_RECORDED = "intent_recorded"
    STARTING = "starting"
    RUNNING = "running"
    STOP_REQUESTED = "stop_requested"
    COLLECTING = "collecting"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    PENDING_VERIFICATION = "pending_verification"
    EXECUTION_ERROR = "execution_error"
    INVALIDATED = "invalidated"
    UNKNOWN = "unknown"


class AdapterKind(StrEnum):
    PYTHON_CHECKS = "python_checks"
    COMMAND = "command"
    HTTP = "http"
    VERIFICATION = "verification"
    MANUAL_EVIDENCE = "manual_evidence"
    AGENT = "agent"
    EXTERNAL_RESULT = "external_result"


class SideEffectClass(StrEnum):
    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"
    UNKNOWN = "unknown"


class FailureClass(StrEnum):
    SOURCE_ERROR = "source_error"
    DEPENDENCY_MISSING = "dependency_missing"
    ENVIRONMENT_UNREACHABLE = "environment_unreachable"
    TOOL_FAILURE = "tool_failure"
    PASSED = "passed"


class CaptureCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    GAP = "gap"
    UNKNOWN = "unknown"


class OutputStreamName(StrEnum):
    STDOUT = "stdout"
    STDERR = "stderr"


class ProcessTerminationReason(StrEnum):
    NATURAL_EXIT = "natural_exit"
    TIMEOUT = "timeout"
    CONFIRMED_STOP = "confirmed_stop"
    EXECUTOR_LOST = "executor_lost"
    CAPTURE_FAILURE = "capture_failure"
    UNKNOWN = "unknown"


class TransportErrorClass(StrEnum):
    NETWORK = "network"
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    INPUT_LIMIT = "input_limit"
    RESPONSE_TRUNCATED = "response_truncated"
    SCHEMA_ERROR = "schema_error"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PlanRevisionRef:
    revision_id: str
    revision_no: int
    digest: str

    def __post_init__(self) -> None:
        _require_text(self.revision_id, "revision_id")
        _require_positive(self.revision_no, "revision_no")
        _require_text(self.digest, "digest")


@dataclass(frozen=True, slots=True)
class StepRevisionRef:
    step_revision_id: str
    revision_no: int
    digest: str
    inherited: bool = False
    base_step_revision_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.step_revision_id, "step_revision_id")
        _require_positive(self.revision_no, "revision_no")
        _require_text(self.digest, "digest")
        if self.base_step_revision_id is not None:
            _require_text(self.base_step_revision_id, "base_step_revision_id")


@dataclass(frozen=True, slots=True)
class InputRef:
    input_ref_id: str
    value_ref: str
    value_digest: str
    resolved: bool = False
    source_ref: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.input_ref_id, "input_ref_id")
        _require_text(self.value_ref, "value_ref")
        _require_text(self.value_digest, "value_digest")


@dataclass(frozen=True, slots=True)
class RegisteredEntryRef:
    entry_id: str
    adapter_kind: AdapterKind
    entrypoint: str
    arguments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.entry_id, "entry_id")
        _require_text(self.entrypoint, "entrypoint")


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    requirement_id: str
    evidence_kind: str
    required_for_assertion: bool = True
    critical: bool = False
    projection_requirement: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.requirement_id, "requirement_id")
        _require_text(self.evidence_kind, "evidence_kind")


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    upstream_step_id: str
    downstream_step_id: str
    required: bool = True
    condition_ref: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.upstream_step_id, "upstream_step_id")
        _require_text(self.downstream_step_id, "downstream_step_id")


@dataclass(frozen=True, slots=True)
class ConsumedOutput:
    upstream_attempt_id: str
    output_object_digest: str
    value_ref: str

    def __post_init__(self) -> None:
        _require_text(self.upstream_attempt_id, "upstream_attempt_id")
        _require_text(self.output_object_digest, "output_object_digest")
        _require_text(self.value_ref, "value_ref")


@dataclass(frozen=True, slots=True)
class ConsumedCondition:
    upstream_attempt_id: str
    condition_fact_ref: str
    condition_digest: str

    def __post_init__(self) -> None:
        _require_text(self.upstream_attempt_id, "upstream_attempt_id")
        _require_text(self.condition_fact_ref, "condition_fact_ref")
        _require_text(self.condition_digest, "condition_digest")


@dataclass(frozen=True, slots=True)
class AuthorizationRef:
    authorization_id: str
    intent_id: str
    step_id: str
    resolved_input_digest: str
    target_ref: str
    credential_scope_ref: str
    plan_revision_ref: PlanRevisionRef
    consumed_by_attempt_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "authorization_id",
            "intent_id",
            "step_id",
            "resolved_input_digest",
            "target_ref",
            "credential_scope_ref",
        ):
            _require_text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    project_id: str
    run_id: str
    step_id: str
    attempt_id: str
    intent_id: str
    resolved_input_digest: str
    registered_entry: RegisteredEntryRef
    materialized_snapshot_ref: str
    environment_ref: str
    source_binding_digest: str
    authorization_ref: AuthorizationRef
    side_effect_class: SideEffectClass
    timeout_ms: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "run_id",
            "step_id",
            "attempt_id",
            "intent_id",
            "resolved_input_digest",
            "materialized_snapshot_ref",
            "environment_ref",
            "source_binding_digest",
        ):
            _require_text(getattr(self, name), name)
        if self.timeout_ms is not None:
            _require_positive(self.timeout_ms, "timeout_ms")


@dataclass(frozen=True, slots=True)
class ExecutionHandle:
    handle_id: str
    adapter_kind: AdapterKind
    adapter_version: str
    real_execution_id: str
    process_start_identity: str
    workdir_ref: str

    def __post_init__(self) -> None:
        for name in (
            "handle_id",
            "adapter_version",
            "real_execution_id",
            "process_start_identity",
            "workdir_ref",
        ):
            _require_text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class OutputCursor:
    attempt_id: str
    stream_name: OutputStreamName
    offset: int
    last_block_index: int
    last_committed_digest: str
    durable: bool = False

    def __post_init__(self) -> None:
        _require_text(self.attempt_id, "attempt_id")
        _require_non_negative(self.offset, "offset")
        _require_non_negative(self.last_block_index, "last_block_index")
        _require_text(self.last_committed_digest, "last_committed_digest")


@dataclass(frozen=True, slots=True)
class OutputBlockRef:
    block_id: str
    attempt_id: str
    stream_name: OutputStreamName
    block_index: int
    offset: int
    length: int
    digest: str
    complete: bool
    capture_source: str
    redaction_summary_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.block_id, "block_id")
        _require_text(self.attempt_id, "attempt_id")
        _require_non_negative(self.block_index, "block_index")
        _require_non_negative(self.offset, "offset")
        _require_non_negative(self.length, "length")
        _require_text(self.digest, "digest")
        _require_text(self.capture_source, "capture_source")


@dataclass(frozen=True, slots=True)
class CapturedOutputBlock:
    run_id: str
    step_id: str
    attempt_id: str
    stream_name: OutputStreamName
    block_index: int
    offset: int
    content: bytes
    complete: bool = True
    capture_source: str = "adapter"
    redaction_summary_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "step_id", "attempt_id", "capture_source"):
            _require_text(getattr(self, name), name)
        _require_non_negative(self.block_index, "block_index")
        _require_non_negative(self.offset, "offset")

    @property
    def length(self) -> int:
        return len(self.content)

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True, slots=True)
class SpoolManifest:
    attempt_id: str
    run_id: str
    step_id: str
    blocks: tuple[OutputBlockRef, ...] = ()
    cursors: tuple[OutputCursor, ...] = ()
    schema_version: str = "aitest.spool/1.0"

    def __post_init__(self) -> None:
        for name in ("attempt_id", "run_id", "step_id", "schema_version"):
            _require_text(getattr(self, name), name)
        keys = [(block.stream_name, block.block_index) for block in self.blocks]
        if len(keys) != len(set(keys)):
            raise ValueError("spool blocks must be unique by stream and block_index")
        if any(block.attempt_id != self.attempt_id for block in self.blocks):
            raise ValueError("spool block attempt_id must match manifest")
        stream_names = [cursor.stream_name for cursor in self.cursors]
        if len(stream_names) != len(set(stream_names)):
            raise ValueError("spool cursors must be unique by stream")
        if any(cursor.attempt_id != self.attempt_id for cursor in self.cursors):
            raise ValueError("spool cursor attempt_id must match manifest")


@dataclass(frozen=True, slots=True)
class ExitFact:
    attempt_id: str
    startup_token: str
    process_start_identity: str
    real_exit_code: int | None
    last_block_index_by_stream: tuple[tuple[OutputStreamName, int], ...] = ()
    saved_bytes_by_stream: tuple[tuple[OutputStreamName, int], ...] = ()
    capture_completeness: CaptureCompleteness = CaptureCompleteness.UNKNOWN
    termination_reason: ProcessTerminationReason = ProcessTerminationReason.UNKNOWN
    timed_out: bool = False
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.attempt_id, "attempt_id")
        _require_text(self.startup_token, "startup_token")
        _require_text(self.process_start_identity, "process_start_identity")


@dataclass(frozen=True, slots=True)
class RecoveryCheckpoint:
    run_id: str
    step_id: str
    attempt_id: str
    last_committed_stage: str
    output_cursors: tuple[OutputCursor, ...] = ()
    output_block_refs: tuple[OutputBlockRef, ...] = ()
    resolved_input_digest: str = ""
    side_effect_class: SideEffectClass = SideEffectClass.UNKNOWN
    execution_handle_ref: ExecutionHandle | None = None
    checkpoint_revision: int = 1

    def __post_init__(self) -> None:
        for name in ("run_id", "step_id", "attempt_id", "last_committed_stage"):
            _require_text(getattr(self, name), name)
        _require_positive(self.checkpoint_revision, "checkpoint_revision")


@dataclass(frozen=True, slots=True)
class StructuredExecutionError:
    error_id: str
    error_class: TransportErrorClass
    error_code: str
    message_code: str
    safe_message: str
    retry_candidate: bool = False
    retry_eligibility_source: str | None = None
    provider_request_id: str | None = None
    adapter_kind: AdapterKind | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("error_id", "error_code", "message_code", "safe_message"):
            _require_text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class Run:
    run_id: str
    project_id: str
    origin_workspace_id: str
    intent_id: str
    tier: RunTier
    driver: str
    conclusion_ceiling: str
    plan_revision_ref: PlanRevisionRef
    environment_ref: str
    environment_isolated: bool
    rules_revision: str
    control_state: RunControlState = RunControlState.NOT_STARTED
    evidence_level: str | None = None
    primary_gap_ids: tuple[str, ...] = ()
    coverage_summary: str | None = None
    runtime_revision_refs: tuple[str, ...] = ()
    required_scope: frozenset[str] = frozenset()
    selected_scope: frozenset[str] = frozenset()
    frozen_input_refs: tuple[InputRef, ...] = ()
    source_binding_digest: str = ""
    result_ref: str | None = None
    revision: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "project_id",
            "origin_workspace_id",
            "intent_id",
            "driver",
            "conclusion_ceiling",
            "environment_ref",
            "rules_revision",
        ):
            _require_text(getattr(self, name), name)
        _require_non_negative(self.revision, "revision")


@dataclass(frozen=True, slots=True)
class Step:
    step_id: str
    run_id: str
    ordinal: int
    case_id: str
    level: StepLevel
    step_revision_ref: StepRevisionRef
    required_for_case: bool = True
    state: StepState = StepState.PENDING
    input_refs: tuple[InputRef, ...] = ()
    dependency_edges: tuple[DependencyEdge, ...] = ()
    registered_entry_ref: RegisteredEntryRef | None = None
    assertion_refs: tuple[str, ...] = ()
    evidence_requirements: tuple[EvidenceRequirement, ...] = ()
    execution_source_requirement: str | None = None
    current_attempt_id: str | None = None
    invalidated_by: str | None = None

    def __post_init__(self) -> None:
        for name in ("step_id", "run_id", "case_id"):
            _require_text(getattr(self, name), name)
        _require_positive(self.ordinal, "ordinal")


@dataclass(frozen=True, slots=True)
class Attempt:
    attempt_id: str
    run_id: str
    step_id: str
    attempt_index: int
    resolved_input_digest: str
    step_revision_ref: StepRevisionRef
    source_binding_digest: str
    side_effect_class: SideEffectClass
    adapter_kind: AdapterKind
    adapter_version: str
    state: AttemptState = AttemptState.INTENT_RECORDED
    intent_id: str = ""
    intent_digest: str = ""
    consumed_outputs: tuple[ConsumedOutput, ...] = ()
    consumed_conditions: tuple[ConsumedCondition, ...] = ()
    authorization_ref: AuthorizationRef | None = None
    business_idempotency_key_ref: str | None = None
    transport_retry_count: int = 0
    timeout_ms: int | None = None
    timed_out: bool = False
    execution_handle_ref: ExecutionHandle | None = None
    output_cursors: tuple[OutputCursor, ...] = ()
    output_block_refs: tuple[OutputBlockRef, ...] = ()
    structured_result_ref: str | None = None
    exit_fact_ref: ExitFact | None = None
    capture_completeness: CaptureCompleteness = CaptureCompleteness.UNKNOWN
    error_ref: StructuredExecutionError | None = None
    unknown_reason_ref: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        for name in (
            "attempt_id",
            "run_id",
            "step_id",
            "resolved_input_digest",
            "source_binding_digest",
            "adapter_version",
        ):
            _require_text(getattr(self, name), name)
        _require_positive(self.attempt_index, "attempt_index")
        _require_non_negative(self.transport_retry_count, "transport_retry_count")
        _require_non_negative(self.revision, "revision")
        if self.timeout_ms is not None:
            _require_positive(self.timeout_ms, "timeout_ms")

    @property
    def output_cursor_ref(self) -> OutputCursor | None:
        return self.output_cursors[0] if self.output_cursors else None

    @property
    def retry_count(self) -> int:
        return self.attempt_index - 1


class ExecutionInspectionState(StrEnum):
    RUNNING = "running"
    EXITED = "exited"
    STOPPED = "stopped"
    LOST = "lost"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ExecutionInspectionResult:
    handle_id: str
    state: ExecutionInspectionState
    process_reachable: bool
    identity_matches: bool
    stop_confirmed: bool = False
    observed_at: datetime | None = None
    unknown_reason: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.handle_id, "handle_id")


@dataclass(frozen=True, slots=True)
class ExecutionCollectionResult:
    attempt_id: str
    output_blocks: tuple[OutputBlockRef, ...] = ()
    captured_blocks: tuple[CapturedOutputBlock, ...] = ()
    output_cursors: tuple[OutputCursor, ...] = ()
    exit_fact_ref: ExitFact | None = None
    structured_result_ref: str | None = None
    capture_completeness: CaptureCompleteness = CaptureCompleteness.UNKNOWN
    error_ref: StructuredExecutionError | None = None
    complete: bool = False

    def __post_init__(self) -> None:
        _require_text(self.attempt_id, "attempt_id")

    @property
    def output_cursor_ref(self) -> OutputCursor | None:
        return self.output_cursors[0] if self.output_cursors else None


@dataclass(frozen=True, slots=True)
class StopRequestResult:
    handle_id: str
    stop_confirmed: bool
    observed_state: ExecutionInspectionState
    unknown_reason: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.handle_id, "handle_id")


# Compatibility name for skeleton consumers. Prefer RunControlState.
ExecutionStatus = RunControlState


__all__ = [
    "AdapterKind",
    "Attempt",
    "AttemptState",
    "AuthorizationRef",
    "CaptureCompleteness",
    "CapturedOutputBlock",
    "ConsumedCondition",
    "ConsumedOutput",
    "DependencyEdge",
    "EvidenceRequirement",
    "ExecutionCollectionResult",
    "ExecutionHandle",
    "ExecutionInspectionResult",
    "ExecutionInspectionState",
    "ExecutionRequest",
    "ExecutionStatus",
    "ExitFact",
    "FailureClass",
    "InputRef",
    "OutputStreamName",
    "OutputBlockRef",
    "OutputCursor",
    "PlanRevisionRef",
    "ProcessTerminationReason",
    "RecoveryCheckpoint",
    "RegisteredEntryRef",
    "Run",
    "RunControlState",
    "RunTier",
    "SideEffectClass",
    "Step",
    "StepLevel",
    "StepRevisionRef",
    "SpoolManifest",
    "StepState",
    "StopRequestResult",
    "StructuredExecutionError",
    "TransportErrorClass",
]
