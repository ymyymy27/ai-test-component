"""Real file transactions for successive revisions, exact replay and restart reads.

The paused fixture is a controlled projection, not real Trae/start acceptance.
"""

from dataclasses import replace

import pytest

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.runtime_revision import SavedRuntimeRevisionReader
from aitest.application.execution.runtime_revision_service import RuntimeRevisionService
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
    case_from_payload,
    case_to_payload,
)
from aitest.contracts.execution_facts import RunControlStateFact
from aitest.domain.planning.plans import (
    CaseRevisionRef,
    Plan,
    PlanPublicationStatus,
    RuleRevisionRef,
    RunDriver,
    RunTier,
    TemplateVersionRef,
)
from aitest.domain.planning.runtime_revision import (
    CaseRuntimeChange,
    RuntimeRevisionRefused,
    RuntimeRevisionRequest,
)
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_default_source_analysis import dispatch
from tests.unit.test_initial_run_registration import register, service


def initial_basis(core, inputs):
    prepared = prepare(core, inputs).result
    facts = register(core, prepared)
    paused = facts.model_copy(
        update={
            "run_revision": 2,
            "run": facts.run.model_copy(
                update={
                    "run_revision": 2,
                    "control_state": RunControlStateFact.PAUSED,
                }
            ),
        }
    )
    unit = core.unit_of_work
    unit.begin("controlled-pause", inputs.project_id, intent_id="controlled-pause-intent")
    coordinator = ExecutionCommitCoordinator(unit, records=unit.repo)
    _, paused = coordinator._stage_snapshot(paused)
    unit.commit("controlled-pause")
    raw = unit.repo.read(
        aggregate_kind="plan",
        record_id=paused.plan_revision.revision_id,
        revision=paused.plan_revision.revision_no,
    ).payload
    scope = acceptance_scope_from_payload(
        unit.repo.read(
            aggregate_kind="acceptance_scope",
            record_id=raw["scope_id"],
            revision=raw["scope_revision"],
        ).payload
    )
    plan = Plan(
        plan_id=raw["plan_id"],
        revision=raw["revision"],
        scope=scope,
        case_revisions=tuple(CaseRevisionRef(**ref) for ref in raw["case_revisions"]),
        rule_revisions=tuple(RuleRevisionRef(**ref) for ref in raw["rule_revisions"]),
        template_versions=tuple(TemplateVersionRef(**ref) for ref in raw["template_versions"]),
        run_tier=RunTier(raw["run_tier"]),
        initial_driver=RunDriver(raw["initial_driver"]),
        status=PlanPublicationStatus.PUBLISHED,
        confirmation_id="saved-publication-view",
        record_revision=paused.plan_revision.revision_no,
    )
    cases = tuple(
        case_from_payload(
            unit.repo.read(
                aggregate_kind="case",
                record_id=ref.case_id,
                revision=ref.revision,
            ).payload
        )
        for ref in plan.case_revisions
    )
    return plan, cases, paused


def saved_next_case(core, inputs, case, *, expected=None):
    response = dispatch(
        core,
        "save_case",
        project=inputs.project_id,
        request=f"save-{case.case_id}-{case.revision}-request",
        intent=f"save-{case.case_id}-{case.revision}",
        expected=case.revision - 1 if expected is None else expected,
        parameters={"case": case_to_payload(case, project_id=inputs.project_id)},
    )
    assert response.error is None, response.error
    return case


def revision_request(facts, case, **updates):
    return RuntimeRevisionRequest(
        base_plan_revision_id=facts.plan_revision.revision_id,
        base_plan_revision_no=facts.plan_revision.revision_no,
        base_plan_revision_digest=facts.plan_revision.digest,
        observed_snapshot_cursor=facts.snapshot_cursor,
        case_changes=(CaseRuntimeChange(case),),
        reason="controlled pending content revision",
        operator_ref="controlled-component-fixture",
        **updates,
    )


def apply(
    core, plan, facts, request, *, intent="runtime-revision-intent", request_id="rev-request"
):
    origin = service(core)
    return RuntimeRevisionService(
        unit=core.unit_of_work,
        records=core.unit_of_work.repo,
        approvals=origin.approvals,
        controlled_writes=origin.controlled_writes,
    ).apply(
        project_id=facts.project_id,
        run_id=facts.run_id,
        plan=plan,
        request=request,
        intent_id=intent,
        request_id=request_id,
    )


