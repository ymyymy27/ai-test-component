"""Assemble the frozen ExecutionFacts contract from package C facts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from aitest.contracts.execution_facts import (
    AdapterKindFact,
    AttemptFact,
    AttemptStateFact,
    CaptureCompletenessFact,
    CodeIdentityFact,
    ConsumedConditionFact,
    ConsumedOutputFact,
    CoverageSummary,
    DependencyInvalidationFact,
    EvidenceCaptureSourceFact,
    EvidenceFact,
    EvidenceGapFact,
    EvidenceIntegrityFact,
    EvidenceKindFact,
    EvidenceLevelFact,
    ExecutionFacts,
    ExecutionHandleFact,
    ExitFactDTO,
    FactCompleteness,
    MockDeclarationFact,
    MockDeclarationSourceFact,
    MockVerificationStateFact,
    OutputBlockFact,
    OutputCursorFact,
    PlanRevisionRefFact,
    ProcessTerminationReasonFact,
    ProjectionStateFact,
    RedactionStateFact,
    RedactionSummaryFact,
    RunControlStateFact,
    RunFact,
    SideEffectClassFact,
    SourceBindingKindFact,
    SourceCheckFact,
    SourceVerificationFact,
    SourceVerificationStateFact,
    StepFact,
    StepLevelFact,
    StepRevisionRefFact,
    StepStateFact,
    UnknownReasonFact,
    VerificationFact,
    VerificationObservationFact,
)
from aitest.contracts.prepared_run import (
    ConclusionCeilingFact,
    RunDriverFact,
    RunTierFact,
)
from aitest.domain.evidence.evidence import (
    EvidenceRef,
    MockDeclaration,
    RedactionSummary,
    Verification,
)
from aitest.domain.execution.runs import Attempt, PlanRevisionRef, Run, Step
from aitest.domain.execution.sources import ExecutionSourceVerification, SourceCheckResult


def _enum[EnumT: StrEnum](enum_type: type[EnumT], value: str) -> EnumT:
    return enum_type(value)


@dataclass(frozen=True, slots=True)
class ExecutionFactsAssembly:
    facts_id: str
    snapshot_commit_id: str
    snapshot_cursor: int
    snapshot_revision: int
    committed_at: datetime
    run: Run
    steps: tuple[Step, ...]
    attempts: tuple[Attempt, ...]
    evidence_refs: tuple[EvidenceRef, ...] = ()
    source_check_results: tuple[SourceCheckResult, ...] = ()
    source_verifications: tuple[ExecutionSourceVerification, ...] = ()
    verifications: tuple[Verification, ...] = ()
    mock_declarations: tuple[MockDeclaration, ...] = ()
    dependency_invalidations: tuple[DependencyInvalidationFact, ...] = ()
    unknowns: tuple[UnknownReasonFact, ...] = ()
    gaps: tuple[EvidenceGapFact, ...] = ()
    redaction_summaries: Mapping[str, RedactionSummary] = field(default_factory=dict)
    coverage: CoverageSummary = field(default_factory=CoverageSummary)
    completeness: FactCompleteness = FactCompleteness.COMPLETE


class ExecutionFactsAssembler:
    """Map internal C domain facts to the versioned C-D contract."""

    def assemble(self, assembly: ExecutionFactsAssembly) -> ExecutionFacts:
        run = assembly.run
        attempts_by_step: dict[str, tuple[Attempt, ...]] = {}
        for attempt in assembly.attempts:
            attempts_by_step.setdefault(attempt.step_id, ())
            attempts_by_step[attempt.step_id] += (attempt,)
        current_attempt_by_step = {step.step_id: step.current_attempt_id for step in assembly.steps}
        return ExecutionFacts(
            facts_id=assembly.facts_id,
            project_id=run.project_id,
            run_id=run.run_id,
            snapshot_commit_id=assembly.snapshot_commit_id,
            snapshot_cursor=assembly.snapshot_cursor,
            snapshot_revision=assembly.snapshot_revision,
            committed_at=assembly.committed_at,
            run_revision=run.revision,
            plan_revision=_plan_revision(run.plan_revision_ref),
            runtime_revision_refs=run.runtime_revision_refs,
            run=_run_fact(run),
            steps=tuple(_step_fact(step) for step in assembly.steps),
            attempts=tuple(
                _attempt_fact(
                    attempt,
                    is_current=current_attempt_by_step.get(attempt.step_id) == attempt.attempt_id,
                    redaction_summaries=assembly.redaction_summaries,
                )
                for attempt in assembly.attempts
            ),
            current_attempt_by_step=current_attempt_by_step,
            evidence_refs=tuple(
                _evidence_fact(item, assembly.redaction_summaries)
                for item in assembly.evidence_refs
            ),
            source_check_results=tuple(
                SourceCheckFact(
                    check_result_id=item.check_result_id,
                    attempt_id=item.attempt_id,
                    check_type=item.check_type.value,
                    scope=item.scope,
                    source_snapshot_ref=item.source_snapshot_ref,
                    environment_ref=item.environment_ref,
                    rules_revision=item.rules_revision,
                    adapter_version=item.adapter_version,
                    failure_class=item.failure_class.value,
                    raw_output_evidence_ref=item.raw_output_evidence_ref,
                    evidence_refs=item.evidence_refs,
                )
                for item in assembly.source_check_results
            ),
            source_verifications=tuple(
                _source_verification_fact(item) for item in assembly.source_verifications
            ),
            verifications=tuple(_verification_fact(item) for item in assembly.verifications),
            mock_declarations=tuple(_mock_fact(item) for item in assembly.mock_declarations),
            dependency_invalidations=assembly.dependency_invalidations,
            unknowns=assembly.unknowns,
            gaps=assembly.gaps,
            coverage=assembly.coverage,
            completeness=assembly.completeness,
        )


def _plan_revision(ref: PlanRevisionRef) -> PlanRevisionRefFact:
    return PlanRevisionRefFact(
        revision_id=ref.revision_id,
        revision_no=ref.revision_no,
        digest=ref.digest,
    )


def _step_revision(ref: object) -> StepRevisionRefFact:
    return StepRevisionRefFact(
        step_revision_id=ref.step_revision_id,  # type: ignore[attr-defined]
        revision_no=ref.revision_no,  # type: ignore[attr-defined]
        digest=ref.digest,  # type: ignore[attr-defined]
        inherited=ref.inherited,  # type: ignore[attr-defined]
        base_step_revision_id=ref.base_step_revision_id,  # type: ignore[attr-defined]
    )


def _run_fact(run: Run) -> RunFact:
    return RunFact(
        run_id=run.run_id,
        run_revision=run.revision,
        origin_workspace_id=run.origin_workspace_id,
        intent_id=run.intent_id,
        tier=_enum(RunTierFact, run.tier.value),
        driver=_enum(RunDriverFact, run.driver),
        conclusion_ceiling=_enum(ConclusionCeilingFact, run.conclusion_ceiling),
        plan_revision=_plan_revision(run.plan_revision_ref),
        environment_ref=run.environment_ref,
        environment_isolated=run.environment_isolated,
        rules_revision=run.rules_revision,
        control_state=_enum(RunControlStateFact, run.control_state.value),
        evidence_level=(
            _enum(EvidenceLevelFact, run.evidence_level) if run.evidence_level is not None else None
        ),
        primary_gap_ids=run.primary_gap_ids,
        coverage_summary=run.coverage_summary,
        runtime_revision_refs=run.runtime_revision_refs,
        required_scope=tuple(sorted(run.required_scope)),
        selected_scope=tuple(sorted(run.selected_scope)),
        source_binding_digest=run.source_binding_digest or None,
        result_ref=run.result_ref,
        started_at=run.started_at,
        ended_at=run.ended_at,
    )


def _step_fact(step: Step) -> StepFact:
    return StepFact(
        step_id=step.step_id,
        run_id=step.run_id,
        step_revision=step.step_revision_ref.revision_no,
        step_revision_ref=_step_revision(step.step_revision_ref),
        ordinal=step.ordinal,
        case_id=step.case_id,
        required_for_case=step.required_for_case,
        level=_enum(StepLevelFact, step.level.value),
        state=_enum(StepStateFact, step.state.value),
        dependency_step_ids=tuple(
            edge.upstream_step_id for edge in step.dependency_edges if edge.required
        ),
        registered_entry_ref=(
            step.registered_entry_ref.entry_id if step.registered_entry_ref is not None else None
        ),
        assertion_refs=step.assertion_refs,
        evidence_requirement_ids=tuple(
            requirement.requirement_id for requirement in step.evidence_requirements
        ),
        current_attempt_id=step.current_attempt_id,
        invalidated=step.state.value == "invalidated",
        invalidated_by=step.invalidated_by,
    )


def _attempt_fact(
    attempt: Attempt,
    *,
    is_current: bool,
    redaction_summaries: Mapping[str, RedactionSummary],
) -> AttemptFact:
    return AttemptFact(
        attempt_id=attempt.attempt_id,
        run_id=attempt.run_id,
        step_id=attempt.step_id,
        attempt_revision=attempt.revision,
        attempt_index=attempt.attempt_index,
        retry_count=attempt.retry_count,
        state=_enum(AttemptStateFact, attempt.state.value),
        is_current=is_current,
        intent_id=attempt.intent_id or None,
        intent_digest=attempt.intent_digest or None,
        resolved_input_digest=attempt.resolved_input_digest,
        step_revision_ref=_step_revision(attempt.step_revision_ref),
        source_binding_digest=attempt.source_binding_digest,
        consumed_outputs=tuple(
            ConsumedOutputFact(
                upstream_attempt_id=item.upstream_attempt_id,
                output_object_digest=item.output_object_digest,
                value_ref=item.value_ref,
            )
            for item in attempt.consumed_outputs
        ),
        consumed_conditions=tuple(
            ConsumedConditionFact(
                upstream_attempt_id=item.upstream_attempt_id,
                condition_fact_ref=item.condition_fact_ref,
                condition_digest=item.condition_digest,
            )
            for item in attempt.consumed_conditions
        ),
        side_effect_class=_enum(SideEffectClassFact, attempt.side_effect_class.value),
        business_idempotency_key_ref=attempt.business_idempotency_key_ref,
        transport_retry_count=attempt.transport_retry_count,
        authorization_ref=(
            attempt.authorization_ref.authorization_id
            if attempt.authorization_ref is not None
            else None
        ),
        adapter_kind=_enum(AdapterKindFact, attempt.adapter_kind.value),
        adapter_version=attempt.adapter_version,
        timeout_ms=attempt.timeout_ms,
        timed_out=attempt.timed_out,
        handle=(
            ExecutionHandleFact(
                handle_id=attempt.execution_handle_ref.handle_id,
                adapter_kind=_enum(
                    AdapterKindFact,
                    attempt.execution_handle_ref.adapter_kind.value,
                ),
                adapter_version=attempt.execution_handle_ref.adapter_version,
                real_execution_id=attempt.execution_handle_ref.real_execution_id,
                process_start_identity=attempt.execution_handle_ref.process_start_identity,
                workdir_ref=attempt.execution_handle_ref.workdir_ref,
            )
            if attempt.execution_handle_ref is not None
            else None
        ),
        output_cursors=tuple(
            OutputCursorFact(
                attempt_id=cursor.attempt_id,
                stream_name=cursor.stream_name.value,
                offset=cursor.offset,
                last_block_index=cursor.last_block_index,
                last_committed_digest=cursor.last_committed_digest,
                durable=cursor.durable,
            )
            for cursor in attempt.output_cursors
        ),
        output_blocks=tuple(
            OutputBlockFact(
                block_id=block.block_id,
                attempt_id=block.attempt_id,
                stream_name=block.stream_name.value,
                block_index=block.block_index,
                offset=block.offset,
                length=block.length,
                digest=block.digest,
                complete=block.complete,
                capture_source=block.capture_source,
                redaction_summary=_redaction_summary_fact(
                    block.redaction_summary_id,
                    redaction_summaries,
                ),
            )
            for block in attempt.output_block_refs
        ),
        exit_fact=(
            ExitFactDTO(
                attempt_id=attempt.exit_fact_ref.attempt_id,
                startup_token=attempt.exit_fact_ref.startup_token,
                process_start_identity=attempt.exit_fact_ref.process_start_identity,
                real_exit_code=attempt.exit_fact_ref.real_exit_code,
                last_block_index_by_stream={
                    stream.value: index
                    for stream, index in attempt.exit_fact_ref.last_block_index_by_stream
                },
                saved_bytes_by_stream={
                    stream.value: size
                    for stream, size in attempt.exit_fact_ref.saved_bytes_by_stream
                },
                capture_completeness=_enum(
                    CaptureCompletenessFact,
                    attempt.exit_fact_ref.capture_completeness.value,
                ),
                termination_reason=_enum(
                    ProcessTerminationReasonFact,
                    attempt.exit_fact_ref.termination_reason.value,
                ),
                timed_out=attempt.exit_fact_ref.timed_out,
                published_at=attempt.exit_fact_ref.published_at,
            )
            if attempt.exit_fact_ref is not None
            else None
        ),
        capture_completeness=_enum(
            CaptureCompletenessFact,
            attempt.capture_completeness.value,
        ),
        error_ref=(attempt.error_ref.error_id if attempt.error_ref is not None else None),
        unknown_reason_ids=(
            (attempt.unknown_reason_ref,) if attempt.unknown_reason_ref is not None else ()
        ),
        started_at=attempt.started_at,
        ended_at=attempt.ended_at,
    )


def _evidence_fact(
    item: EvidenceRef,
    redaction_summaries: Mapping[str, RedactionSummary],
) -> EvidenceFact:
    return EvidenceFact(
        evidence_id=item.evidence_id,
        evidence_revision=item.evidence_revision,
        project_id=item.project_id,
        source_instance_id=item.source_instance_id,
        run_id=item.run_id,
        step_id=item.step_id,
        attempt_id=item.attempt_id,
        evidence_kind=_enum(EvidenceKindFact, item.evidence_kind.value),
        capture_source=_enum(EvidenceCaptureSourceFact, item.capture_source.value),
        code_identity=CodeIdentityFact(
            binding_kind=_enum(
                SourceBindingKindFact,
                item.code_identity.binding_kind.value,
            ),
            workspace_ref=item.code_identity.workspace_ref,
            commit_id=item.code_identity.commit_id,
            file_manifest_digest=item.code_identity.file_manifest_digest,
            revision_ref=item.code_identity.revision_ref,
        ),
        object_digest=item.object_digest,
        object_size=item.object_size,
        media_type=item.media_type,
        integrity=_enum(EvidenceIntegrityFact, item.integrity.value),
        redaction_state=_enum(RedactionStateFact, item.redaction_state.value),
        projection_state=_enum(ProjectionStateFact, item.projection_state.value),
        redaction_summary=_redaction_summary_fact(
            item.redaction_summary_ref,
            redaction_summaries,
        ),
        evidence_level=_enum(EvidenceLevelFact, item.evidence_level.value),
        gap_ids=item.gap_ids,
        created_at=item.created_at,
    )


def _redaction_summary_fact(
    summary_id: str | None,
    summaries: Mapping[str, RedactionSummary],
) -> RedactionSummaryFact | None:
    if summary_id is None:
        return None
    summary = summaries.get(summary_id)
    if summary is None:
        return RedactionSummaryFact(policy_version=summary_id)
    return RedactionSummaryFact(
        policy_version=summary.policy_version,
        applied_rule_categories=summary.applied_rule_categories,
        filtered_streams=summary.filtered_streams,
        filtered_ranges=summary.filtered_ranges,
        replacement_count=summary.replacement_count,
        completeness=summary.completeness,
        gap_reasons=summary.gap_reasons,
        created_at=summary.created_at,
    )


def _source_verification_fact(item: ExecutionSourceVerification) -> SourceVerificationFact:
    return SourceVerificationFact(
        verification_id=item.verification_id,
        project_id=item.project_id,
        plan_revision=_plan_revision(item.plan_revision_ref),
        expected_source_binding_digest=item.expected_source_binding_digest,
        materialized_snapshot_ref=item.materialized_snapshot_ref,
        observed_source_digest=item.observed_source_digest,
        state=_enum(SourceVerificationStateFact, item.state.value),
        observed_entry_ref=item.observed_entry_ref,
        observed_import_ref=item.observed_import_ref,
        failure_class=item.failure_class.value if item.failure_class is not None else None,
        gap_ids=item.gap_ids,
        evidence_refs=item.evidence_refs,
        verified_at=item.verified_at,
    )


def _verification_fact(item: Verification) -> VerificationFact:
    return VerificationFact(
        verification_id=item.verification_id,
        verification_of=item.verification_of,
        business_object_id=item.business_object_id,
        query_method=item.query_method,
        observation=_enum(VerificationObservationFact, item.observation.value),
        query_interval=item.query_interval,
        deadline_condition=item.deadline_condition,
        target_deployment_ref=item.target_deployment_ref,
        actual_result_ref=item.actual_result_ref,
        key_trace_link_refs=item.key_trace_link_refs,
        covers_critical_chain_item_ids=item.covers_critical_chain_item_ids,
        evidence_refs=item.evidence_refs,
        gap_ids=item.gap_ids,
        verified_at=item.created_at,
    )


def _mock_fact(item: MockDeclaration) -> MockDeclarationFact:
    return MockDeclarationFact(
        mock_declaration_id=item.mock_declaration_id,
        content_revision=item.content_revision,
        project_id=item.project_id,
        idempotency_key=item.idempotency_key,
        declaration_source=_enum(
            MockDeclarationSourceFact,
            item.declaration_source.value,
        ),
        replaced_object_ref=item.replaced_object_ref,
        replaced_behavior_ref=item.replaced_behavior_ref,
        scope=item.scope,
        verification_state=_enum(
            MockVerificationStateFact,
            item.verification_state.value,
        ),
        verification_conclusion_ref=item.verification_conclusion_ref,
        evidence_refs=item.evidence_refs,
        created_at=item.created_at,
    )


__all__ = ["ExecutionFactsAssembler", "ExecutionFactsAssembly"]
