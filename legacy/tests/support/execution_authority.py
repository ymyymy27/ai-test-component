"""Explicit saved fixture authority for non-security runner component tests.

This narrow test port freezes exact authorized actions before any start. It does
not simulate human consent or certify the real ExecutionAuthorizationService;
the original-authority consumer tests exercise that service separately.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import (
    ExecutionFactsAssembler,
    ExecutionFactsAssembly,
    execution_payload_digest,
)
from aitest.application.execution.runner import SerialRunner
from aitest.application.execution.start_identity import execution_start_fingerprint
from aitest.contracts.prepared_run import ConclusionCeilingFact, RunDriverFact
from aitest.domain.execution.runs import (
    Attempt,
    Run,
    RunControlState,
    RunTier,
    Step,
    StepLevel,
    attempt_start_basis,
)
from aitest.domain.project.context import IsolationMode
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork


def _prepared(attempt, request):
    return replace(
        attempt,
        intent_id=request.intent_id,
        authorization_ref=request.authorization_ref,
        expected_plan_revision_ref=request.expected_plan_revision_ref
        or request.authorization_ref.plan_revision_ref,
        timeout_ms=request.timeout_ms,
    )


class SavedFixtureExecutionAuthority:
    def __init__(self, unit):
        self.unit = unit

    @staticmethod
    def key(identity):
        return "fixture-original-authority:" + identity

    def read(self, attempt):
        if attempt.authorization_ref is None:
            raise ValueError("fixture original authorization is missing")
        identity = self.key(attempt.authorization_ref.authorization_id)
        raw = self.unit.read(
            aggregate_kind="execution_authorization", record_id=identity, revision=1
        ).payload
        saved = TypeAdapter(Attempt).validate_json(json.dumps(raw["attempt"]), strict=True)
        if attempt_start_basis(attempt) != attempt_start_basis(saved):
            raise ValueError("fixture original authorization has different input")
        return identity, raw

    def validate_new(self, *, project_id, attempt):
        identity, raw = self.read(attempt)
        state = self.unit.read(
            aggregate_kind="execution_authorization", record_id=identity + ":state", revision=1
        ).payload
        if (
            raw["project_id"] != project_id
            or self.unit.current_revision(
                aggregate_kind="execution_authorization", record_id=identity + ":state"
            )
            != 1
            or state != {"project_id": project_id, "occupied_by_attempt_id": None}
        ):
            raise ValueError("fixture original authorization is already consumed or foreign")

    def stage_occupation(
        self, *, project_id, attempt, superseded_attempt_ids=(), changed_step_ids=()
    ):
        self.validate_new(project_id=project_id, attempt=attempt)
        identity, raw = self.read(attempt)
        self.unit.stage_record(
            aggregate_kind="execution_authorization",
            record_id=identity + ":state",
            expected_revision=1,
            payload={"project_id": project_id, "occupied_by_attempt_id": attempt.attempt_id},
        )
        return {
            "grant_id": attempt.authorization_ref.authorization_id,
            "grant_revision": 1,
            "grant_digest": execution_payload_digest(raw),
            "state_revision": 2,
        }

    def validate_occupation(self, *, project_id, attempt, proof):
        identity, raw = self.read(attempt)
        occupied = self.unit.read(
            aggregate_kind="execution_authorization", record_id=identity + ":state", revision=2
        ).payload
        if (
            raw["project_id"] != project_id
            or occupied != {"project_id": project_id, "occupied_by_attempt_id": attempt.attempt_id}
            or proof
            != {
                "grant_id": attempt.authorization_ref.authorization_id,
                "grant_revision": 1,
                "grant_digest": execution_payload_digest(raw),
                "state_revision": 2,
            }
        ):
            raise ValueError("fixture original authorization occupation differs")

    def stage_revoke_affected(
        self, *, project_id, run_id, superseded_attempt_ids, changed_step_ids
    ):
        raise ValueError("fixture authority does not implement runtime authorization revocation")


def fixture_coordinator(unit, authorized, *, steps=(), checkpoint_store=None):
    """Explicitly authorize only the supplied exact pairs, with saved initial facts."""
    pairs = tuple((_prepared(attempt, request), request) for attempt, request in authorized)
    proof = SavedFixtureExecutionAuthority(unit)
    coordinator = ExecutionCommitCoordinator(
        unit, checkpoint_store=checkpoint_store, execution_authorizations=proof
    )
    groups = {}
    missing = []
    for attempt, request in pairs:
        attempt = replace(attempt, intent_digest=execution_start_fingerprint(attempt, request))
        key = proof.key(request.authorization_ref.authorization_id)
        if not unit.current_revision(aggregate_kind="execution_authorization", record_id=key):
            missing.append((key, attempt, request))
        groups.setdefault((request.project_id, request.run_id), []).append((attempt, request))
    for (project, run_id), items in groups.items():
        if coordinator.read_current_facts(project_id=project, run_id=run_id) is not None:
            continue
        attempt, request = items[0]
        run = Run(
            run_id,
            project,
            "explicit-component-fixture",
            "fixture-registration-" + run_id,
            RunTier.FULL,
            RunDriverFact.PLANNED.value,
            ConclusionCeilingFact.PASSABLE.value,
            attempt.expected_plan_revision_ref,
            request.environment_ref,
            IsolationMode.VENV,
            "fixture-rules",
            revision=1,
            control_state=RunControlState.NOT_STARTED,
            source_binding_digest=request.source_binding_digest,
        )
        by_step = {item.step_id: item for item, _ in items}
        frozen_steps = steps or tuple(
            Step(item.step_id, run_id, ordinal, "case-1", StepLevel.L2, item.step_revision_ref)
            for ordinal, item in enumerate(by_step.values(), 1)
        )
        facts = ExecutionFactsAssembler().assemble(
            ExecutionFactsAssembly(
                facts_id="fixture-initial-" + run_id,
                snapshot_commit_id="pending-fixture-" + run_id,
                snapshot_cursor=0,
                snapshot_revision=1,
                committed_at=datetime.now(UTC),
                run=run,
                steps=frozen_steps,
                attempts=(),
            )
        )
        unit.begin("explicit-fixture-registration", project)
        coordinator._stage_snapshot(facts)
        unit.commit()
    for identity, attempt, request in missing:
        unit.begin("explicit-fixture-original-authority", request.project_id)
        unit.stage_record(
            aggregate_kind="execution_authorization",
            record_id=identity,
            expected_revision=0,
            payload={
                "project_id": request.project_id,
                "fixture_only": True,
                "attempt": TypeAdapter(Attempt).dump_python(attempt, mode="json"),
            },
        )
        unit.stage_record(
            aggregate_kind="execution_authorization",
            record_id=identity + ":state",
            expected_revision=0,
            payload={"project_id": request.project_id, "occupied_by_attempt_id": None},
        )
        unit.commit()
    return coordinator


def fixture_runner(root: Path, port, authorized, *, steps=(), **kwargs):
    coordinator = fixture_coordinator(
        FileUnitOfWork(root),
        authorized,
        steps=steps,
        checkpoint_store=kwargs.get("checkpoint_store"),
    )
    return SerialRunner(port, commit_coordinator=coordinator, **kwargs)
