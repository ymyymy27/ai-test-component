"""Immutable, independently readable step content behind a frozen StepRevisionRef."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import (
    case_content_digest,
    case_from_payload,
    case_to_payload,
)
from aitest.application.planning.substrate import RecordReader
from aitest.application.ports import RecordRepository
from aitest.contracts.prepared_run import (
    CaseRevisionRef,
    FrozenCaseStep,
    PlanRevisionRef,
    PreparedRun,
)
from aitest.domain.execution.runs import Run, Step, StepRevisionRef
from aitest.domain.planning.plans import Case


class PreparedContentRef(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    prepared_run_id: str = Field(min_length=1)
    record_revision: int = Field(ge=1, le=1)
    digest: str = Field(min_length=1)


class StepContent(BaseModel):
    """Internal storage shape; it does not add a public execution capability."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["aitest.step-content/1.0"]
    project_id: str = Field(min_length=1)
    origin_workspace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    prepared_run_ref: PreparedContentRef
    plan_revision_ref: PlanRevisionRef
    case_revision_ref: CaseRevisionRef
    case_step_index: int = Field(ge=0)
    frozen_step: FrozenCaseStep
    case_content: dict[str, Any]

    def checked_case(self) -> Case:
        case = case_from_payload(self.case_content)
        if (
            self.case_content != case_to_payload(case, project_id=self.project_id)
            or self.case_revision_ref.case_id != case.case_id
            or self.case_revision_ref.revision != case.revision
            or self.case_revision_ref.digest
            != case_content_digest(case, project_id=self.project_id)
            or self.case_step_index >= len(case.steps)
            or self.frozen_step.objective != case.steps[self.case_step_index]
            or self.frozen_step.expected != case.expected
            or self.frozen_step.layer != case.layer.value
        ):
            raise ValueError("step content does not prove the exact case and local step")
        return case


@dataclass(frozen=True, slots=True)
class FrozenStepContent:
    step: Step
    content: StepContent


def freeze_initial_step_contents(
    *, prepared: PreparedRun, run: Run, steps: tuple[Step, ...], reader: RecordReader
) -> tuple[FrozenStepContent, ...]:
    """Read exact B case revisions and freeze the entire body for each selected step."""
    refs = {ref.case_id: ref for ref in prepared.case_revisions}
    result: list[FrozenStepContent] = []
    for frozen in prepared.frozen_cases:
        if frozen.case_id not in run.selected_scope:
            continue
        ref = refs[frozen.case_id]
        record = reader.read(aggregate_kind="case", record_id=ref.case_id, revision=ref.revision)
        if (record.aggregate_kind, record.record_id, record.revision) != (
            "case", ref.case_id, ref.revision
        ) or type(record.revision) is not int:
            raise ValueError("saved case envelope differs from its exact reference")
        for index, item in enumerate(frozen.steps):
            if len(result) >= len(steps):
                raise ValueError("frozen content and initial steps differ")
            step = steps[len(result)]
            if (step.run_id, step.case_id, step.level.value) != (
                run.run_id, ref.case_id, item.layer
            ):
                raise ValueError("frozen content and initial step identity differ")
            body = StepContent(
                schema_version="aitest.step-content/1.0",
                project_id=run.project_id,
                origin_workspace_id=run.origin_workspace_id,
                run_id=run.run_id,
                step_id=step.step_id,
                prepared_run_ref=PreparedContentRef(
                    prepared_run_id=prepared.prepared_run_id,
                    record_revision=1,
                    digest=run.frozen_input_refs[0].value_digest,
                ),
                plan_revision_ref=prepared.plan_revision,
                case_revision_ref=ref,
                case_step_index=index,
                frozen_step=item,
                case_content=dict(record.payload),
            )
            body.checked_case()
            reference = StepRevisionRef(
                step_revision_id="step-revision-"
                + payload_digest(
                    [run.run_id, prepared.prepared_run_id, ref.case_id, item.step_id]
                )[7:],
                revision_no=1,
                digest=payload_digest(body.model_dump(mode="json")),
            )
            result.append(FrozenStepContent(replace(step, step_revision_ref=reference), body))
    if len(result) != len(steps):
        raise ValueError("frozen content and initial steps differ")
    return tuple(result)


class StepContentReader:
    def __init__(self, records: RecordRepository) -> None:
        self.records = records

    def read(self, *, run: Run, step: Step) -> StepContent:
        """Never substitute current/latest content for a missing historic reference."""
        reference = step.step_revision_ref
        if type(reference.revision_no) is not int or reference.revision_no < 1:
            raise ValueError("step content reference has an invalid warehouse revision")
        record = self.records.read(
            aggregate_kind="step_revision", record_id=reference.step_revision_id,
            revision=reference.revision_no,
        )
        if (
            getattr(record, "aggregate_kind", None) != "step_revision"
            or getattr(record, "record_id", None) != reference.step_revision_id
            or type(getattr(record, "revision", None)) is not int
            or getattr(record, "revision", None) != reference.revision_no
        ):
            raise ValueError("step content envelope differs from its exact reference")
        payload = getattr(record, "payload", None)
        if not isinstance(payload, Mapping) or payload_digest(dict(payload)) != reference.digest:
            raise ValueError("step content digest cannot be verified")
        body = StepContent.model_validate_json(json.dumps(dict(payload)), strict=True)
        if (
            (body.project_id, body.origin_workspace_id, body.run_id, body.step_id)
            != (run.project_id, run.origin_workspace_id, run.run_id, step.step_id)
            or step.run_id != run.run_id
            or body.case_revision_ref.case_id != step.case_id
            or body.frozen_step.layer != step.level.value
            or body.plan_revision_ref.model_dump()
            != {"revision_id": run.plan_revision_ref.revision_id,
                "revision_no": run.plan_revision_ref.revision_no,
                "digest": run.plan_revision_ref.digest}
            or not any(
                item.input_ref_id == body.prepared_run_ref.prepared_run_id
                and item.value_ref == f"prepared_run:{item.input_ref_id}@1"
                and item.value_digest == body.prepared_run_ref.digest
                and item.resolved
                for item in run.frozen_input_refs
            )
        ):
            raise ValueError("step content belongs to a different frozen run basis")
        body.checked_case()
        return body
