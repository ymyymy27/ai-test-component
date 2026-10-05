"""Default prepare must not turn self-reported frozen inputs into saved authority."""

from dataclasses import replace

import pytest

from aitest.application.planning.draft import text_digest
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import (
    acceptance_scope_to_payload,
    case_to_payload,
)
from aitest.application.project.serialization import binding_to_payload, project_to_payload
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.prepared_run import (
    AssertionBasisStateFact,
    CaseRevisionRef,
    PlanRevisionRef,
    SnapshotRef,
)
from aitest.domain.planning.plans import AssertionBasisState
from aitest.domain.project.context import BindingForm, LocalProjectBinding
from tests.contracts.test_prepare_run_entrypoint import _parameters
from tests.support.controlled_binding import controlled_binding_save
from tests.support.prepared_run_factory import build_scenario
from tests.unit.test_default_source_analysis import dispatch


@pytest.fixture
def authoritative(tmp_path):
    core = assemble_workspace_core(tmp_path / "workspace", instance_id="authoritative-core")
    source = tmp_path / "tested"
    source.mkdir()
    (source / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    scenario = build_scenario("plain")
    project = replace(scenario.project, workspace_id=core.workspace.workspace_id)
    cases = tuple(
        replace(
            case,
            assertion_basis=replace(
                case.assertion_basis,
                state=AssertionBasisState.PRESENT_UNCONFIRMED,
                text_digest=text_digest(case.assertion_basis.text),
            ),
        )
        for case in scenario.cases
    )
    assert (
        dispatch(
            core,
            "save_context",
            project=project.project_id,
            parameters={"project": project_to_payload(project)},
        ).error
        is None
    )
    binding = LocalProjectBinding(
        "binding-ticket",
        1,
        project.project_id,
        source.as_posix(),
        BindingForm.PLAIN,
        manifest_digest="declared",
        confirmed=True,
    )
    assert (
        controlled_binding_save(
            core,
            project=project.project_id,
            binding=binding_to_payload(binding),
        ).error
        is None
    )
    scope = scenario.plan.scope
    unit = core.unit_of_work
    unit.begin("fixture-material-request", project.project_id, intent_id="fixture-material-intent")
    for case in cases:
        unit.stage_record(
            aggregate_kind="case",
            record_id=case.case_id,
            expected_revision=0,
            payload=case_to_payload(case, project_id=project.project_id),
        )
    unit.stage_record(
        aggregate_kind="acceptance_scope",
        record_id=scope.scope_id,
        expected_revision=0,
        payload=acceptance_scope_to_payload(scope, project_id=project.project_id),
    )
    unit.stage_record(
        aggregate_kind="environment",
        record_id="env-local",
        expected_revision=0,
        payload={
            "project_id": project.project_id,
            "environment_id": "env-local",
            "revision": 1,
            "isolation_mode": "venv",
        },
    )
    unit.commit("fixture-material-request")
    published = dispatch(
        core,
        "publish_plan",
        project=project.project_id,
        parameters={
            "plan_id": scenario.plan.plan_id,
            "revision": 1,
            "scope": acceptance_scope_to_payload(scope, project_id=project.project_id),
            "cases": [case_to_payload(case, project_id=project.project_id) for case in cases],
        },
    )
    assert published.error is None, published.error
    assert published.result["published"], published.result
    pinned = dispatch(
        core,
        "analyze_project",
        project=project.project_id,
        binding_revision=1,
        parameters={
            "binding_id": binding.binding_id,
            "purpose": "prepare",
            "source_scope": ".",
            "selected_paths": ["main.py"],
            "exclusion_rules": [".git"],
        },
    )
    assert pinned.error is None, pinned.error
    snapshot = pinned.result
    plan = unit.repo.read(aggregate_kind="plan", record_id=scenario.plan.plan_id, revision=1)
    saved_source = unit.repo.read(
        aggregate_kind="source_snapshot", record_id=snapshot["snapshot_id"], revision=1
    )
    inputs = replace(
        scenario.inputs,
        workspace_id=core.workspace.workspace_id,
        binding_revision=1,
        selected_paths=("main.py",),
        exclusion_rules=(".git",),
        input_revisions=replace(scenario.inputs.input_revisions, binding_revision=1),
        snapshot=SnapshotRef(
            source_snapshot_id=snapshot["snapshot_id"],
            purpose="prepare",
            content_identity=snapshot["content_identity"],
        ),
        plain_manifest_digest=saved_source.payload["plain_manifest_digest"],
        plan_revision=PlanRevisionRef(
            revision_id=scenario.plan.plan_id, revision_no=1, digest=payload_digest(plan.payload)
        ),
        case_revisions=tuple(CaseRevisionRef(**ref) for ref in published.result["case_revisions"]),
        rule_versions=(),
        template_versions=(),
        assertion_bases=tuple(
            basis.model_copy(
                update={
                    "basis_text_digest": next(
                        c.assertion_basis.text_digest for c in cases if c.case_id == basis.case_id
                    ),
                    "assertion_basis_state": AssertionBasisStateFact.PRESENT_UNCONFIRMED,
                    "confirmation_refs": (),
                }
            )
            for basis in scenario.inputs.assertion_bases
        ),
    )
    try:
        yield core, inputs, source
    finally:
        core.lifetime_lock.release()


def prepare(core, inputs, *, request="prepare-request", intent="prepare-intent"):
    return dispatch(
        core,
        "prepare_run",
        project=inputs.project_id,
        parameters=_parameters(inputs),
        intent=intent,
        request=request,
    )


def test_default_prepare_correct_saved_material_can_be_prepared_and_recalled(authoritative):
    core, inputs, _ = authoritative
    first = prepare(core, inputs)
    assert first.error is None
    assert first.result["status"] == "prepared", first.result
    seq = core.unit_of_work.current_commit_sequence()
    again = prepare(core, inputs, request="prepare-retransmission")
    assert again.error is None
    assert again.result["intent_id"] == first.result["intent_id"]
    assert again.result["created_at_commit"] == first.result["created_at_commit"]
    assert core.unit_of_work.current_commit_sequence() == seq


def test_default_prepared_snapshot_is_saved_atomically_and_recalled_after_restart(authoritative):
    core, inputs, _ = authoritative
    response = prepare(core, inputs)
    assert response.error is None
    result = response.result
    assert core.unit_of_work.repo.current_revision("prepared_run", result["prepared_run_id"]) == 1
    saved = core.unit_of_work.repo.read(
        aggregate_kind="prepared_run", record_id=result["prepared_run_id"], revision=1
    )
    assert dict(saved.payload) == result
    core.lifetime_lock.release()
    restarted = assemble_workspace_core(core.workspace.root, instance_id="restarted-preparer")
    try:
        recalled = prepare(restarted, inputs, request="after-restart")
        assert recalled.error is None
        assert recalled.result == result
    finally:
        restarted.lifetime_lock.release()


def test_changed_actual_source_requires_reprepare_and_does_not_consume_new_intent(authoritative):
    core, inputs, source = authoritative
    (source / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    seq = core.unit_of_work.current_commit_sequence()
    response = prepare(core, inputs)
    assert response.error is None
    assert response.result["status"] == "blocked"
    assert any(
        reason["code"] == "needs_reprepare" for reason in response.result["blocking_reasons"]
    )
    assert core.unit_of_work.current_commit_sequence() == seq


@pytest.mark.parametrize("changed", ["step", "basis", "scope", "workspace", "selection"])
def test_default_prepare_redundant_fields_cannot_override_saved_content(authoritative, changed):
    core, inputs, _ = authoritative
    if changed == "step":
        first = inputs.frozen_cases[0]
        steps = (
            first.steps[0].model_copy(update={"objective": "different action"}),
            *first.steps[1:],
        )
        inputs = replace(
            inputs,
            frozen_cases=(first.model_copy(update={"steps": steps}), *inputs.frozen_cases[1:]),
        )
    elif changed == "basis":
        first = inputs.assertion_bases[0].model_copy(
            update={"basis_text_digest": "sha256:different"}
        )
        inputs = replace(inputs, assertion_bases=(first, *inputs.assertion_bases[1:]))
    elif changed == "scope":
        inputs = replace(inputs, template_required_case_ids=())
    elif changed == "workspace":
        inputs = replace(inputs, workspace_id="another-workspace")
    else:
        inputs = replace(inputs, selected_paths=("different.py",))
    seq = core.unit_of_work.current_commit_sequence()
    result = prepare(core, inputs)
    assert result.error is None, result.error
    assert result.result["status"] == "blocked", result.result
    assert core.unit_of_work.current_commit_sequence() == seq


def test_default_prepare_missing_authority_is_blocked_without_consuming_intent(tmp_path):
    core = assemble_workspace_core(tmp_path, instance_id="prepare-authority-core")
    scenario = build_scenario("plain")
    inputs = replace(scenario.inputs, workspace_id=core.workspace.workspace_id)
    before = core.unit_of_work.current_commit_sequence()
    try:
        response = dispatch(
            core,
            "prepare_run",
            project=inputs.project_id,
            parameters=_parameters(inputs),
            intent="prepare-original",
        )
        assert response.error is None
        assert response.result["status"] == "blocked"
        assert any(
            reason["code"] == "basis_unverified" for reason in response.result["blocking_reasons"]
        )
        assert core.unit_of_work.current_commit_sequence() == before
        assert core.unit_of_work.repo.current_revision("preparation_record", "missing") == 0
    finally:
        core.lifetime_lock.release()
