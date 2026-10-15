"""Build preparation inputs from exact saved identities, not caller-made frozen cases."""

import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aitest.application.planning.preparation import InputRevisions
from aitest.application.planning.prepare_run import PreparationInputs
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
    case_from_payload,
)
from aitest.application.planning.substrate import AggregateKind, RecordReader, read_scoped_record
from aitest.application.project.environment_resolution import EnvironmentResolutionService
from aitest.contracts.prepared_run import (
    AssertionBasisEntry,
    AssertionBasisStateFact,
    BindingFormFact,
    CaseLinks,
    CaseRevisionRef,
    ConfirmationRef,
    ExecutionSourceBinding,
    FrozenCase,
    FrozenCaseStep,
    PlanRevisionRef,
    RuleVersionRef,
    RunDriverFact,
    RunTierFact,
    SnapshotRef,
    TemplateVersionRef,
)
from aitest.domain.planning.plans import AssertionBasisState


class SavedPreparationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["from_saved"]
    project_revision: int = Field(ge=1)
    binding_id: str = Field(min_length=1)
    binding_revision: int = Field(ge=1)
    plan_id: str = Field(min_length=1)
    plan_revision: int = Field(ge=1)
    snapshot_id: str = Field(min_length=1)
    snapshot_revision: int = Field(ge=1)
    environment_id: str = Field(min_length=1)
    environment_revision: int = Field(ge=1)
    client_id: str = Field(min_length=1)
    prepare_request_id: str = Field(min_length=1)
    execution_source: ExecutionSourceBinding
    selected_case_ids: tuple[str, ...] = Field(min_length=1)
    confirmation_refs: tuple[ConfirmationRef, ...] = ()


def from_saved(
    parameters: Mapping[str, object], *, project_id: str, workspace_id: str,
    reader: RecordReader, environments: EnvironmentResolutionService,
) -> PreparationInputs:
    selection = SavedPreparationSelection.model_validate_json(json.dumps(dict(parameters)))

    def read(kind: AggregateKind, identity: str, revision: int) -> dict[str, Any]:
        return dict(read_scoped_record(reader, project_id=project_id, aggregate_kind=kind,
                                       record_id=identity, revision=revision).payload)

    project = read("project", project_id, selection.project_revision)
    if project.get("workspace_id") != workspace_id:
        raise ValueError("saved project belongs to a different workspace")
    binding = read("binding", selection.binding_id, selection.binding_revision)
    plan = read("plan", selection.plan_id, selection.plan_revision)
    source = read("source_snapshot", selection.snapshot_id, selection.snapshot_revision)
    if (source.get("binding_id"), source.get("binding_revision"), source.get("purpose")) != (
        selection.binding_id, selection.binding_revision, "prepare"
    ):
        raise ValueError("saved source does not belong to the selected preparation binding")
    # Model validation checks shapes; original publication/digest/current-source checks
    # still run in the existing prepare handler before any prepared snapshot is admitted.
    refs = tuple(CaseRevisionRef.model_validate(value) for value in plan["case_revisions"])
    scope_revision = int(str(plan["scope_revision"]))
    scope = acceptance_scope_from_payload(
        read("acceptance_scope", str(plan["scope_id"]), scope_revision)
    )
    cases = tuple(case_from_payload(read("case", ref.case_id, ref.revision)) for ref in refs)
    selected = selection.selected_case_ids
    if len(set(selected)) != len(selected) or not set(selected) <= {c.case_id for c in cases}:
        raise ValueError("selected cases must be unique and belong to the saved plan")
    confirmations = selection.confirmation_refs
    if (len({r.confirmation_id for r in confirmations}) != len(confirmations)
            or not {r.case_id for r in confirmations} <= {c.case_id for c in cases}):
        raise ValueError("confirmation references must be unique and belong to the saved plan")
    environment = environments.resolve(
        project_id=project_id, binding_id=selection.binding_id,
        binding_revision=selection.binding_revision, environment_id=selection.environment_id,
        environment_revision=selection.environment_revision,
    )
    execution = selection.execution_source
    execution = execution.model_copy(update={"resolved_input_digest": payload_digest({
        "source": source["content_identity"], "environment": environment.model_dump(mode="json"),
        "plan": {"record_id": selection.plan_id, "digest": payload_digest(plan)},
        "entry": execution.model_dump(mode="json", exclude={"resolved_input_digest"}),
    })})
    rules = tuple(RuleVersionRef.model_validate(v) for v in plan["rule_revisions"])
    templates = tuple(TemplateVersionRef.model_validate(v) for v in plan["template_versions"])
    return PreparationInputs(
        project_id=project_id, workspace_id=workspace_id, scope_id=scope.scope_id,
        binding_id=selection.binding_id, binding_revision=selection.binding_revision,
        binding_form=BindingFormFact(binding["binding_form"]), client_id=selection.client_id,
        prepare_request_id=selection.prepare_request_id,
        input_revisions=InputRevisions(
            selection.project_revision, selection.binding_revision, selection.snapshot_revision,
            selection.environment_revision, selection.plan_revision,
            max((r.revision for r in rules), default=1), 1, scope_revision,
        ),
        snapshot=SnapshotRef(source_snapshot_id=selection.snapshot_id,
                             record_revision=selection.snapshot_revision, purpose="prepare",
                             content_identity=str(source["content_identity"])),
        selected_paths=tuple(source["selected_paths"]),
        environment=environment, execution_source=execution,
        plan_revision=PlanRevisionRef(revision_id=selection.plan_id,
                                      revision_no=selection.plan_revision,
                                      digest=payload_digest(plan)),
        acceptance_scope_revision=scope_revision, rule_versions=rules, template_versions=templates,
        case_revisions=refs,
        frozen_cases=tuple(FrozenCase(
            case_id=case.case_id, revision=case.revision, layer=case.layer.value,
            required=case.case_id in scope.required_case_ids,
            independent_verification=case.independent_verification or "",
            mock_scope=case.mock_scope, importance=case.importance.value,
            steps=tuple(FrozenCaseStep(step_id=f"step-{index}", layer=case.layer.value,
                                       objective=text, expected=case.expected)
                        for index, text in enumerate(case.steps, 1)),
            links=CaseLinks(**{key: tuple(sorted(getattr(case.links, key))) for key in (
                "acceptance_item_ids", "module_ids", "environment_ids", "critical_path_ids",
            )}),
        ) for case in cases),
        assertion_bases=tuple(AssertionBasisEntry(
            case_id=case.case_id, basis_revision=case.assertion_basis.revision,
            basis_text_digest=case.assertion_basis.text_digest,
            assertion_basis_state=(AssertionBasisStateFact.MISSING
                if case.assertion_basis.state is AssertionBasisState.MISSING
                else AssertionBasisStateFact.CONFIRMED
                if any(r.case_id == case.case_id for r in confirmations)
                else AssertionBasisStateFact.PRESENT_UNCONFIRMED),
            confirmation_refs=tuple(r for r in confirmations if r.case_id == case.case_id),
        ) for case in cases),
        exclusion_rules=tuple(source["exclusion_rules"]),
        refetch_dependencies=tuple(source["refetch_dependencies"]),
        git_base_commit=source.get("git_base_commit"),
        git_diff_digest=source.get("git_diff_digest"),
        plain_manifest_digest=source.get("plain_manifest_digest"),
        run_tier=RunTierFact(plan["run_tier"]),
        initial_driver=RunDriverFact(plan["initial_driver"]),
        template_required_case_ids=tuple(sorted(scope.template_case_ids)),
        frozen_required_case_ids=tuple(sorted(scope.required_case_ids)), selected_case_ids=selected,
    )
