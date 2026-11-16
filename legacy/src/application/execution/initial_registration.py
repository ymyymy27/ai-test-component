"""初始运行登记。"""
# mypy: disable-error-code="misc,attr-defined"


from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from aitest.application.execution.commit_guards import (
    _json_payload,
    _payload_digest,
)
from aitest.application.execution.facts import (
    ExecutionFactsAssembler,
    ExecutionFactsAssembly,
)
from aitest.application.execution.run_record import run_record_payload
from aitest.application.ports import StageableWorkspaceUnitOfWork as StageableWorkspaceUnitOfWork
from aitest.contracts.execution_facts import (
    ExecutionFacts,
)
from aitest.domain.evidence.evidence import (
    EvidenceRef,
)
from aitest.domain.execution.runs import (
    RecoveryRecord,
    Run,
    RunControlState,
    Step,
    StepState,
)

_CHECKPOINT_ADAPTER = TypeAdapter(RecoveryRecord)
_EVIDENCE_ADAPTER = TypeAdapter(EvidenceRef)


if TYPE_CHECKING:
    from aitest.application.execution.commit import ExecutionCommitCoordinator


class InitialRegistrationMixin:
    def stage_initial_run(
        self: ExecutionCommitCoordinator,
        *,
        run: Run,
        steps: tuple[Step, ...],
        facts: ExecutionFacts,
        fingerprint: str,
        prepared_run_id: str,
        intent_id: str,
    ) -> ExecutionFacts:
        """Stage the initial run in an already open common UOW; fabricate no attempt."""
        projected = ExecutionFactsAssembler().assemble(
            ExecutionFactsAssembly(
                facts_id=facts.facts_id,
                snapshot_commit_id=facts.snapshot_commit_id,
                snapshot_cursor=facts.snapshot_cursor,
                snapshot_revision=facts.snapshot_revision,
                committed_at=facts.committed_at,
                run=run,
                steps=steps,
                attempts=(),
            )
        )
        if (
            run.control_state is not RunControlState.NOT_STARTED
            or run.revision != 1
            or run.started_at is not None
            or run.ended_at is not None
            or projected.run != facts.run
            or projected.steps != facts.steps
            or projected.current_attempt_by_step != facts.current_attempt_by_step
            or set(facts.coverage.mandatory_case_ids) != run.required_scope
            or set(facts.coverage.selected_case_ids) != run.selected_scope
            or facts.coverage.executed_attempt_ids
            or facts.run.control_state.value != "not_started"
            or (facts.project_id, facts.run_id) != (run.project_id, run.run_id)
            or facts.attempts
            or facts.verifications
            or facts.evidence_refs
            or facts.source_check_results
            or facts.source_verifications
            or facts.run.evidence_level is not None
            or facts.run.source_binding_digest is not None
            or any(
                s.state is not StepState.PENDING or s.current_attempt_id is not None for s in steps
            )
            or self.read_current_facts(project_id=run.project_id, run_id=run.run_id) is not None
        ):
            raise ValueError("initial run must contain only unexecuted frozen steps")
        if self._revision("run", run.run_id) or any(
            self._revision("step", s.step_id) for s in steps
        ):
            raise ValueError("run/step identity already exists without this registration intent")
        self._uow.stage_record(
            aggregate_kind="run",
            record_id=run.run_id,
            expected_revision=0,
            payload=run_record_payload(run),
        )
        for step in steps:
            self._uow.stage_record(
                aggregate_kind="step",
                record_id=step.step_id,
                expected_revision=0,
                payload={"project_id": run.project_id, **_json_payload(TypeAdapter(Step), step)},
            )
        # Intent, pointer, and snapshot are the last three records of this batch.
        sequence = int(self._uow.next_commit_seq()) + 2
        frozen = facts.model_copy(
            update={
                "snapshot_commit_id": f"commit-{sequence}",
                "snapshot_cursor": sequence,
                "snapshot_revision": 1,
            }
        )
        self._uow.stage_record(
            aggregate_kind="execution_intent",
            record_id="run-registration:" + run.run_id,
            expected_revision=0,
            payload={
                "schema_version": "aitest.run-registration-intent/1.0",
                "project_id": run.project_id,
                "run_id": run.run_id,
                "intent_id": intent_id,
                "prepared_run_id": prepared_run_id,
                "fingerprint": fingerprint,
                "snapshot_commit_id": frozen.snapshot_commit_id,
                "snapshot_revision": 1,
                "snapshot_digest": _payload_digest(frozen.model_dump(mode="json")),
            },
        )
        _staged, published = self._stage_snapshot(frozen)
        if published != frozen:
            raise ValueError("initial run snapshot does not match its reserved commit boundary")
        return published
