"""Generate CD-001 ExecutionFacts fixtures through the production assembler."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from aitest.application.execution.facts import (
    ExecutionFactsAssembler,
    ExecutionFactsAssembly,
    ExecutionFactsBoundary,
)
from aitest.contracts.execution_facts import (
    CoverageSummary,
    DependencyInvalidationFact,
    EvidenceGapFact,
    FactCompleteness,
    UnknownReasonFact,
)
from aitest.domain.evidence.evidence import (
    CodeIdentity,
    EvidenceCaptureSource,
    EvidenceIntegrity,
    EvidenceKind,
    EvidenceLevel,
    EvidenceRef,
    ProjectionState,
    RedactionState,
    Verification,
    VerificationObservation,
)
from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    AuthorizationRef,
    CaptureCompleteness,
    ExecutionHandle,
    ExitFact,
    FailureClass,
    OutputBlockRef,
    OutputCursor,
    OutputStreamName,
    PlanRevisionRef,
    ProcessTerminationReason,
    Run,
    RunControlState,
    RunTier,
    SideEffectClass,
    Step,
    StepLevel,
    StepRevisionRef,
    StepState,
)
from aitest.domain.execution.sources import (
    ExecutionSourceVerification,
    SourceBindingKind,
    SourceCheckResult,
    SourceCheckType,
    SourceVerificationState,
)
from aitest.domain.project.context import IsolationMode

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "docs/接口对接/进行中/CD-001-ExecutionFacts/fixtures"
PROJECT_ID = "project-1"
RUN_ID = "run-1"
COMMIT_ID = "commit-1"
CURSOR = 10
REVISION = 1
COMMITTED_AT = datetime(2026, 10, 7, 10, 1, tzinfo=UTC)
PLAN_REVISION = PlanRevisionRef(
    revision_id="plan-1",
    revision_no=1,
    digest="sha256:plan-1",
)
STEP_REVISION = StepRevisionRef(
    step_revision_id="step-rev-1",
    revision_no=1,
    digest="sha256:step-1",
)
AUTHORIZATION = AuthorizationRef(
    authorization_id="authorization-1",
    intent_id="intent-1",
    step_id="step-1",
    resolved_input_digest="sha256:input-1",
    target_ref="target-1",
    credential_scope_ref="scope-1",
    plan_revision_ref=PLAN_REVISION,
)


def _sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _run(
    *,
    state: RunControlState,
    tier: RunTier = RunTier.FULL,
    evidence_level: str | None = "full_link",
    isolation: IsolationMode = IsolationMode.VENV,
) -> Run:
    return Run(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        origin_workspace_id="ws-1",
        intent_id="intent-1",
        tier=tier,
        driver="planned",
        conclusion_ceiling="passable" if tier is RunTier.FULL else "partial",
        plan_revision_ref=PLAN_REVISION,
        environment_ref="env-1",
        environment_isolation_mode=isolation,
        rules_revision="rules-1",
        control_state=state,
        evidence_level=evidence_level,
        required_scope=frozenset({"case-1"}),
        selected_scope=frozenset({"case-1"}),
        source_binding_digest="sha256:source-1",
        started_at=COMMITTED_AT,
        ended_at=COMMITTED_AT,
    )


def _step(
    step_id: str,
    state: StepState,
    *,
    ordinal: int = 1,
    current_attempt_id: str | None = None,
    invalidated_by: str | None = None,
) -> Step:
    return Step(
        step_id=step_id,
        run_id=RUN_ID,
        ordinal=ordinal,
        case_id="case-1",
        level=StepLevel.L2,
        step_revision_ref=STEP_REVISION,
        state=state,
        current_attempt_id=current_attempt_id,
        invalidated_by=invalidated_by,
    )


def _handle() -> ExecutionHandle:
    return ExecutionHandle(
        handle_id="handle-1",
        adapter_kind=AdapterKind.COMMAND,
        adapter_version="1.0",
        real_execution_id="proc-1",
        process_start_identity="start-1",
        workdir_ref="ws-1",
    )


def _outputs(
    attempt_id: str,
    streams: dict[OutputStreamName, bytes],
    *,
    complete: bool,
) -> tuple[tuple[OutputBlockRef, ...], tuple[OutputCursor, ...], tuple[EvidenceRef, ...]]:
    blocks: list[OutputBlockRef] = []
    cursors: list[OutputCursor] = []
    evidence: list[EvidenceRef] = []
    for stream_name, content in streams.items():
        digest = _sha(content)
        block = OutputBlockRef(
            block_id=f"{attempt_id}:{stream_name.value}:0",
            attempt_id=attempt_id,
            stream_name=stream_name,
            block_index=0,
            offset=0,
            length=len(content),
            digest=digest,
            complete=complete,
            capture_source="command",
        )
        blocks.append(block)
        cursors.append(
            OutputCursor(
                attempt_id=attempt_id,
                stream_name=stream_name,
                offset=len(content),
                last_block_index=0,
                last_committed_digest=digest,
                durable=True,
            )
        )
        evidence.append(
            EvidenceRef(
                evidence_id=f"evidence-{stream_name.value}",
                project_id=PROJECT_ID,
                source_instance_id=RUN_ID,
                run_id=RUN_ID,
                step_id="step-1",
                attempt_id=attempt_id,
                evidence_kind=EvidenceKind.COMMAND_OUTPUT,
                capture_source=EvidenceCaptureSource.PLUGIN_RUNTIME,
                object_digest=digest,
                object_size=len(content) or 1,
                code_identity=CodeIdentity(
                    binding_kind=SourceBindingKind.GIT,
                    workspace_ref="ws-1",
                    commit_id="abc123",
                ),
                integrity=(
                    EvidenceIntegrity.COMPLETE
                    if complete
                    else EvidenceIntegrity.MISSING_TAIL
                ),
                redaction_state=RedactionState.NOT_REQUIRED,
                projection_state=ProjectionState.DISPLAYABLE,
                evidence_level=(
                    EvidenceLevel.FULL_LINK if complete else EvidenceLevel.UNKNOWN
                ),
            )
        )
    return tuple(blocks), tuple(cursors), tuple(evidence)


def _attempt(
    *,
    state: AttemptState,
    capture: CaptureCompleteness,
    streams: dict[OutputStreamName, bytes],
    timed_out: bool = False,
    termination: ProcessTerminationReason = ProcessTerminationReason.NATURAL_EXIT,
    with_exit: bool = True,
    unknown_reason: str | None = None,
) -> tuple[Attempt, tuple[EvidenceRef, ...]]:
    blocks, cursors, evidence = _outputs(
        "attempt-1",
        streams,
        complete=capture is CaptureCompleteness.COMPLETE,
    )
    exit_fact = (
        ExitFact(
            attempt_id="attempt-1",
            startup_token="token-1",
            process_start_identity="start-1",
            real_exit_code=0 if not timed_out else -1,
            last_block_index_by_stream=tuple(
                (cursor.stream_name, cursor.last_block_index) for cursor in cursors
            ),
            saved_bytes_by_stream=tuple(
                (cursor.stream_name, cursor.offset) for cursor in cursors
            ),
            capture_completeness=capture,
            termination_reason=termination,
            timed_out=timed_out,
        )
        if with_exit
        else None
    )
    return (
        Attempt(
            attempt_id="attempt-1",
            run_id=RUN_ID,
            step_id="step-1",
            attempt_index=1,
            intent_id="intent-1",
            intent_digest="sha256:intent-1",
            resolved_input_digest="sha256:input-1",
            step_revision_ref=STEP_REVISION,
            source_binding_digest="sha256:source-1",
            side_effect_class=SideEffectClass.READ_ONLY,
            adapter_kind=AdapterKind.COMMAND,
            adapter_version="1.0",
            state=state,
            expected_plan_revision_ref=PLAN_REVISION,
            authorization_ref=AUTHORIZATION,
            timeout_ms=10000 if timed_out else None,
            timed_out=timed_out,
            execution_handle_ref=_handle(),
            output_cursors=cursors,
            output_block_refs=blocks,
            exit_fact_ref=exit_fact,
            capture_completeness=capture,
            unknown_reason_ref=unknown_reason,
            started_at=COMMITTED_AT,
            ended_at=COMMITTED_AT,
        ),
        evidence,
    )


def _source_check(
    *,
    failure: FailureClass,
    evidence_refs: tuple[str, ...],
) -> SourceCheckResult:
    return SourceCheckResult(
        check_result_id="check-1",
        attempt_id="attempt-1",
        check_type=SourceCheckType.LOAD,
        scope="module",
        source_snapshot_ref="snapshot-1",
        environment_ref="environment-1",
        rules_revision="rules-1",
        adapter_version="python-checks/1.0",
        failure_class=failure,
        entry_ref="entry-1",
        argument_refs=("--check",),
        raw_output_evidence_ref=evidence_refs[0] if evidence_refs else None,
        evidence_refs=evidence_refs,
    )


def _source_verification(
    *,
    state: SourceVerificationState,
    evidence_refs: tuple[str, ...],
    gap_ids: tuple[str, ...] = (),
) -> ExecutionSourceVerification:
    return ExecutionSourceVerification(
        verification_id="source-verify-1",
        project_id=PROJECT_ID,
        plan_revision_ref=PLAN_REVISION,
        expected_source_binding_digest="sha256:source-1",
        materialized_snapshot_ref="snapshot-1",
        observed_source_digest="sha256:source-1",
        state=state,
        observed_entry_ref="entry-1",
        gap_ids=gap_ids,
        evidence_refs=evidence_refs,
        verified_at=COMMITTED_AT,
    )


def _verification(
    observation: VerificationObservation,
    *,
    evidence_refs: tuple[str, ...],
    gap_ids: tuple[str, ...] = (),
) -> Verification:
    return Verification(
        verification_id="business-verify-1",
        verification_of="business_assertion",
        business_object_id="order-1",
        query_method="independent_read_only",
        observation=observation,
        query_interval="configured",
        deadline_condition="immediate",
        target_deployment_ref="deployment-1",
        actual_result_ref=evidence_refs[0] if evidence_refs else None,
        evidence_refs=evidence_refs,
        gap_ids=gap_ids,
        created_at=COMMITTED_AT,
    )


def _assembly(
    name: str,
    *,
    run: Run,
    steps: tuple[Step, ...],
    attempt: Attempt,
    evidence: tuple[EvidenceRef, ...],
    source_failure: FailureClass,
    source_state: SourceVerificationState,
    verification_observation: VerificationObservation,
    verification_gaps: tuple[str, ...] = (),
    gaps: tuple[EvidenceGapFact, ...] = (),
    unknowns: tuple[UnknownReasonFact, ...] = (),
    invalidations: tuple[DependencyInvalidationFact, ...] = (),
    completeness: FactCompleteness,
    evidence_readable: bool,
) -> ExecutionFactsAssembly:
    evidence_ids = tuple(item.evidence_id for item in evidence)
    executed = (attempt.attempt_id,) if attempt.state is not AttemptState.INVALIDATED else ()
    return ExecutionFactsAssembly(
        facts_id=f"facts-{name}",
        snapshot_commit_id=COMMIT_ID,
        snapshot_cursor=CURSOR,
        snapshot_revision=REVISION,
        committed_at=COMMITTED_AT,
        boundary=ExecutionFactsBoundary(COMMIT_ID, CURSOR, REVISION),
        run=run,
        steps=steps,
        attempts=(attempt,),
        evidence_refs=evidence,
        source_check_results=(
            _source_check(failure=source_failure, evidence_refs=evidence_ids),
        ),
        source_verifications=(
            _source_verification(
                state=source_state,
                evidence_refs=evidence_ids,
                gap_ids=("source_probe_unverified",)
                if source_state is not SourceVerificationState.VERIFIED
                else (),
            ),
        ),
        verifications=(
            _verification(
                verification_observation,
                evidence_refs=evidence_ids,
                gap_ids=verification_gaps,
            ),
        ),
        dependency_invalidations=invalidations,
        unknowns=unknowns,
        gaps=gaps,
        coverage=CoverageSummary(
            mandatory_case_ids=("case-1",),
            selected_case_ids=("case-1",),
            executed_attempt_ids=executed,
            blocked_step_ids=tuple(
                step.step_id for step in steps if step.state is StepState.BLOCKED
            ),
            invalidated_step_ids=tuple(
                step.step_id for step in steps if step.state is StepState.INVALIDATED
            ),
        ),
        completeness=completeness,
        evidence_readable=evidence_readable,
    )


def _gap(gap_id: str, reason: str, *, critical: bool = True) -> EvidenceGapFact:
    return EvidenceGapFact(
        gap_id=gap_id,
        kind="execution",
        subject_ref="attempt-1",
        reason_code=gap_id,
        safe_reason=reason,
        critical=critical,
    )


def _unknown(reason: str) -> UnknownReasonFact:
    return UnknownReasonFact(
        unknown_reason_id=f"unknown-{reason}",
        subject_type="attempt",
        subject_id="attempt-1",
        reason_code=reason,
        safe_reason=reason,
    )


def _success_like(
    name: str,
    *,
    tier: RunTier = RunTier.FULL,
    isolation: IsolationMode = IsolationMode.VENV,
    streams: dict[OutputStreamName, bytes] | None = None,
    evidence_level: str | None = "full_link",
) -> ExecutionFactsAssembly:
    attempt, evidence = _attempt(
        state=AttemptState.COMPLETED,
        capture=CaptureCompleteness.COMPLETE,
        streams=streams or {OutputStreamName.STDOUT: b"hello success\n"},
    )
    return _assembly(
        name,
        run=_run(
            state=RunControlState.COMPLETED,
            tier=tier,
            evidence_level=evidence_level,
            isolation=isolation,
        ),
        steps=(_step("step-1", StepState.COMPLETED, current_attempt_id="attempt-1"),),
        attempt=attempt,
        evidence=evidence,
        source_failure=FailureClass.PASSED,
        source_state=SourceVerificationState.VERIFIED,
        verification_observation=VerificationObservation.MATCHED,
        completeness=FactCompleteness.COMPLETE,
        evidence_readable=True,
    )


def _build(name: str) -> ExecutionFactsAssembly:
    if name == "success":
        return _success_like(
            name,
            streams={
                OutputStreamName.STDOUT: b"hello stdout",
                OutputStreamName.STDERR: b"stderr!\n",
            },
        )
    if name == "quick":
        return _success_like(
            name,
            tier=RunTier.QUICK,
            isolation=IsolationMode.UNMANAGED,
            evidence_level=None,
        )
    if name == "multistream":
        return _success_like(
            name,
            streams={
                OutputStreamName.STDOUT: b"multistream stdout\n",
                OutputStreamName.STDERR: b"multistream stderr\n",
            },
        )
    if name == "non_utf8":
        stdout = (FIXTURE_DIR / "non_utf8.stdout.bin").read_bytes()
        stderr = (FIXTURE_DIR / "non_utf8.stderr.bin").read_bytes()
        return _success_like(
            name,
            streams={
                OutputStreamName.STDOUT: stdout,
                OutputStreamName.STDERR: stderr,
            },
        )
    if name == "failure":
        attempt, evidence = _attempt(
            state=AttemptState.EXECUTION_ERROR,
            capture=CaptureCompleteness.GAP,
            streams={},
            with_exit=False,
            unknown_reason="execution_error",
        )
        invalidation = DependencyInvalidationFact(
            invalidation_id="invalidation-1",
            run_id=RUN_ID,
            affected_step_id="step-2",
            upstream_attempt_id="attempt-1",
            reason="upstream_execution_error",
            source_revision_ref=STEP_REVISION.digest,
            transitive=False,
        )
        return _assembly(
            name,
            run=_run(state=RunControlState.COMPLETED, evidence_level=None),
            steps=(
                _step(
                    "step-1",
                    StepState.EXECUTION_ERROR,
                    current_attempt_id="attempt-1",
                ),
                _step(
                    "step-2",
                    StepState.BLOCKED,
                    ordinal=2,
                    invalidated_by="attempt-1",
                ),
            ),
            attempt=attempt,
            evidence=evidence,
            source_failure=FailureClass.TOOL_FAILURE,
            source_state=SourceVerificationState.VERIFIED,
            verification_observation=VerificationObservation.NO_RESULT,
            verification_gaps=("business_verification_not_reached",),
            gaps=(_gap("execution_error", "执行失败，尚未进入业务核验"),),
            invalidations=(invalidation,),
            completeness=FactCompleteness.PARTIAL,
            evidence_readable=False,
        )
    if name == "unknown":
        attempt, evidence = _attempt(
            state=AttemptState.PENDING_VERIFICATION,
            capture=CaptureCompleteness.PARTIAL,
            streams={OutputStreamName.STDOUT: b"partial output"},
            with_exit=False,
            unknown_reason="execution_result_unknown",
        )
        return _assembly(
            name,
            run=_run(
                state=RunControlState.PENDING_VERIFICATION,
                evidence_level=None,
                isolation=IsolationMode.NONE,
            ),
            steps=(
                _step(
                    "step-1",
                    StepState.PENDING_VERIFICATION,
                    current_attempt_id="attempt-1",
                ),
            ),
            attempt=attempt,
            evidence=evidence,
            source_failure=FailureClass.TOOL_FAILURE,
            source_state=SourceVerificationState.UNVERIFIED,
            verification_observation=VerificationObservation.QUERY_ERROR,
            verification_gaps=("independent_query_unavailable",),
            gaps=(_gap("unknown_execution_result", "执行结果未知，需要核实"),),
            unknowns=(_unknown("execution_result_unknown"),),
            completeness=FactCompleteness.UNKNOWN,
            evidence_readable=False,
        )
    if name == "timeout":
        attempt, evidence = _attempt(
            state=AttemptState.PENDING_VERIFICATION,
            capture=CaptureCompleteness.PARTIAL,
            streams={OutputStreamName.STDOUT: b"partial timeout output\n"},
            timed_out=True,
            termination=ProcessTerminationReason.TIMEOUT,
            unknown_reason="command_timeout",
        )
        return _assembly(
            name,
            run=_run(state=RunControlState.PENDING_VERIFICATION, evidence_level=None),
            steps=(
                _step(
                    "step-1",
                    StepState.PENDING_VERIFICATION,
                    current_attempt_id="attempt-1",
                ),
            ),
            attempt=attempt,
            evidence=evidence,
            source_failure=FailureClass.PASSED,
            source_state=SourceVerificationState.VERIFIED,
            verification_observation=VerificationObservation.NO_RESULT,
            verification_gaps=("execution_timeout_verification_pending",),
            gaps=(_gap("command_timeout", "命令超时，业务结果仍需核实"),),
            completeness=FactCompleteness.PARTIAL,
            evidence_readable=False,
        )
    if name == "business_failure":
        attempt, evidence = _attempt(
            state=AttemptState.COMPLETED,
            capture=CaptureCompleteness.COMPLETE,
            streams={OutputStreamName.STDOUT: b"business assertion failed\n"},
        )
        return _assembly(
            name,
            run=_run(state=RunControlState.COMPLETED, evidence_level="full_link"),
            steps=(_step("step-1", StepState.COMPLETED, current_attempt_id="attempt-1"),),
            attempt=attempt,
            evidence=evidence,
            source_failure=FailureClass.PASSED,
            source_state=SourceVerificationState.VERIFIED,
            verification_observation=VerificationObservation.MISMATCHED,
            completeness=FactCompleteness.COMPLETE,
            evidence_readable=True,
        )
    raise ValueError(f"unknown fixture: {name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*")
    args = parser.parse_args()
    names = args.names or [
        "success",
        "failure",
        "unknown",
        "quick",
        "timeout",
        "multistream",
        "non_utf8",
        "business_failure",
    ]
    assembler = ExecutionFactsAssembler()
    for name in names:
        facts = assembler.assemble(_build(name))
        payload = facts.model_dump(mode="json", exclude_none=True)
        (FIXTURE_DIR / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {name}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
