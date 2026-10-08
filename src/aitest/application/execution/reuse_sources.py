"""Whole-case historic provenance; readable material alone never grants reuse."""

from collections.abc import Mapping
from dataclasses import dataclass

from aitest.application.execution.checkpoint_refs import (
    read_checkpoint_refs,
    read_referenced_checkpoint,
)
from aitest.application.execution.facts import _run_fact
from aitest.application.execution.registration import _initial_domain
from aitest.application.execution.reuse_material import validate_source_material
from aitest.application.execution.run_record import read_run_record
from aitest.application.execution.runtime_revision import (
    SavedRuntimeRevisionReader,
    SnapshotContentRef,
)
from aitest.application.execution.step_content import StepContent, StepContentReader
from aitest.application.planning.preparation_origin import (
    load_saved_preparation,
    validate_preparation_origin,
)
from aitest.application.planning.serialization import case_to_payload
from aitest.application.planning.substrate import RecordReader
from aitest.application.ports import EvidenceObjectStore, RecordRepository, SpoolStore
from aitest.contracts.execution_facts import AttemptFact, ExecutionFacts, StepFact
from aitest.contracts.prepared_run import CaseRevisionRef, PreparedRun
from aitest.domain.execution.runs import RecoveryRecord, Step, StepLevel, StepRevisionRef
from aitest.domain.json_material import require_json_text


@dataclass(frozen=True, slots=True)
class CaseReuseStepSource:
    step: StepFact
    attempt: AttemptFact | None
    content: StepContent
    checkpoint: RecoveryRecord | None = None


@dataclass(frozen=True, slots=True)
class CaseReuseSource:
    reference: SnapshotContentRef
    facts: ExecutionFacts
    original_preparation: PreparedRun
    case_revision: CaseRevisionRef
    steps: tuple[CaseReuseStepSource, ...]