def test_successive_revisions_reject_version_replay_and_survive_restart(authoritative):
    from aitest.bootstrap import assemble_workspace_core

    core, inputs, _ = authoritative
    plan, cases, before = initial_basis(core, inputs)
    case2 = saved_next_case(
        core,
        inputs,
        replace(cases[0], revision=2, steps=tuple(text + " updated" for text in cases[0].steps)),
    )
    request1 = revision_request(before, case2, requested_driver=RunDriver.STEPWISE)
    first = apply(core, plan, before, request1)
    assert first.run.driver.value == "stepwise"
    assert first.plan_revision == before.plan_revision
    assert first.run.required_scope == before.run.required_scope
    assert first.run.selected_scope == before.run.selected_scope
    assert len(first.runtime_revision_refs) == 1
    sequence = core.unit_of_work.current_commit_sequence()
    assert apply(core, plan, before, request1, request_id="retransmission") == first
    assert core.unit_of_work.current_commit_sequence() == sequence
    with pytest.raises(ValueError, match="conflicts"):
        apply(core, plan, first, replace(request1, reason="different input"))
    with pytest.raises(RuntimeRevisionRefused):
        apply(core, plan, first, revision_request(first, case2), intent="duplicate-version")
    case3 = saved_next_case(
        core,
        inputs,
        replace(case2, revision=3, steps=tuple(text + " again" for text in case2.steps)),
    )
    second = apply(core, plan, first, revision_request(first, case3), intent="second-revision")
    assert len(second.runtime_revision_refs) == 2
    assert second.runtime_revision_refs[0] == first.runtime_revision_refs[0]
    reader = SavedRuntimeRevisionReader(core.unit_of_work.repo)
    effective = reader.read_effective_cases(facts=second, plan=plan, initial_cases=cases)
    assert effective == (case3, *cases[1:])
    one, two = tuple(
        reader.read_record(project_id=inputs.project_id, reference=ref)
        for ref in second.runtime_revision_refs
    )
    assert (one.revision_no, two.revision_no) == (1, 2)
    assert two.previous_revision_ref == one.reference
    assert all(change.next_ref.revision_no == 1 for change in two.step_changes)
    assert all(
        change.previous_ref
        == next(old.next_ref for old in one.step_changes if old.step_id == change.step_id)
        for change in two.step_changes
    )
    core.lifetime_lock.release()
    restarted = assemble_workspace_core(core.workspace.root, instance_id="runtime-restarted")
    try:
        current = ExecutionCommitCoordinator(
            restarted.unit_of_work, records=restarted.unit_of_work.repo
        ).read_runtime_revision_facts(project_id=inputs.project_id, run_id=second.run_id)
        assert current == second
        assert (
            SavedRuntimeRevisionReader(restarted.unit_of_work.repo).read_effective_cases(
                facts=current, plan=plan, initial_cases=cases
            )
            == effective
        )
        assert apply(restarted, plan, before, request1, request_id="restarted-replay") == first
    finally:
        restarted.lifetime_lock.release()


def test_runtime_revision_stage_failures_leave_original_pointer_and_content(
    authoritative, monkeypatch
):
    core, inputs, _ = authoritative
    plan, cases, before = initial_basis(core, inputs)
    case2 = saved_next_case(core, inputs, replace(cases[0], revision=2))
    request = revision_request(before, case2)
    sequence = core.unit_of_work.current_commit_sequence()
    original = core.unit_of_work.stage_record
    for kind in (
        "step_revision",
        "run_plan_revision",
        "execution_facts_current",
        "execution_facts",
    ):

        def fail(failing_kind=kind, **kwargs):
            if kwargs["aggregate_kind"] == failing_kind:
                raise OSError("controlled runtime publication failure")
            return original(**kwargs)

        monkeypatch.setattr(core.unit_of_work, "stage_record", fail)
        with pytest.raises(OSError, match="controlled"):
            apply(core, plan, before, request, request_id="failed-" + kind)
        assert core.unit_of_work.current_commit_sequence() == sequence
        assert (
            ExecutionCommitCoordinator(
                core.unit_of_work, records=core.unit_of_work.repo
            ).read_current_facts(project_id=inputs.project_id, run_id=before.run_id)
            == before
        )
    monkeypatch.setattr(core.unit_of_work, "stage_record", original)
    after = apply(core, plan, before, request, request_id="controlled-retry")
    assert len(after.runtime_revision_refs) == 1


def test_committed_runtime_response_loss_recalls_original_result(authoritative, monkeypatch):
    core, inputs, _ = authoritative
    plan, cases, before = initial_basis(core, inputs)
    case2 = saved_next_case(core, inputs, replace(cases[0], revision=2))
    request = revision_request(before, case2)
    original = core.unit_of_work.commit

    def lose_response(request_id):
        original(request_id)
        raise OSError("controlled runtime response lost")

    monkeypatch.setattr(core.unit_of_work, "commit", lose_response)
    with pytest.raises(OSError, match="response lost"):
        apply(core, plan, before, request)
    monkeypatch.setattr(core.unit_of_work, "commit", original)
    sequence = core.unit_of_work.current_commit_sequence()
    after = apply(core, plan, before, request, request_id="response-replay")
    assert len(after.runtime_revision_refs) == 1
    assert core.unit_of_work.current_commit_sequence() == sequence
