"""Issue #127: a verified runtime observation must restore source identity.

The admission probe is deliberately downgraded after start because it cannot
certify the process that actually ran. The runtime observation replaces that
fact; if the replacement is lost, every report keeps
``actual_execution_load_source_unverified`` and caps at evidence grade C.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from aitest.application.review.execution_decision_adapter import build_decision_facts
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.execution.runs import (
    AdapterKind,
    AttemptState,
    CaptureCompleteness,
    ExecutionHandle,
    ExitFact,
    ProcessTerminationReason,
    RecoveryCheckpoint,
    RecoveryRecord,
)
from aitest.domain.execution.runtime_source import RuntimeSourceObservation
from aitest.domain.review.reports import SourceIdentityState
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_default_start_source_chain import (
    _authoritative,
    _chain_environment,
    _prepare_execution,
)
from tests.unit.test_execution_authorization_origin import review, save, started
from tests.unit.test_reuse_consumption import _attempt, _facts

_ADMISSION_GAP = "actual_execution_load_source_unverified"


def _evidence(identity: str, attempt_id: str, *, kind: str = "source_check") -> dict[str, object]:
    return {
        "evidence_id": identity,
        "evidence_revision": 1,
        "project_id": "project-1",
        "source_instance_id": "instance-1",
        "run_id": "run-target",
        "step_id": "step-1",
        "attempt_id": attempt_id,
        "evidence_kind": kind,
        "capture_source": "plugin_runtime",
        "code_identity": {"binding_kind": "plain", "workspace_ref": "binding-1@1"},
        "object_digest": "sha256:" + "a" * 64,
        "object_size": 3,
        "integrity": "complete",
        "redaction_state": "not_required",
        "projection_state": "displayable",
        "evidence_level": "full_link",
    }


def _source(
    attempt_id: str,
    evidence_ids: tuple[str, ...],
    *,
    state: str = "verified",
    gaps: tuple[str, ...] = (),
    verification_id: str | None = None,
) -> dict[str, object]:
    return {
        "verification_id": verification_id or "source-verification:" + attempt_id,
        "project_id": "project-1",
        "plan_revision": {
            "revision_id": "plan-1",
            "revision_no": 1,
            "digest": "sha256:plan-1",
        },
        "expected_source_binding_digest": "snapshot-content-1",
        "materialized_snapshot_ref": "snapshot-content-1",
        "observed_source_digest": "snapshot-content-1",
        "state": state,
        "gap_ids": list(gaps),
        "evidence_refs": list(evidence_ids),
        "observed_entry_ref": "entry-1",
    }


def _decision(
    *,
    attempts: tuple[dict[str, object], ...],
    sources: tuple[dict[str, object], ...],
    evidence: tuple[dict[str, object], ...],
) -> SourceIdentityState:
    raw = _facts(attempts=attempts).model_dump(mode="json")
    raw["source_verifications"] = list(sources)
    raw["evidence_refs"] = list(evidence)
    return build_decision_facts(ExecutionFacts.model_validate(raw)).source_identity_state


def test_verified_runtime_fact_supersedes_unverified_admission_probe() -> None:
    """A runtime observation for the same attempt wins over the saved probe."""

    attempt = _attempt()
    admission = _source(
        "attempt-src-1", ("source-evidence-attempt-src-1",),
        state="unverified", gaps=(_ADMISSION_GAP,),
    )
    runtime = _source(
        "attempt-src-1", ("runtime-source-evidence-attempt-src-1",),
        verification_id="source-verification:attempt-src-1:runtime",
    )
    state = _decision(
        attempts=(attempt,),
        sources=(admission, runtime),
        evidence=(
            _evidence("source-evidence-attempt-src-1", "attempt-src-1"),
            _evidence("runtime-source-evidence-attempt-src-1", "attempt-src-1"),
        ),
    )
    assert state is SourceIdentityState.MATCHED


def test_admission_probe_alone_remains_unverified() -> None:
    """No runtime observation: the downgraded probe cannot certify the run."""

    state = _decision(
        attempts=(_attempt(),),
        sources=(
            _source(
                "attempt-src-1", ("source-evidence-attempt-src-1",),
                state="unverified", gaps=(_ADMISSION_GAP,),
            ),
        ),
        evidence=(_evidence("source-evidence-attempt-src-1", "attempt-src-1"),),
    )
    assert state is SourceIdentityState.UNVERIFIED


def test_runtime_observation_with_gap_remains_unverified() -> None:
    """A runtime fact that itself carries a gap must not restore identity."""

    state = _decision(
        attempts=(_attempt(),),
        sources=(
            _source(
                "attempt-src-1", ("source-evidence-attempt-src-1",),
                state="unverified", gaps=(_ADMISSION_GAP,),
            ),
            _source(
                "attempt-src-1", ("runtime-source-evidence-attempt-src-1",),
                gaps=("actual_source_capture_incomplete",),
                verification_id="source-verification:attempt-src-1:runtime",
            ),
        ),
        evidence=(
            _evidence("source-evidence-attempt-src-1", "attempt-src-1"),
            _evidence("runtime-source-evidence-attempt-src-1", "attempt-src-1"),
        ),
    )
    assert state is SourceIdentityState.UNVERIFIED


def test_evidence_less_probe_of_a_superseded_attempt_does_not_cap_the_run() -> None:
    """A probe with no saved material still belongs to its attempt by identity."""

    old = {**_attempt("attempt-old", state="invalidated"), "is_current": False}
    current = _attempt("attempt-current")
    raw = _facts(
        selected=("case-1", "case-2"),
        case_steps=(("step-1", "case-1"), ("step-2", "case-2")),
        attempts=(old, current),
    ).model_dump(mode="json")
    raw["source_verifications"] = [
        _source(
            "attempt-old", (),
            state="unverified", gaps=("source_probe_material_unavailable",),
        ),
        _source(
            "attempt-current", ("runtime-source-evidence-attempt-current",),
            verification_id="source-verification:attempt-current",
        ),
    ]
    raw["evidence_refs"] = [
        _evidence("runtime-source-evidence-attempt-current", "attempt-current"),
    ]
    state = build_decision_facts(ExecutionFacts.model_validate(raw)).source_identity_state
    assert state is SourceIdentityState.MATCHED


def test_runtime_observation_replaces_admission_probe_in_saved_facts(tmp_path) -> None:
    """Real core path: publish, replace the probe, and keep both materials."""

    with _authoritative(tmp_path, environment_resolver=_chain_environment()) as (core, inputs, _):
        try:
            prepared = prepare(core, inputs)
            assert prepared.error is None, prepared.error
            parameters = _prepare_execution(core, inputs, prepared.result)
            service = core.execution_authorizations
            _, action = service.resolver.read(
                inputs.project_id, parameters["execution_action_id"]
            )
            source = action.source_verification
            assert source is not None and source.capture_plan is not None
            actor, challenge = review(service, inputs.project_id, action, parameters)
            save(service, inputs.project_id, action, parameters, actor, challenge)
            attempt = started(action)
            record = RecoveryRecord(
                RecoveryCheckpoint(
                    last_committed_stage="intent_recorded",
                    run_id=attempt.run_id,
                    step_id=attempt.step_id,
                    attempt_id=attempt.attempt_id,
                    resolved_input_digest=attempt.resolved_input_digest,
                    side_effect_class=attempt.side_effect_class,
                ),
                attempt,
                project_id=inputs.project_id,
            )
            assert core.execution_coordinator.claim_start(
                project_id=inputs.project_id,
                intent_id=attempt.intent_id,
                fingerprint=attempt.intent_digest,
                checkpoint=record,
            ) is None
            before = core.execution_coordinator.read_current_facts(
                project_id=inputs.project_id, run_id=attempt.run_id
            )
            admission = next(
                item for item in before.source_verifications
                if item.verification_id == "source-verification:" + attempt.attempt_id
            )
            assert admission.state.value == "unverified"
            assert admission.gap_ids == (_ADMISSION_GAP,)

            handle = ExecutionHandle(
                handle_id="handle-1",
                adapter_kind=AdapterKind.COMMAND,
                adapter_version=attempt.adapter_version,
                real_execution_id="4242",
                process_start_identity="start-identity-1",
                workdir_ref=source.materialized_snapshot_ref,
            )
            observation = RuntimeSourceObservation(
                attempt_id=attempt.attempt_id,
                process_start_identity=handle.process_start_identity,
                process_id=handle.real_execution_id,
                interpreter=source.observed_interpreter_ref or "",
                entry=source.observed_entry_ref or "",
                files=source.capture_plan.files,
                complete=True,
                policy=source.capture_plan.policy,
                executing_process_id=handle.real_execution_id,
                executing_process_start_identity=handle.process_start_identity,
            )
            completed = replace(
                attempt,
                state=AttemptState.COMPLETED,
                execution_handle_ref=handle,
                exit_fact_ref=ExitFact(
                    attempt_id=attempt.attempt_id,
                    startup_token="startup-1",
                    process_start_identity=handle.process_start_identity,
                    real_exit_code=0,
                    termination_reason=ProcessTerminationReason.NATURAL_EXIT,
                    capture_completeness=CaptureCompleteness.COMPLETE,
                ),
                runtime_source=observation,
                capture_completeness=CaptureCompleteness.COMPLETE,
                started_at=datetime(2026, 11, 3, tzinfo=UTC),
                ended_at=datetime(2026, 11, 3, 0, 1, tzinfo=UTC),
            )
            core.execution_coordinator.commit_checkpoint(
                project_id=inputs.project_id,
                checkpoint=RecoveryRecord(
                    RecoveryCheckpoint(
                        last_committed_stage="completed",
                        run_id=completed.run_id,
                        step_id=completed.step_id,
                        attempt_id=completed.attempt_id,
                        resolved_input_digest=completed.resolved_input_digest,
                        side_effect_class=completed.side_effect_class,
                        execution_handle_ref=handle,
                    ),
                    completed,
                    project_id=inputs.project_id,
                ),
            )
            after = core.execution_coordinator.read_current_facts(
                project_id=inputs.project_id, run_id=attempt.run_id
            )
            observed = next(
                item for item in after.source_verifications
                if item.verification_id == "source-verification:" + attempt.attempt_id
            )
            assert observed.state.value == "verified"
            assert observed.gap_ids == ()
            assert observed.evidence_refs == (
                "runtime-source-evidence-" + attempt.attempt_id,
            )
            assert build_decision_facts(after).source_identity_state is SourceIdentityState.MATCHED
            assert {
                item.evidence_id for item in after.evidence_refs
            } >= {
                "source-evidence-" + attempt.attempt_id,
                "runtime-source-evidence-" + attempt.attempt_id,
            }
        finally:
            core.lifetime_lock.release()
