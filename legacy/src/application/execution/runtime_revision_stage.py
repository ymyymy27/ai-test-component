"""运行中修订的投影与暂存。"""
# mypy: disable-error-code="misc,attr-defined"


from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from aitest.application.execution.commit_guards import (
    _json_payload,
    _run_pointer_id,
    _validate_current_facts,
    _validate_frozen_run_basis,
    _validate_frozen_step_basis,
    _validate_publication_current,
)
from aitest.application.execution.facts import (
    project_attempt_update,
)
from aitest.application.execution.runtime_revision import (
    RunRevisionRecord,
    RuntimePlanningRecordReader,
    SavedRuntimeRevisionReader,
    SnapshotContentRef,
    StepContentChange,
    request_from_payload,
    require_runtime_boundary,
    revision_input_digest,
    revision_record_id,
    revision_request_payload,
)
from aitest.application.execution.step_content import StepContentReader
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.saved_runtime_revision import SavedRuntimeRevisionAssessment
from aitest.application.planning.serialization import case_content_digest, case_to_payload
from aitest.application.ports import StageableWorkspaceUnitOfWork as StageableWorkspaceUnitOfWork
from aitest.contracts.execution_facts import (
    DependencyInvalidationFact,
    ExecutionFacts,
    FactCompleteness,
    RunControlStateFact,
    StepRevisionRefFact,
    StepStateFact,
)
from aitest.contracts.prepared_run import CaseRevisionRef, FrozenCaseStep, RunDriverFact
from aitest.contracts.prepared_run import PlanRevisionRef as PlanContentRef
from aitest.domain.evidence.evidence import (
    EvidenceRef,
)
from aitest.domain.execution.dependencies import (
    invalidate_downstream_attempts,
)
from aitest.domain.execution.runs import (
    AttemptState,
    RecoveryRecord,
    Run,
    Step,
    StepLevel,
    StepRevisionRef,
)
from aitest.domain.planning.plans import Plan
from aitest.domain.planning.runtime_revision import RuntimeRevisionRefused, RuntimeRevisionRequest

_CHECKPOINT_ADAPTER = TypeAdapter(RecoveryRecord)
_EVIDENCE_ADAPTER = TypeAdapter(EvidenceRef)


if TYPE_CHECKING:
    from aitest.application.execution.commit import ExecutionCommitCoordinator


