"""Persisted runtime revision material and exact effective-case provenance."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from aitest.application.execution.facts import (
    execution_payload_digest,
    validate_frozen_run_basis,
    validate_frozen_step_basis,
)
from aitest.application.execution.snapshots import read_execution_snapshot
from aitest.application.execution.step_content import StepContentReader
from aitest.application.planning.draft import text_digest
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.run_mode import request_runtime_revision
from aitest.application.planning.serialization import (
    case_content_digest,
    case_from_payload,
    case_to_payload,
)
from aitest.application.ports import AggregateKind, CommittedRecord, RecordRepository
from aitest.contracts.execution_facts import (
    AttemptStateFact,
    DependencyInvalidationFact,
    ExecutionFacts,
    FactCompleteness,
    RunControlStateFact,
    StepRevisionRefFact,
    StepStateFact,
)
from aitest.contracts.prepared_run import CaseRevisionRef, PlanRevisionRef, RunDriverFact
from aitest.domain.execution.dependencies import downstream_consumers
from aitest.domain.execution.runs import (
    ExitFact,
    ProcessTerminationReason,
    Run,
    Step,
    StepLevel,
    StepRevisionRef,
    is_verified_exit_fact,
)
from aitest.domain.planning.plans import Case, Plan, RunDriver
from aitest.domain.planning.plans import CaseRevisionRef as DomainCaseRevisionRef
from aitest.domain.planning.runtime_revision import CaseRuntimeChange, RuntimeRevisionRequest


class SnapshotContentRef(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    snapshot_commit_id: str = Field(min_length=1)
    snapshot_cursor: int = Field(ge=0)
    digest: str = Field(min_length=1)

    @classmethod
    def of(cls, facts: ExecutionFacts) -> SnapshotContentRef:
        return cls(
            snapshot_commit_id=facts.snapshot_commit_id,
            snapshot_cursor=facts.snapshot_cursor,
            digest=execution_payload_digest(facts.model_dump(mode="json")),
        )


class StepContentChange(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    step_id: str = Field(min_length=1)
    previous_ref: StepRevisionRefFact
    next_ref: StepRevisionRefFact


class RunRevisionRecord(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["aitest.run-plan-revision/1.0"]
    record_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    origin_workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    intent_id: str = Field(min_length=1)
    input_digest: str = Field(min_length=1)
    revision_no: int = Field(ge=1)
    previous_revision_ref: str | None
    initial_plan_ref: PlanRevisionRef
    base_snapshot: SnapshotContentRef
    result_snapshot: SnapshotContentRef
    request_payload: dict[str, Any]
    confirmation_ids: tuple[str, ...]
    effective_case_refs: tuple[CaseRevisionRef, ...]
    step_changes: tuple[StepContentChange, ...]
    invalidated_basis_step_ids: tuple[str, ...]
    invalidated_consumer_attempt_ids: tuple[str, ...]
    pause_required: bool
    effective_driver: RunDriverFact
    created_at: datetime

    @property
    def reference(self) -> str:
        return f"run_plan_revision:{self.record_id}@1"


def revision_record_id(project_id: str, run_id: str, intent_id: str) -> str:
    return "run-revision-" + payload_digest([project_id, run_id, intent_id])[7:]


def revision_request_payload(request: RuntimeRevisionRequest, project_id: str) -> dict[str, Any]:
    return {
        "base_plan_revision_id": request.base_plan_revision_id,
        "base_plan_revision_no": request.base_plan_revision_no,
        "base_plan_revision_digest": request.base_plan_revision_digest,
        "observed_snapshot_cursor": request.observed_snapshot_cursor,
        "case_changes": [
            {
                "case_content": case_to_payload(change.next_case, project_id=project_id),
                "target_step_ids": sorted(change.target_step_ids),
                "remove_from_required": change.remove_from_required,
            }
            for change in sorted(request.case_changes, key=lambda change: change.next_case.case_id)
        ],
        "reason": request.reason,
        "operator_ref": request.operator_ref,
        "requested_driver": request.requested_driver.value if request.requested_driver else None,
    }


def request_from_payload(payload: Mapping[str, Any], project_id: str) -> RuntimeRevisionRequest:
    expected = {
        "base_plan_revision_id",
        "base_plan_revision_no",
        "base_plan_revision_digest",
        "observed_snapshot_cursor",
        "case_changes",
        "reason",
        "operator_ref",
        "requested_driver",
    }
    if set(payload) != expected or any(
        type(payload[key]) is not int
        for key in ("base_plan_revision_no", "observed_snapshot_cursor")
    ):
        raise ValueError("runtime revision request shape cannot be verified")
    for key in ("base_plan_revision_id", "reason", "operator_ref"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise ValueError("runtime revision request text cannot be verified")
    for key in ("base_plan_revision_digest", "requested_driver"):
        if payload[key] is not None and not isinstance(payload[key], str):
            raise ValueError("runtime revision request reference cannot be verified")
    changes = payload["case_changes"]
    if not isinstance(changes, list):
        raise ValueError("runtime revision changes must be a list")
    parsed = []
    for change in changes:
        if (
            not isinstance(change, dict)
            or set(change) != {"case_content", "target_step_ids", "remove_from_required"}
            or type(change["remove_from_required"]) is not bool
            or not isinstance(change["target_step_ids"], list)
            or any(
                not isinstance(item, str) or not item.strip() for item in change["target_step_ids"]
            )
            or not isinstance(change["case_content"], dict)
            or change["case_content"].get("project_id") != project_id
        ):
            raise ValueError("runtime revision case change cannot be verified")
        parsed.append(
            CaseRuntimeChange(
                case_from_payload(change["case_content"]),
                tuple(change["target_step_ids"]),
                change["remove_from_required"],
            )
        )
    request = RuntimeRevisionRequest(
        base_plan_revision_id=payload["base_plan_revision_id"],
        base_plan_revision_no=payload["base_plan_revision_no"],
        base_plan_revision_digest=payload["base_plan_revision_digest"],
        observed_snapshot_cursor=payload["observed_snapshot_cursor"],
        case_changes=tuple(parsed),
        reason=payload["reason"],
        operator_ref=payload["operator_ref"],
        requested_driver=(
            RunDriver(payload["requested_driver"])
            if payload["requested_driver"] is not None
            else None
        ),
    )
    if revision_request_payload(request, project_id) != dict(payload):
        raise ValueError("runtime revision request is not its canonical complete content")
    return request


def revision_input_digest(
    *,
    project_id: str,
    run_id: str,
    request_payload: Mapping[str, Any],
    confirmation_ids: tuple[str, ...],
) -> str:
    return payload_digest([project_id, run_id, dict(request_payload), sorted(confirmation_ids)])


def require_runtime_boundary(facts: ExecutionFacts) -> None:
    """State labels cannot establish that a started external execution stopped."""
    if any(step.state is StepStateFact.RUNNING for step in facts.steps) or any(
        attempt.state.value
        in {
            "intent_recorded",
            "starting",
            "running",
            "stop_requested",
            "collecting",
            "unknown",
            "pending_verification",
        }
        or (
            attempt.handle is not None
            and not is_verified_exit_fact(
                attempt.attempt_id,
                attempt.handle.process_start_identity,
                ExitFact(
                    attempt_id=attempt.exit_fact.attempt_id,
                    startup_token=attempt.exit_fact.startup_token,
                    process_start_identity=attempt.exit_fact.process_start_identity,
                    real_exit_code=attempt.exit_fact.real_exit_code,
                    termination_reason=ProcessTerminationReason(
                        attempt.exit_fact.termination_reason.value
                    ),
                    timed_out=attempt.exit_fact.timed_out,
                )
                if attempt.exit_fact is not None
                else None,
            )
        )
        for attempt in facts.attempts
    ):
        raise ValueError("runtime revision requires a verified non-active step boundary")


class SavedRuntimeRevisionReader:
    """Check every saved link; a sequence count cannot establish effective content."""

    def __init__(self, records: RecordRepository) -> None:
        self.records = records

    def _read(self, kind: str, record_id: str, revision: int, project_id: str) -> dict[str, Any]:
        if type(revision) is not int or revision < 1:
            raise ValueError("runtime material requires an exact warehouse revision")
        record = self.records.read(aggregate_kind=kind, record_id=record_id, revision=revision)
        raw_payload = getattr(record, "payload", None)
        if (
            (
                getattr(record, "aggregate_kind", None),
                getattr(record, "record_id", None),
                getattr(record, "revision", None),
            )
            != (kind, record_id, revision)
            or type(getattr(record, "revision", None)) is not int
            or not isinstance(raw_payload, Mapping)
        ):
            raise ValueError("runtime material envelope cannot be verified")
        payload = dict(raw_payload)
        if payload.get("project_id") != project_id:
            raise ValueError("runtime material belongs to another project")
        return payload

    def read_record(self, *, project_id: str, reference: str) -> RunRevisionRecord:
        match = re.fullmatch(r"run_plan_revision:(run-revision-[0-9a-f]{64})@1", reference)
        if match is None:
            raise ValueError("saved runtime revision sequence is not readable")
        raw = self._read("run_plan_revision", match[1], 1, project_id)
        record = RunRevisionRecord.model_validate_json(json.dumps(raw), strict=True)
        if (
            record.record_id != match[1]
            or record.record_id != revision_record_id(project_id, record.run_id, record.intent_id)
            or record.input_digest
            != revision_input_digest(
                project_id=project_id,
                run_id=record.run_id,
                request_payload=record.request_payload,
                confirmation_ids=record.confirmation_ids,
            )
            or len(record.confirmation_ids) != len(set(record.confirmation_ids))
        ):
            raise ValueError("saved runtime revision identity or input cannot be verified")
        request_from_payload(record.request_payload, project_id)
        return record

    def read_snapshot(
        self, *, project_id: str, run_id: str, reference: SnapshotContentRef
    ) -> ExecutionFacts:
        return read_execution_snapshot(
            self.records, project_id=project_id, run_id=run_id,
            snapshot_id=reference.snapshot_commit_id, digest=reference.digest,
            snapshot_cursor=reference.snapshot_cursor,
        )

    def read_effective_cases(
        self, *, facts: ExecutionFacts, plan: Plan, initial_cases: tuple[Case, ...]
    ) -> tuple[Case, ...]:
        references = facts.runtime_revision_refs
        if references != facts.run.runtime_revision_refs or len(references) != len(set(references)):
            raise ValueError("runtime revision sequence disagrees with the saved run")
        project_id, run_id = facts.project_id, facts.run_id
        original_run = TypeAdapter(Run).validate_python(self._read("run", run_id, 1, project_id))
        if original_run.origin_workspace_id != facts.run.origin_workspace_id:
            raise ValueError("runtime revision workspace cannot be verified")
        receipt = self._read("execution_intent", "run-registration:" + run_id, 1, project_id)
        if (
            receipt.get("schema_version") != "aitest.run-registration-intent/1.0"
            or receipt.get("run_id") != run_id
            or type(receipt.get("snapshot_revision")) is not int
            or receipt.get("snapshot_revision") != 1
            or not isinstance(receipt.get("snapshot_commit_id"), str)
        ):
            raise ValueError("initial runtime registration receipt cannot be verified")
        initial_raw = self._read("execution_facts", receipt["snapshot_commit_id"], 1, project_id)
        initial = ExecutionFacts.model_validate_json(json.dumps(initial_raw), strict=True)
        if (
            execution_payload_digest(initial_raw) != receipt.get("snapshot_digest")
            or initial.run_id != run_id
            or initial.plan_revision != facts.plan_revision
            or initial.run.origin_workspace_id != facts.run.origin_workspace_id
            or initial.runtime_revision_refs
            or initial.run.runtime_revision_refs
            or {step.step_id for step in initial.steps} != {step.step_id for step in facts.steps}
        ):
            raise ValueError(
                "current runtime step set differs from the frozen initial registration"
            )
        effective = {case.case_id: case for case in initial_cases}
        step_refs = {}
        original_steps = {}
        for fact in facts.steps:
            step = TypeAdapter(Step).validate_python(
                self._read("step", fact.step_id, 1, project_id)
            )
            if (step.run_id, step.case_id) != (run_id, fact.case_id):
                raise ValueError("runtime step origin cannot be verified")
            StepContentReader(self.records).read(run=original_run, step=step)
            original_steps[step.step_id] = step
            step_refs[step.step_id] = StepRevisionRefFact(
                step_revision_id=step.step_revision_ref.step_revision_id,
                revision_no=step.step_revision_ref.revision_no,
                digest=step.step_revision_ref.digest,
                inherited=step.step_revision_ref.inherited,
                base_step_revision_id=step.step_revision_ref.base_step_revision_id,
            )
        if {step.step_id: step.step_revision_ref for step in initial.steps} != step_refs:
            raise ValueError("initial step records substitute the registered content")
        levels = {step.step_id: step.level for step in initial.steps}
        driver = initial.run.driver
        self._validate_snapshot_basis(initial, facts)
        if not references:
            if (
                {step.step_id: step.step_revision_ref for step in facts.steps} != step_refs
                or {step.step_id: step.level for step in facts.steps} != levels
                or facts.run.driver != driver
            ):
                raise ValueError("current step content differs from the initial runtime basis")
            return initial_cases
        previous_ref = None
        for ordinal, reference in enumerate(references, 1):
            record = self.read_record(project_id=project_id, reference=reference)
            if (
                record.run_id != run_id
                or record.origin_workspace_id != facts.run.origin_workspace_id
                or record.revision_no != ordinal
                or record.previous_revision_ref != previous_ref
                or record.initial_plan_ref.model_dump() != facts.plan_revision.model_dump()
            ):
                raise ValueError(
                    "runtime revision order, ownership or frozen plan cannot be verified"
                )
            before = self.read_snapshot(
                project_id=project_id, run_id=run_id, reference=record.base_snapshot
            )
            after = self.read_snapshot(
                project_id=project_id, run_id=run_id, reference=record.result_snapshot
            )
            self._validate_snapshot_basis(initial, before)
            self._validate_snapshot_basis(initial, after)
            if (
                before.runtime_revision_refs != references[: ordinal - 1]
                or after.runtime_revision_refs != references[:ordinal]
                or before.run.runtime_revision_refs != before.runtime_revision_refs
                or after.run.runtime_revision_refs != after.runtime_revision_refs
                or record.result_snapshot.snapshot_cursor <= record.base_snapshot.snapshot_cursor
                or {s.step_id: s.step_revision_ref for s in before.steps} != step_refs
                or {s.step_id: s.level for s in before.steps} != levels
                or before.run.driver != driver
            ):
                raise ValueError("runtime revision snapshots do not prove the exact sequence")
            request = request_from_payload(record.request_payload, project_id)
            decision = request_runtime_revision(
                plan=plan,
                cases=tuple(effective.values()),
                confirmations=(),
                request=request,
                facts=before,
                effective_case_revisions=tuple(
                    DomainCaseRevisionRef(
                        case.case_id,
                        case.revision,
                        case_content_digest(case, project_id=project_id),
                    )
                    for case in effective.values()
                )
                if ordinal > 1
                else (),
            )
            if (
                not decision.accepted
                or decision.revision_no != ordinal
                or decision.pause_required != record.pause_required
                or decision.invalidated_basis_step_ids != record.invalidated_basis_step_ids
                or decision.effective_driver.value != record.effective_driver.value
                or after.run.driver != record.effective_driver
            ):
                raise ValueError("saved runtime revision no longer proves its guarded change")
            for change in request.case_changes:
                effective[change.next_case.case_id] = change.next_case
            expected_case_refs = tuple(
                sorted(
                    (
                        CaseRevisionRef(
                            case_id=case.case_id,
                            revision=case.revision,
                            digest=case_content_digest(case, project_id=project_id),
                        )
                        for case in effective.values()
                    ),
                    key=lambda ref: ref.case_id,
                )
            )
            if expected_case_refs != record.effective_case_refs:
                raise ValueError("runtime revision effective case set cannot be verified")
            for ref in expected_case_refs:
                saved_case = case_from_payload(
                    self._read("case", ref.case_id, ref.revision, project_id)
                )
                if saved_case != effective[
                    ref.case_id
                ] or saved_case.assertion_basis.text_digest != (
                    text_digest(saved_case.assertion_basis.text) or ""
                ):
                    raise ValueError("runtime effective case content cannot be verified")
            updates = {change.step_id: change for change in record.step_changes}
            if len(updates) != len(record.step_changes) or set(updates) != set(
                decision.affected_step_ids
            ):
                raise ValueError("runtime revision step changes do not match its exact guard")
            after_steps = {s.step_id: s for s in after.steps}
            for step_id, update in updates.items():
                if step_refs.get(step_id) != update.previous_ref:
                    raise ValueError("runtime revision step content chain is broken")
                step = replace(
                    original_steps[step_id],
                    step_revision_ref=StepRevisionRef(**update.next_ref.model_dump()),
                    level=StepLevel(after_steps[step_id].level.value),
                )
                body = StepContentReader(self.records).read(run=original_run, step=step)
                if (
                    body.case_revision_ref not in expected_case_refs
                    or body.frozen_step.layer != after_steps[step_id].level.value
                ):
                    raise ValueError("runtime step content differs from the effective case basis")
                step_refs[step_id] = update.next_ref
                levels[step_id] = after_steps[step_id].level
            if {s.step_id: s.step_revision_ref for s in after.steps} != step_refs:
                raise ValueError("runtime revision snapshot substitutes step content")
            self._validate_revision_projection(before, after, record)
            if record.pause_required and (
                after.run.control_state.value != "paused"
                or any(s.state.value == "running" for s in after.steps)
                or any(
                    a.state.value
                    in {"intent_recorded", "starting", "running", "stop_requested", "collecting"}
                    for a in after.attempts
                )
            ):
                raise ValueError("runtime revision pause is not an actual step boundary")
            previous_ref = reference
            driver = after.run.driver
        if (
            {s.step_id: s.step_revision_ref for s in facts.steps} != step_refs
            or {s.step_id: s.level for s in facts.steps} != levels
            or facts.run.driver != driver
        ):
            raise ValueError("current step content disagrees with the saved runtime sequence")
        return tuple(effective[case.case_id] for case in initial_cases)

    @staticmethod
    def _validate_snapshot_basis(initial: ExecutionFacts, facts: ExecutionFacts) -> None:
        """Only proven revision links may change driver, content and layer."""
        original = {step.step_id: step for step in initial.steps}
        if set(original) != {step.step_id for step in facts.steps}:
            raise ValueError("runtime snapshot changes the frozen step set")
        normalized = facts.model_copy(
            update={
                "run": facts.run.model_copy(
                    update={
                        "driver": initial.run.driver,
                        "runtime_revision_refs": initial.run.runtime_revision_refs,
                    }
                ),
                "steps": tuple(
                    step.model_copy(
                        update={
                            "step_revision_ref": original[step.step_id].step_revision_ref,
                            "step_revision": original[step.step_id].step_revision,
                            "level": original[step.step_id].level,
                        }
                    )
                    for step in facts.steps
                ),
            }
        )
        validate_frozen_run_basis(initial, normalized)
        validate_frozen_step_basis(initial, normalized)
        if (facts.project_id, facts.run_id, facts.plan_revision) != (
            initial.project_id,
            initial.run_id,
            initial.plan_revision,
        ):
            raise ValueError("runtime snapshot substitutes its initial run or plan")

    @staticmethod
    def _validate_revision_projection(
        before: ExecutionFacts, after: ExecutionFacts, record: RunRevisionRecord
    ) -> None:
        require_runtime_boundary(before)
        seeds = {
            attempt.attempt_id
            for attempt in before.attempts
            if attempt.step_id in record.invalidated_basis_step_ids
        }
        consumers = downstream_consumers(
            {
                attempt.attempt_id: {item.upstream_attempt_id for item in attempt.consumed_outputs}
                | {item.upstream_attempt_id for item in attempt.consumed_conditions}
                for attempt in before.attempts
            },
            tuple(seeds),
        )
        if tuple(sorted(consumers)) != record.invalidated_consumer_attempt_ids:
            raise ValueError("runtime invalidation does not match actual saved consumers")
        affected = seeds | set(consumers)
        expected_attempts = tuple(
            attempt.model_copy(
                update={
                    "state": AttemptStateFact.INVALIDATED,
                    "attempt_revision": attempt.attempt_revision + 1,
                    "unknown_reason_ids": ("runtime_revision_basis_outdated",),
                }
            )
            if attempt.attempt_id in affected
            else attempt
            for attempt in before.attempts
        )
        if (
            after.attempts != expected_attempts
            or after.current_attempt_by_step != before.current_attempt_by_step
        ):
            raise ValueError("runtime revision substitutes saved attempt facts or current identity")
        affected_steps = set(record.invalidated_basis_step_ids) | {
            attempt.step_id
            for attempt in before.attempts
            if attempt.attempt_id in consumers
            and before.current_attempt_by_step.get(attempt.step_id) == attempt.attempt_id
        }
        changes = {change.step_id: change for change in record.step_changes}
        after_steps = {step.step_id: step for step in after.steps}
        for old in before.steps:
            updates: dict[str, object] = {}
            if old.step_id in changes:
                updates.update(
                    step_revision_ref=changes[old.step_id].next_ref,
                    step_revision=changes[old.step_id].next_ref.revision_no,
                    level=after_steps[old.step_id].level,
                )
            if old.step_id in affected_steps:
                updates.update(
                    state=StepStateFact.INVALIDATED,
                    invalidated=True,
                    invalidated_by=record.reference,
                )
            if after_steps[old.step_id] != old.model_copy(update=updates):
                raise ValueError("runtime revision substitutes a step or historical current basis")
        if (
            after.run_revision != before.run_revision + 1
            or after.run.run_revision != after.run_revision
            or after.facts_id != "runtime-revision:" + record.record_id
            or after.run.result_ref is not None
            or after.run.evidence_level is not None
            or after.run.coverage_summary is not None
            or after.run.ended_at is not None
            or after.coverage.executed_attempt_ids
            != tuple(
                identity
                for identity in before.coverage.executed_attempt_ids
                if identity not in affected
            )
        ):
            raise ValueError("runtime revision does not prove its derived result boundary")
        expected_run = before.run.model_copy(
            update={
                "run_revision": before.run_revision + 1,
                "runtime_revision_refs": (*before.runtime_revision_refs, record.reference),
                "driver": record.effective_driver,
                "control_state": RunControlStateFact.PAUSED
                if record.pause_required
                else before.run.control_state,
                "result_ref": None,
                "evidence_level": None,
                "coverage_summary": None,
                "ended_at": None,
            }
        )
        expected_coverage = before.coverage.model_copy(
            update={
                "executed_attempt_ids": tuple(
                    identity
                    for identity in before.coverage.executed_attempt_ids
                    if identity not in affected
                ),
                "invalidated_step_ids": tuple(
                    step.step_id for step in after.steps if step.invalidated
                ),
                "blocked_step_ids": tuple(
                    step.step_id for step in after.steps if step.state is StepStateFact.BLOCKED
                ),
                "unknown_step_ids": tuple(
                    step.step_id
                    for step in after.steps
                    if step.state is StepStateFact.PENDING_VERIFICATION
                ),
            }
        )
        expected_events = tuple(
            DependencyInvalidationFact(
                invalidation_id=f"{record.record_id}:{identity}:{upstream}",
                run_id=record.run_id,
                affected_step_id=next(
                    a.step_id for a in before.attempts if a.attempt_id == identity
                ),
                affected_attempt_id=identity,
                upstream_attempt_id=upstream,
                reason="runtime_revision_basis_outdated",
                source_revision_ref=record.reference,
                transitive=upstream not in seeds,
                invalidated_at=record.created_at,
            )
            for identity, upstreams in consumers.items()
            for upstream in upstreams
        )
        if (
            after.run != expected_run
            or after.coverage != expected_coverage
            or after.dependency_invalidations
            != (*before.dependency_invalidations, *expected_events)
            or after.committed_at != record.created_at
            or after.completeness is not FactCompleteness.PARTIAL
        ):
            raise ValueError(
                "runtime revision substitutes preserved run, coverage or invalidations"
            )
        normalized = after.model_copy(
            update={
                key: getattr(before, key)
                for key in (
                    "facts_id",
                    "snapshot_commit_id",
                    "snapshot_cursor",
                    "snapshot_revision",
                    "committed_at",
                    "run_revision",
                    "runtime_revision_refs",
                    "run",
                    "steps",
                    "attempts",
                    "dependency_invalidations",
                    "coverage",
                    "completeness",
                )
            }
        )
        if normalized != before:
            raise ValueError("runtime revision rewrites preserved execution material")


class RuntimePlanningRecordReader:
    """Adapt the common A record port using the same strict envelope/context checks."""

    def __init__(self, records: SavedRuntimeRevisionReader, project_id: str) -> None:
        self.records, self.project_id = records, project_id

    def read(
        self, *, aggregate_kind: AggregateKind, record_id: str, revision: int
    ) -> CommittedRecord:
        return CommittedRecord(
            aggregate_kind,
            record_id,
            revision,
            self.records._read(aggregate_kind, record_id, revision, self.project_id),
        )
