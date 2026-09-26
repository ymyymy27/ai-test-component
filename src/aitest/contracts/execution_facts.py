"""Frozen cross-package schema for package C execution facts.

ExecutionFacts is a single-Run, one-commit consistent snapshot consumed by D
and stored by A. It contains facts, references, and unknown reasons only; it
does not contain final case or business outcomes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aitest.contracts.prepared_run import (
    ConclusionCeilingFact,
    EnvironmentIsolationModeFact,
    RunDriverFact,
    RunTierFact,
)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunControlStateFact(StrEnum):
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


class StepLevelFact(StrEnum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class StepStateFact(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    PENDING_VERIFICATION = "pending_verification"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"
    EXECUTION_ERROR = "execution_error"


class AttemptStateFact(StrEnum):
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


class AdapterKindFact(StrEnum):
    PYTHON_CHECKS = "python_checks"
    COMMAND = "command"
    HTTP = "http"
    VERIFICATION = "verification"
    MANUAL_EVIDENCE = "manual_evidence"
    AGENT = "agent"
    EXTERNAL_RESULT = "external_result"


class SideEffectClassFact(StrEnum):
    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"
    UNKNOWN = "unknown"


class CaptureCompletenessFact(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    GAP = "gap"
    UNKNOWN = "unknown"


class ProcessTerminationReasonFact(StrEnum):
    NATURAL_EXIT = "natural_exit"
    CONFIRMED_STOP = "confirmed_stop"
    EXECUTOR_LOST = "executor_lost"
    CAPTURE_FAILURE = "capture_failure"
    UNKNOWN = "unknown"


class EvidenceKindFact(StrEnum):
    SOURCE_CHECK = "source_check"
    COMMAND_OUTPUT = "command_output"
    HTTP_RESPONSE = "http_response"
    MODEL_RESPONSE = "model_response"
    MANUAL_STEP = "manual_step"
    ATTACHMENT = "attachment"
    TRACE_NODE = "trace_node"
    VERIFICATION = "verification"
    EXTERNAL_IMPORT = "external_import"


class EvidenceCaptureSourceFact(StrEnum):
    PLUGIN_RUNTIME = "plugin_runtime"
    MANUAL = "manual"
    AGENT_RUNTIME = "agent_runtime"
    EXTERNAL_IMPORT = "external_import"
    SYSTEM_RECOVERY = "system_recovery"


class EvidenceIntegrityFact(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING_TAIL = "missing_tail"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class RedactionStateFact(StrEnum):
    REDACTED = "redacted"
    NOT_REQUIRED = "not_required"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class ProjectionStateFact(StrEnum):
    INTERNAL = "internal"
    DISPLAYABLE = "displayable"
    EXPORTABLE = "exportable"
    MODEL_OUTBOUND = "model_outbound"
    BLOCKED = "blocked"


class EvidenceLevelFact(StrEnum):
    FULL_LINK = "full_link"
    MANUAL_ASSOCIATED = "manual_associated"
    INSUFFICIENT = "insufficient"
    UNKNOWN = "unknown"


class SourceBindingKindFact(StrEnum):
    GIT = "git"
    PLAIN = "plain"


class SourceVerificationStateFact(StrEnum):
    VERIFIED = "verified"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class MockDeclarationSourceFact(StrEnum):
    DEVELOPMENT_DECLARATION = "development_declaration"
    RUNTIME_CONFIG = "runtime_config"
    STATIC_CANDIDATE = "static_candidate"


class MockVerificationStateFact(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class VerificationObservationFact(StrEnum):
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    NO_RESULT = "no_result"
    QUERY_ERROR = "query_error"
    DEADLINE_REACHED = "deadline_reached"
    UNKNOWN = "unknown"


class FactCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class PlanRevisionRefFact(ContractModel):
    revision_id: str
    revision_no: int = Field(ge=1)
    digest: str


class StepRevisionRefFact(ContractModel):
    step_revision_id: str
    revision_no: int = Field(ge=1)
    digest: str
    inherited: bool = False
    base_step_revision_id: str | None = None


class ConsumedOutputFact(ContractModel):
    upstream_attempt_id: str
    output_object_digest: str
    value_ref: str


class ConsumedConditionFact(ContractModel):
    upstream_attempt_id: str
    condition_fact_ref: str
    condition_digest: str


class ExecutionHandleFact(ContractModel):
    handle_id: str
    adapter_kind: AdapterKindFact
    adapter_version: str
    real_execution_id: str
    process_start_identity: str
    workdir_ref: str


class OutputCursorFact(ContractModel):
    attempt_id: str
    stream_name: str
    offset: int = Field(ge=0)
    last_block_index: int = Field(ge=0)
    last_committed_digest: str
    durable: bool = False


class RedactionSummaryFact(ContractModel):
    policy_version: str
    applied_rule_categories: tuple[str, ...] = Field(default_factory=tuple)
    filtered_streams: tuple[str, ...] = Field(default_factory=tuple)
    filtered_ranges: tuple[str, ...] = Field(default_factory=tuple)
    replacement_count: int = Field(default=0, ge=0)
    completeness: str = "unknown"
    gap_reasons: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime | None = None


class OutputBlockFact(ContractModel):
    block_id: str
    attempt_id: str
    stream_name: str
    block_index: int = Field(ge=0)
    offset: int = Field(ge=0)
    length: int = Field(ge=0)
    digest: str
    complete: bool
    capture_source: str
    redaction_summary: RedactionSummaryFact | None = None


class ExitFactDTO(ContractModel):
    attempt_id: str
    startup_token: str
    process_start_identity: str
    real_exit_code: int | None = None
    last_block_index_by_stream: dict[str, int] = Field(default_factory=dict)
    saved_bytes_by_stream: dict[str, int] = Field(default_factory=dict)
    capture_completeness: CaptureCompletenessFact
    termination_reason: ProcessTerminationReasonFact
    published_at: datetime | None = None


class RunFact(ContractModel):
    run_id: str
    run_revision: int = Field(ge=0)
    origin_workspace_id: str
    intent_id: str = Field(min_length=1)
    tier: RunTierFact
    driver: RunDriverFact
    conclusion_ceiling: ConclusionCeilingFact
    plan_revision: PlanRevisionRefFact
    environment_ref: str
    environment_isolation_mode: EnvironmentIsolationModeFact
    rules_revision: str
    control_state: RunControlStateFact
    evidence_level: EvidenceLevelFact | None = None
    primary_gap_ids: tuple[str, ...] = Field(default_factory=tuple)
    coverage_summary: str | None = None
    runtime_revision_refs: tuple[str, ...] = Field(default_factory=tuple)
    required_scope: tuple[str, ...] = Field(
        default_factory=tuple,
        description="PreparedRun.frozen_required_case_ids, the frozen mandatory scope M",
    )
    selected_scope: tuple[str, ...] = Field(
        default_factory=tuple,
        description="PreparedRun.selected_case_ids, the selected scope S for this run",
    )
    source_binding_digest: str | None = None
    result_ref: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class StepFact(ContractModel):
    step_id: str
    run_id: str
    step_revision: int = Field(ge=0)
    step_revision_ref: StepRevisionRefFact
    ordinal: int = Field(ge=1)
    case_id: str
    required_for_case: bool = True
    level: StepLevelFact
    state: StepStateFact
    dependency_step_ids: tuple[str, ...] = Field(default_factory=tuple)
    registered_entry_ref: str | None = None
    assertion_refs: tuple[str, ...] = Field(default_factory=tuple)
    evidence_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    current_attempt_id: str | None = None
    invalidated: bool = False
    invalidated_by: str | None = None
    gap_ids: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_revision(self) -> StepFact:
        if self.step_revision != self.step_revision_ref.revision_no:
            raise ValueError("step_revision must match step_revision_ref.revision_no")
        return self


class AttemptFact(ContractModel):
    attempt_id: str
    run_id: str
    step_id: str
    attempt_revision: int = Field(ge=0)
    attempt_index: int = Field(ge=1)
    retry_count: int = Field(ge=0)
    state: AttemptStateFact
    is_current: bool
    intent_id: str | None = None
    intent_digest: str | None = None
    resolved_input_digest: str
    step_revision_ref: StepRevisionRefFact
    source_binding_digest: str
    consumed_outputs: tuple[ConsumedOutputFact, ...] = Field(default_factory=tuple)
    consumed_conditions: tuple[ConsumedConditionFact, ...] = Field(default_factory=tuple)
    side_effect_class: SideEffectClassFact
    business_idempotency_key_ref: str | None = None
    transport_retry_count: int = Field(default=0, ge=0)
    authorization_ref: str | None = None
    adapter_kind: AdapterKindFact
    adapter_version: str
    timeout_ms: int | None = Field(default=None, ge=1)
    timed_out: bool = False
    handle: ExecutionHandleFact | None = None
    output_cursor: OutputCursorFact | None = None
    output_blocks: tuple[OutputBlockFact, ...] = Field(default_factory=tuple)
    structured_result_ref: str | None = None
    exit_fact: ExitFactDTO | None = None
    capture_completeness: CaptureCompletenessFact
    error_ref: str | None = None
    unknown_reason_ids: tuple[str, ...] = Field(default_factory=tuple)
    started_at: datetime | None = None
    ended_at: datetime | None = None

    @model_validator(mode="after")
    def validate_attempt_numbers(self) -> AttemptFact:
        if self.retry_count != self.attempt_index - 1:
            raise ValueError("retry_count must equal attempt_index - 1")
        return self


class CodeIdentityFact(ContractModel):
    binding_kind: SourceBindingKindFact
    workspace_ref: str
    commit_id: str | None = None
    file_manifest_digest: str | None = None
    revision_ref: str | None = None


class EvidenceFact(ContractModel):
    evidence_id: str
    evidence_revision: int = Field(ge=1)
    project_id: str
    source_instance_id: str
    run_id: str
    step_id: str
    attempt_id: str
    evidence_kind: EvidenceKindFact
    capture_source: EvidenceCaptureSourceFact
    code_identity: CodeIdentityFact
    object_digest: str
    object_size: int = Field(ge=1)
    media_type: str | None = None
    integrity: EvidenceIntegrityFact
    redaction_state: RedactionStateFact
    projection_state: ProjectionStateFact
    redaction_summary: RedactionSummaryFact | None = None
    evidence_level: EvidenceLevelFact
    gap_ids: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime | None = None


class SourceCheckFact(ContractModel):
    check_result_id: str
    attempt_id: str
    check_type: str
    scope: str
    source_snapshot_ref: str
    environment_ref: str
    rules_revision: str
    adapter_version: str
    failure_class: str
    raw_output_evidence_ref: str | None = None
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)


class SourceVerificationFact(ContractModel):
    verification_id: str
    project_id: str
    plan_revision: PlanRevisionRefFact
    expected_source_binding_digest: str
    materialized_snapshot_ref: str
    observed_source_digest: str
    state: SourceVerificationStateFact
    observed_entry_ref: str | None = None
    observed_import_ref: str | None = None
    failure_class: str | None = None
    gap_ids: tuple[str, ...] = Field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)
    verified_at: datetime | None = None


class VerificationFact(ContractModel):
    verification_id: str
    verification_of: str
    business_object_id: str
    query_method: str
    observation: VerificationObservationFact
    query_interval: str | None = None
    deadline_condition: str | None = None
    target_deployment_ref: str | None = None
    actual_result_ref: str | None = None
    key_trace_link_refs: tuple[str, ...] = Field(default_factory=tuple)
    covers_critical_chain_item_ids: tuple[str, ...] = Field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)
    gap_ids: tuple[str, ...] = Field(default_factory=tuple)
    verified_at: datetime | None = None


class MockDeclarationFact(ContractModel):
    mock_declaration_id: str
    content_revision: int = Field(ge=1)
    project_id: str
    idempotency_key: str
    declaration_source: MockDeclarationSourceFact
    replaced_object_ref: str
    replaced_behavior_ref: str
    scope: str
    verification_state: MockVerificationStateFact
    verification_conclusion_ref: str | None = None
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime | None = None


class DependencyInvalidationFact(ContractModel):
    invalidation_id: str
    run_id: str
    affected_step_id: str
    affected_attempt_id: str | None = None
    upstream_attempt_id: str | None = None
    reason: str
    source_revision_ref: str
    transitive: bool
    invalidated_at: datetime | None = None
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)


class UnknownReasonFact(ContractModel):
    unknown_reason_id: str
    subject_type: str
    subject_id: str
    reason_code: str
    safe_reason: str
    requires_verification: bool = True
    suggested_next_action: str | None = None
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime | None = None


class EvidenceGapFact(ContractModel):
    gap_id: str
    kind: str
    subject_ref: str
    reason_code: str
    safe_reason: str
    critical: bool
    requires_user_action: bool = False
    created_at: datetime | None = None


class CoverageSummary(ContractModel):
    mandatory_case_ids: tuple[str, ...] = Field(default_factory=tuple)
    selected_case_ids: tuple[str, ...] = Field(default_factory=tuple)
    executed_attempt_ids: tuple[str, ...] = Field(default_factory=tuple)
    blocked_step_ids: tuple[str, ...] = Field(default_factory=tuple)
    invalidated_step_ids: tuple[str, ...] = Field(default_factory=tuple)
    unknown_step_ids: tuple[str, ...] = Field(default_factory=tuple)
    evidence_gap_count: int = Field(default=0, ge=0)
    critical_gap_count: int = Field(default=0, ge=0)


class ExecutionFacts(ContractModel):
    schema_version: Literal["aitest.execution-facts/1.0"] = "aitest.execution-facts/1.0"
    facts_id: str
    project_id: str
    run_id: str
    snapshot_commit_id: str
    snapshot_cursor: int = Field(ge=0)
    snapshot_revision: int = Field(ge=1)
    committed_at: datetime
    run_revision: int = Field(ge=0)
    plan_revision: PlanRevisionRefFact
    runtime_revision_refs: tuple[str, ...] = Field(default_factory=tuple)
    run: RunFact
    steps: tuple[StepFact, ...]
    attempts: tuple[AttemptFact, ...]
    current_attempt_by_step: dict[str, str | None] = Field(default_factory=dict)
    evidence_refs: tuple[EvidenceFact, ...] = Field(default_factory=tuple)
    source_check_results: tuple[SourceCheckFact, ...] = Field(default_factory=tuple)
    source_verifications: tuple[SourceVerificationFact, ...] = Field(default_factory=tuple)
    verifications: tuple[VerificationFact, ...] = Field(default_factory=tuple)
    mock_declarations: tuple[MockDeclarationFact, ...] = Field(default_factory=tuple)
    dependency_invalidations: tuple[DependencyInvalidationFact, ...] = Field(default_factory=tuple)
    unknowns: tuple[UnknownReasonFact, ...] = Field(default_factory=tuple)
    gaps: tuple[EvidenceGapFact, ...] = Field(default_factory=tuple)
    coverage: CoverageSummary = Field(default_factory=CoverageSummary)
    completeness: FactCompleteness = FactCompleteness.COMPLETE

    @model_validator(mode="after")
    def validate_plan_revision(self) -> Self:
        if self.plan_revision != self.run.plan_revision:
            raise ValueError("top-level plan_revision must match run.plan_revision")
        return self


__all__ = [
    "AdapterKindFact",
    "AttemptFact",
    "AttemptStateFact",
    "CaptureCompletenessFact",
    "CodeIdentityFact",
    "ConsumedConditionFact",
    "ConsumedOutputFact",
    "ContractModel",
    "CoverageSummary",
    "DependencyInvalidationFact",
    "EvidenceCaptureSourceFact",
    "EvidenceFact",
    "EvidenceGapFact",
    "EvidenceIntegrityFact",
    "EvidenceKindFact",
    "EvidenceLevelFact",
    "ExecutionFacts",
    "ExecutionHandleFact",
    "ExitFactDTO",
    "FactCompleteness",
    "MockDeclarationFact",
    "MockDeclarationSourceFact",
    "MockVerificationStateFact",
    "OutputBlockFact",
    "OutputCursorFact",
    "PlanRevisionRefFact",
    "ProcessTerminationReasonFact",
    "ProjectionStateFact",
    "RedactionStateFact",
    "RedactionSummaryFact",
    "RunControlStateFact",
    "RunFact",
    "RunTierFact",
    "SideEffectClassFact",
    "SourceBindingKindFact",
    "SourceCheckFact",
    "SourceVerificationFact",
    "SourceVerificationStateFact",
    "StepFact",
    "StepLevelFact",
    "StepRevisionRefFact",
    "StepStateFact",
    "UnknownReasonFact",
    "VerificationFact",
    "VerificationObservationFact",
]