class RuntimeRevisionStageMixin:
    def stage_runtime_revision(
        self: ExecutionCommitCoordinator,
        *,
        project_id: str,
        run_id: str,
        plan: Plan,
        request: RuntimeRevisionRequest,
        intent_id: str,
        confirmation_ids: tuple[str, ...] = (),
        on_revision_staged: Callable[[RunRevisionRecord], None] | None = None,
    ) -> tuple[ExecutionFacts, bool]:
        """Reassess and derive one exact revision in the caller's short transaction.

        This action constructs the permitted projection itself. It accepts no
        replacement facts, revision sequence number or generic admission bypass.
        Proposed Case revisions must already be accurately saved by B.
        """
        if self._records is None or not intent_id.strip():
            raise ValueError("runtime revision requires saved authority and a persistent intent")
        reader = SavedRuntimeRevisionReader(self._records)
        record_id = revision_record_id(project_id, run_id, intent_id)
        request_payload = revision_request_payload(request, project_id)
        request_from_payload(request_payload, project_id)
        fingerprint = revision_input_digest(
            project_id=project_id,
            run_id=run_id,
            request_payload=request_payload,
            confirmation_ids=confirmation_ids,
        )
        if self._revision("run_plan_revision", record_id):
            original = reader.read_record(
                project_id=project_id, reference=f"run_plan_revision:{record_id}@1"
            )
            if original.run_id != run_id or original.input_digest != fingerprint:
                raise ValueError("runtime revision intent conflicts with saved input")
            result = reader.read_snapshot(
                project_id=project_id, run_id=run_id, reference=original.result_snapshot
            )
            if (
                len(result.runtime_revision_refs) != original.revision_no
                or result.runtime_revision_refs[-1] != original.reference
                or result.facts_id != "runtime-revision:" + record_id
            ):
                raise ValueError("runtime revision receipt does not identify its exact result")
            return result, False
        before = self.read_runtime_revision_facts(project_id=project_id, run_id=run_id)
        assessment = SavedRuntimeRevisionAssessment(
            reader=RuntimePlanningRecordReader(reader, project_id),
            execution=self,
            runtime_basis=reader,
            approvals=self._approvals,
            controlled_writes=self._controlled_writes,
        )
        decision = assessment.assess(
            project_id=project_id,
            run_id=run_id,
            plan=plan,
            request=request,
            confirmation_ids=confirmation_ids,
        )
        if not decision.accepted:
            raise RuntimeRevisionRefused(decision)
        require_runtime_boundary(before)
        if self.read_runtime_revision_facts(project_id=project_id, run_id=run_id) != before:
            raise ValueError("runtime snapshot changed before revision staging")
        initial_cases = assessment._cases(project_id, plan)
        cases = {
            case.case_id: case
            for case in reader.read_effective_cases(
                facts=before, plan=plan, initial_cases=initial_cases
            )
        }
        changes = {change.next_case.case_id: change for change in request.case_changes}
        for change in changes.values():
            saved = reader._read(
                "case", change.next_case.case_id, change.next_case.revision, project_id
            )
            if saved != case_to_payload(change.next_case, project_id=project_id):
                raise ValueError("proposed runtime case differs from its saved exact revision")
            if len(change.next_case.steps) != len(cases[change.next_case.case_id].steps):
                raise ValueError(
                    "changed step layout requires explicit registration; mapping unknown"
                )
            cases[change.next_case.case_id] = change.next_case
        original_run = TypeAdapter(Run).validate_python(reader._read("run", run_id, 1, project_id))
        materials = {}
        step_changes = []
        reference = f"run_plan_revision:{record_id}@1"
        next_steps = []
        for fact in before.steps:
            if fact.step_id not in decision.affected_step_ids:
                next_steps.append(fact)
                continue
            step = TypeAdapter(Step).validate_python(
                reader._read("step", fact.step_id, 1, project_id)
            )
            step = replace(
                step,
                step_revision_ref=StepRevisionRef(**fact.step_revision_ref.model_dump()),
                level=StepLevel(fact.level.value),
            )
            body = StepContentReader(self._records).read(run=original_run, step=step)
            case = cases[fact.case_id]
            body = body.model_copy(
                update={
                    "case_revision_ref": CaseRevisionRef(
                        case_id=case.case_id,
                        revision=case.revision,
                        digest=case_content_digest(case, project_id=project_id),
                    ),
                    "case_content": case_to_payload(case, project_id=project_id),
                    "frozen_step": FrozenCaseStep.model_validate(
                        {
                            "step_id": body.frozen_step.step_id,
                            "layer": case.layer.value,
                            "objective": case.steps[body.case_step_index],
                            "expected": case.expected,
                        }
                    ),
                }
            )
            body.checked_case()
            next_ref = StepRevisionRefFact(
                step_revision_id="step-revision-" + payload_digest([record_id, fact.step_id])[7:],
                revision_no=1,
                digest=payload_digest(body.model_dump(mode="json")),
            )
            materials[next_ref.step_revision_id] = body
            step_changes.append(
                StepContentChange(
                    step_id=fact.step_id, previous_ref=fact.step_revision_ref, next_ref=next_ref
                )
            )
            next_steps.append(
                fact.model_copy(
                    update={
                        "step_revision_ref": next_ref,
                        "step_revision": 1,
                        "level": type(fact.level)(case.layer.value),
                    }
                )
            )
        checkpoints = {
            fact.attempt_id: self.read_checkpoint(project_id=project_id, attempt_id=fact.attempt_id)
            for fact in before.attempts
        }
        seeds = {
            attempt.attempt_id
            for attempt in before.attempts
            if attempt.step_id in decision.invalidated_basis_step_ids
        }
        consumers = invalidate_downstream_attempts(
            tuple(checkpoint.attempt for checkpoint in checkpoints.values()),
            previous_plan_revision=original_run.plan_revision_ref,
            current_plan_revision=original_run.plan_revision_ref,
            affected_upstream_attempt_ids=tuple(seeds),
        )
        affected_attempts = seeds | {item.attempt.attempt_id for item in consumers}
        next_attempts = []
        checkpoint_refs: dict[str, tuple[object, Mapping[str, object]]] = {}
        for attempt_fact in before.attempts:
            if attempt_fact.attempt_id not in affected_attempts:
                next_attempts.append(attempt_fact)
                continue
            old = checkpoints[attempt_fact.attempt_id]
            attempt = replace(
                old.attempt,
                state=AttemptState.INVALIDATED,
                unknown_reason_ref="runtime_revision_basis_outdated",
                revision=old.attempt.revision + 1,
            )
            checkpoint = replace(old, attempt=attempt)
            self._validate_checkpoint_update(project_id, checkpoint)
            self._stage_checkpoint_payload(
                checkpoint_refs=checkpoint_refs,
                aggregate_kind="execution_checkpoint",
                record_id=attempt_fact.attempt_id,
                expected_revision=self._revision("execution_checkpoint", attempt_fact.attempt_id),
                payload={
                    **_json_payload(_CHECKPOINT_ADAPTER, checkpoint),
                    "project_id": project_id,
                },
            )
            next_attempts.append(
                project_attempt_update(attempt, attempt_fact, is_current=attempt_fact.is_current)
            )
        affected_steps = set(decision.invalidated_basis_step_ids) | {
            item.attempt.step_id
            for item in consumers
            if before.current_attempt_by_step.get(item.attempt.step_id) == item.attempt.attempt_id
        }
        self._stage_authorization_revocations(
            before=before,
            superseded_attempt_ids=tuple(sorted(affected_attempts)),
            changed_step_ids=tuple(
                sorted(
                    affected_steps
                    | {item.step_id for item in step_changes}
                    | (
                        {step.step_id for step in before.steps}
                        if decision.effective_driver.value != before.run.driver.value
                        else set()
                    )
                )
            ),
        )
        next_steps = [
            step.model_copy(
                update={
                    "state": StepStateFact.INVALIDATED,
                    "invalidated": True,
                    "invalidated_by": reference,
                }
            )
            if step.step_id in affected_steps
            else step
            for step in next_steps
        ]
        now = datetime.now(UTC)
        events = tuple(
            DependencyInvalidationFact(
                invalidation_id=f"{record_id}:{item.attempt.attempt_id}:{upstream}",
                run_id=run_id,
                affected_step_id=item.attempt.step_id,
                affected_attempt_id=item.attempt.attempt_id,
                upstream_attempt_id=upstream,
                reason="runtime_revision_basis_outdated",
                source_revision_ref=reference,
                transitive=upstream not in seeds,
                invalidated_at=now,
            )
            for item in consumers
            for upstream in item.upstream_attempt_ids
        )
        refs = (*before.runtime_revision_refs, reference)
        revision = before.run_revision + 1
        facts = before.model_copy(
            update={
                "facts_id": "runtime-revision:" + record_id,
                "committed_at": now,
                "run_revision": revision,
                "runtime_revision_refs": refs,
                "run": before.run.model_copy(
                    update={
                        "run_revision": revision,
                        "runtime_revision_refs": refs,
                        "driver": RunDriverFact(decision.effective_driver.value),
                        "control_state": (
                            RunControlStateFact.PAUSED
                            if decision.pause_required
                            else before.run.control_state
                        ),
                        "result_ref": None,
                        "evidence_level": None,
                        "coverage_summary": None,
                        "ended_at": None,
                    }
                ),
                "steps": tuple(next_steps),
                "attempts": tuple(next_attempts),
                "dependency_invalidations": before.dependency_invalidations + events,
                "coverage": before.coverage.model_copy(
                    update={
                        "executed_attempt_ids": tuple(
                            item
                            for item in before.coverage.executed_attempt_ids
                            if item not in affected_attempts
                        ),
                        "invalidated_step_ids": tuple(
                            step.step_id for step in next_steps if step.invalidated
                        ),
                        "blocked_step_ids": tuple(
                            step.step_id
                            for step in next_steps
                            if step.state is StepStateFact.BLOCKED
                        ),
                        "unknown_step_ids": tuple(
                            step.step_id
                            for step in next_steps
                            if step.state is StepStateFact.BLOCKED
                        ),
                    }
                ),
                "completeness": FactCompleteness.PARTIAL,
            }
        )
        _validate_current_facts(facts)
        _validate_publication_current(before, facts)
        # These are the only basis changes this guarded action derives.
        _validate_frozen_run_basis(
            before,
            facts.model_copy(
                update={
                    "run": facts.run.model_copy(
                        update={
                            "driver": before.run.driver,
                            "runtime_revision_refs": before.run.runtime_revision_refs,
                        }
                    )
                }
            ),
        )
        _validate_frozen_step_basis(
            before,
            facts.model_copy(
                update={
                    "steps": tuple(
                        step.model_copy(
                            update={
                                "step_revision_ref": original.step_revision_ref,
                                "step_revision": original.step_revision,
                                "level": original.level,
                            }
                        )
                        for step, original in zip(facts.steps, before.steps, strict=True)
                    )
                }
            ),
        )
        for content_id, body in materials.items():
            self._uow.stage_record(
                aggregate_kind="step_revision",
                record_id=content_id,
                expected_revision=0,
                payload=body.model_dump(mode="json"),
            )
        sequence = int(self._uow.next_commit_seq()) + 2 + bool(facts.attempts)
        facts = facts.model_copy(
            update={
                "snapshot_commit_id": f"commit-{sequence}",
                "snapshot_cursor": sequence,
                "snapshot_revision": 1,
            }
        )
        record = RunRevisionRecord(
            schema_version="aitest.run-plan-revision/1.0",
            record_id=record_id,
            project_id=project_id,
            origin_workspace_id=before.run.origin_workspace_id,
            run_id=run_id,
            intent_id=intent_id,
            input_digest=fingerprint,
            revision_no=decision.revision_no,
            previous_revision_ref=before.runtime_revision_refs[-1]
            if before.runtime_revision_refs
            else None,
            initial_plan_ref=PlanContentRef(**before.plan_revision.model_dump()),
            base_snapshot=SnapshotContentRef.of(before),
            result_snapshot=SnapshotContentRef.of(facts),
            request_payload=request_payload,
            confirmation_ids=confirmation_ids,
            effective_case_refs=tuple(
                CaseRevisionRef(
                    case_id=case.case_id,
                    revision=case.revision,
                    digest=case_content_digest(case, project_id=project_id),
                )
                for case in sorted(cases.values(), key=lambda case: case.case_id)
            ),
            step_changes=tuple(sorted(step_changes, key=lambda change: change.step_id)),
            invalidated_basis_step_ids=decision.invalidated_basis_step_ids,
            invalidated_consumer_attempt_ids=tuple(
                sorted(item.attempt.attempt_id for item in consumers)
            ),
            pause_required=decision.pause_required,
            effective_driver=RunDriverFact(decision.effective_driver.value),
            created_at=now,
        )
        self._uow.stage_record(
            aggregate_kind="run_plan_revision",
            record_id=record_id,
            expected_revision=0,
            payload=record.model_dump(mode="json"),
        )
        if int(self._uow.next_commit_seq()) + 1 + bool(facts.attempts) != sequence:
            raise ValueError("runtime revision result does not match its reserved commit boundary")
        result = self._stage_snapshot_records(
            facts,
            previous=before,
            pointer_id=_run_pointer_id(project_id, run_id),
            expected_revisions={},
            checkpoint_refs=checkpoint_refs,
        )[1]
        if on_revision_staged is not None:
            on_revision_staged(record)
        return result, True