class CaseReuseSourceReader:
    def __init__(
        self, records: RecordRepository, preparation_reader: RecordReader,
        *, objects: EvidenceObjectStore | None = None,
        spool: SpoolStore | None = None,
    ) -> None:
        self.records, self.preparations = records, preparation_reader
        self.objects = objects
        self.spool = spool

    def read(
        self, *, project_id: str, run_id: str, case_id: str, reference: SnapshotContentRef
    ) -> CaseReuseSource:
        """Read one source run and the complete local mapping, without latest fallback."""
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("case reuse source requires a nonempty case identity")
        require_json_text(case_id)
        facts = SavedRuntimeRevisionReader(self.records).read_snapshot(
            project_id=project_id, run_id=run_id, reference=reference
        )
        if (
            case_id not in facts.run.selected_scope
            or case_id not in facts.coverage.selected_case_ids
        ):
            raise ValueError("case reuse source is outside the selected source scope")
        record = self.records.read(aggregate_kind="run", record_id=run_id, revision=1)
        if (
            getattr(record, "aggregate_kind", None), getattr(record, "record_id", None),
            getattr(record, "revision", None),
        ) != ("run", run_id, 1) or type(getattr(record, "revision", None)) is not int:
            raise ValueError("case reuse original run envelope cannot be verified")
        run = read_run_record(getattr(record, "payload", {}))
        if (run.project_id, run.run_id, run.origin_workspace_id) != (
            project_id, run_id, facts.run.origin_workspace_id,
        ) or len(run.frozen_input_refs) != 1:
            raise ValueError("case reuse original run or preparation identity differs")
        origin = run.frozen_input_refs[0]
        prepared, digest = load_saved_preparation(
            reader=self.preparations, project_id=project_id,
            workspace_id=run.origin_workspace_id, prepared_run_id=origin.input_ref_id,
        )
        validate_preparation_origin(prepared, reader=self.preparations)
        expected_run, initial_steps = _initial_domain(prepared, run_id, digest)
        if run != expected_run or facts.plan_revision.model_dump() != (
            prepared.plan_revision.model_dump()
        ):
            raise ValueError("case reuse original run differs from its exact preparation")
        origin_fact = _run_fact(run)
        if any(
            getattr(facts.run, field) != getattr(origin_fact, field)
            for field in (
                "origin_workspace_id", "intent_id", "tier", "conclusion_ceiling",
                "plan_revision", "environment_ref", "environment_isolation_mode",
                "rules_revision", "required_scope", "selected_scope",
            )
        ) or (
            tuple(sorted(facts.coverage.selected_case_ids)) != origin_fact.selected_scope
            or tuple(sorted(facts.coverage.mandatory_case_ids)) != origin_fact.required_scope
        ):
            raise ValueError("case reuse snapshot differs from its original frozen run basis")
        originals = {step.step_id: step for step in initial_steps if step.case_id == case_id}
        positions = {identity: index for index, identity in enumerate(originals)}
        selected = tuple(step for step in facts.steps if step.case_id == case_id)
        if not originals or {step.step_id for step in selected} != set(originals):
            raise ValueError("case reuse source does not contain the complete original step layout")
        frozen = next(case for case in prepared.frozen_cases if case.case_id == case_id)
        attempts = {attempt.attempt_id: attempt for attempt in facts.attempts}
        checkpoint_refs = read_checkpoint_refs(self.records, facts) if facts.attempts else {}
        contents = StepContentReader(self.records)
        result = []
        for fact in selected:
            checkpoint = None
            if fact.current_attempt_id is not None:
                checkpoint = read_referenced_checkpoint(
                    self.records, checkpoint_refs[fact.current_attempt_id],
                    attempts[fact.current_attempt_id], facts,
                )
            original = originals[fact.step_id]
            if (fact.ordinal, fact.required_for_case) != (
                original.ordinal, original.required_for_case,
            ):
                raise ValueError("case reuse source changed original step identity or requirement")
            step = Step(
                step_id=fact.step_id, run_id=run_id, ordinal=fact.ordinal, case_id=case_id,
                level=StepLevel(fact.level.value),
                step_revision_ref=StepRevisionRef(**fact.step_revision_ref.model_dump()),
            )
            body = contents.read(run=run, step=step)
            if (
                body.case_step_index != positions[fact.step_id]
                or body.case_step_index >= len(frozen.steps)
                or body.frozen_step.step_id != frozen.steps[body.case_step_index].step_id
                or body.prepared_run_ref.prepared_run_id != prepared.prepared_run_id
                or body.prepared_run_ref.digest != digest
            ):
                raise ValueError("case reuse source step differs from the original local mapping")
            result.append(CaseReuseStepSource(
                step=fact,
                attempt=attempts[fact.current_attempt_id]
                if fact.current_attempt_id is not None else None,
                content=body,
                checkpoint=checkpoint,
            ))
        result.sort(key=lambda item: item.content.case_step_index)
        first = result[0].content
        if (
            [item.content.case_step_index for item in result] != list(range(len(frozen.steps)))
            or len(result) != len(first.checked_case().steps)
            or any(
                item.content.case_revision_ref != first.case_revision_ref
                or item.content.case_content != first.case_content
                for item in result
            )
        ):
            raise ValueError("case reuse source mixes or omits exact whole-case content")
        case_ref = first.case_revision_ref
        saved_case = self.records.read(
            aggregate_kind="case", record_id=case_ref.case_id, revision=case_ref.revision
        )
        raw_case = getattr(saved_case, "payload", None)
        if (
            getattr(saved_case, "aggregate_kind", None), getattr(saved_case, "record_id", None),
            getattr(saved_case, "revision", None),
        ) != ("case", case_ref.case_id, case_ref.revision) or (
            type(getattr(saved_case, "revision", None)) is not int
            or not isinstance(raw_case, Mapping)
            or dict(raw_case) != case_to_payload(first.checked_case(), project_id=project_id)
        ):
            raise ValueError("case reuse source differs from its exact saved case body")
        source = CaseReuseSource(
            reference=reference, facts=facts, original_preparation=prepared,
            case_revision=first.case_revision_ref, steps=tuple(result),
        )
        validate_source_material(source, self.objects, self.spool)
        return source
