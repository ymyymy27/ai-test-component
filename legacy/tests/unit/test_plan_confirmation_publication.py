"""One publish_plan confirmation also derives the execution grant and basis links."""

from __future__ import annotations

from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import coverage_contract_to_payload
from aitest.contracts.commands import Command
from aitest.domain.planning.plans import CoverageContract
from aitest.interfaces.local.api import EntryKind, Session
from tests.support.controlled_publication import (
    controlled_publication_save,
    prepare_publication,
)
from tests.unit.test_authoritative_preparation import authoritative as authoritative


def _publication_material(core, inputs):
    repo = core.unit_of_work.repo
    plan = repo.read(
        aggregate_kind="plan",
        record_id=inputs.plan_revision.revision_id,
        revision=1,
    ).payload
    scope = repo.read(
        aggregate_kind="acceptance_scope",
        record_id=plan["scope_id"],
        revision=plan["scope_revision"],
    ).payload
    cases = [
        repo.read(
            aggregate_kind="case",
            record_id=ref["case_id"],
            revision=ref["revision"],
        ).payload
        for ref in plan["case_revisions"]
    ]
    return plan, scope, cases


def _grant_scope() -> dict[str, object]:
    return {
        "allowed_action_classes": ["read_only"],
        "credential_scope_ref": "none",
        "environment_id": "env-local",
        "risk_class": "low",
        "understanding_revision": 1,
    }


def test_publish_plan_with_grant_saves_plan_grant_and_every_basis(authoritative):
    core, inputs, _ = authoritative
    _, scope, cases = _publication_material(core, inputs)
    project = inputs.project_id
    parameters = {
        "plan_id": "plan-with-grant",
        "revision": 1,
        "scope": scope,
        "cases": cases,
        "run_tier": "on_demand",
        "plan_grant": _grant_scope(),
    }

    saved = controlled_publication_save(
        core,
        "publish_plan",
        project=project,
        parameters=parameters,
        request="grant-publication-request",
        intent="grant-publication-intent",
    )
    assert saved.error is None, saved.error
    result = saved.result
    assert result["plan_confirmation_id"]
    assert result["grant_id"]
    assert len(result["basis_confirmation_ids"]) == len(cases)
    assert {item["case_id"] for item in result["basis_confirmations"]} == {
        case["case_id"] for case in cases
    }
    for item in result["basis_confirmations"]:
        assert item["confirmed_at_commit"] == result["approval_commit_seq"]

    repo = core.unit_of_work.repo
    plan = repo.read(
        aggregate_kind="plan",
        record_id="plan-with-grant",
        revision=result["plan_record_revision"],
    ).payload
    assert plan["approval_action"] == "publish_plan"
    assert plan["approval_confirmation_id"] == result["plan_confirmation_id"]
    assert plan["approval_parameters"]["plan_grant"]["allowed_action_classes"] == [
        "read_only"
    ]

    grant = repo.read(
        aggregate_kind="plan_execution_grant",
        record_id=result["grant_id"],
        revision=1,
    ).payload
    assert grant["plan_id"] == "plan-with-grant"
    assert grant["grant"]["plan_revision"] == result["plan_record_revision"]
    assert grant["grant"]["scope_digest"] == payload_digest(dict(scope))

    for confirmation_id in result["basis_confirmation_ids"]:
        link = repo.read(
            aggregate_kind="case_link", record_id=confirmation_id, revision=1
        ).payload
        assert link["plan_confirmation_id"] == result["plan_confirmation_id"]
        assert link["plan_id"] == "plan-with-grant"
        assert link["plan_revision"] == result["plan_record_revision"]


def test_publish_plan_freezes_a_contract_verified_against_saved_understanding(
    authoritative,
):
    from tests.unit.test_default_source_analysis import dispatch

    core, inputs, _ = authoritative
    analyzed = dispatch(
        core,
        "analyze_project",
        project=inputs.project_id,
        expected=1,
        binding_revision=1,
        request="contract-understanding-request",
        intent="contract-understanding-intent",
        parameters={
            "binding_id": inputs.binding_id,
            "purpose": "prepare",
            "source_scope": ".",
            "selected_paths": list(inputs.selected_paths),
            "exclusion_rules": list(inputs.exclusion_rules),
            "include_understanding": True,
        },
    )
    assert analyzed.error is None, analyzed.error
    understanding = analyzed.result["understanding"]
    contract = CoverageContract(
        contract_id=(
            f"plan-coverage-contract:1:{understanding['understanding_id']}:"
            f"{understanding['revision']}"
        ),
        understanding_revision=understanding["revision"],
    )
    _, scope, cases = _publication_material(core, inputs)
    saved = controlled_publication_save(
        core,
        "publish_plan",
        project=inputs.project_id,
        parameters={
            "plan_id": "plan-with-contract",
            "revision": 1,
            "scope": scope,
            "cases": cases,
            "coverage_contract": coverage_contract_to_payload(contract),
            "run_tier": "on_demand",
            "understanding_ref": {
                "understanding_id": understanding["understanding_id"],
                "revision": understanding["revision"],
            },
        },
        request="contract-publication-request",
        intent="contract-publication-intent",
    )
    assert saved.error is None, saved.error
    plan = core.unit_of_work.repo.read(
        aggregate_kind="plan", record_id="plan-with-contract", revision=1
    ).payload
    assert plan["coverage_contract"]["understanding_revision"] == understanding["revision"]
    assert plan["approval_parameters"]["understanding_ref"]["revision"] == (
        understanding["revision"]
    )


def test_changing_grant_scope_after_the_challenge_is_refused(authoritative):
    core, inputs, _ = authoritative
    _, scope, cases = _publication_material(core, inputs)
    project = inputs.project_id
    command = Command(
        action="publish_plan",
        project_id=project,
        request_id="grant-tamper-request",
        intent_id="grant-tamper-intent",
        expected_revision=0,
        parameters={
            "project_revision": 1,
            "expected_revision": 0,
            "plan_id": "plan-grant-tampered",
            "revision": 1,
            "scope": scope,
            "cases": cases,
            "run_tier": "on_demand",
            "plan_grant": _grant_scope(),
        },
    )
    session = Session("grant-tamper-session", EntryKind.HUMAN_UI, True)
    challenge = prepare_publication(core, command, session)
    assert challenge.error is None, challenge.error

    tampered = dict(command.parameters)
    tampered["plan_grant"] = dict(_grant_scope()) | {"risk_class": "high"}
    refused = core.api.dispatch_user_confirmation(
        command.model_copy(
            update={
                "request_id": "grant-tamper-approved",
                "parameters": tampered
                | {"approval_challenge_id": challenge.result["challenge_id"]},
            }
        ),
        session,
        challenge_id=challenge.result["challenge_id"],
        input_digest=challenge.result["basis"]["input_digest"],
    )
    assert refused.error is not None
    assert core.unit_of_work.repo.current_revision(
        aggregate_kind="plan", record_id="plan-grant-tampered"
    ) == 0


def test_retransmitting_the_same_plan_confirmation_returns_the_same_records(authoritative):
    core, inputs, _ = authoritative
    _, scope, cases = _publication_material(core, inputs)
    project = inputs.project_id
    parameters = {
        "plan_id": "plan-with-grant-replay",
        "revision": 1,
        "scope": scope,
        "cases": cases,
        "run_tier": "on_demand",
        "plan_grant": _grant_scope(),
    }
    first = controlled_publication_save(
        core,
        "publish_plan",
        project=project,
        parameters=parameters,
        request="grant-replay-request",
        intent="grant-replay-intent",
    )
    assert first.error is None, first.error
    seq = core.unit_of_work.current_commit_sequence()
    second = controlled_publication_save(
        core,
        "publish_plan",
        project=project,
        parameters=parameters,
        request="grant-replay-request-2",
        intent="grant-replay-intent",
    )
    assert second.error is None, second.error
    assert second.result["plan_confirmation_id"] == first.result["plan_confirmation_id"]
    assert second.result["grant_id"] == first.result["grant_id"]
    assert second.result["basis_confirmation_ids"] == first.result["basis_confirmation_ids"]
    assert core.unit_of_work.current_commit_sequence() == seq


def test_full_publication_without_coverage_contract_is_refused(authoritative):
    core, inputs, _ = authoritative
    _, scope, cases = _publication_material(core, inputs)
    saved = controlled_publication_save(
        core,
        "publish_plan",
        project=inputs.project_id,
        parameters={
            "plan_id": "plan-full-without-contract",
            "revision": 1,
            "scope": scope,
            "cases": cases,
            "run_tier": "full",
        },
        request="full-without-contract-request",
        intent="full-without-contract-intent",
    )
    assert saved.error is not None
    assert core.unit_of_work.repo.current_revision(
        aggregate_kind="plan", record_id="plan-full-without-contract"
    ) == 0


def test_declared_data_mutation_units_must_fit_the_grant_budget(authoritative):
    core, inputs, _ = authoritative
    _, scope, cases = _publication_material(core, inputs)
    parameters = {
        "plan_id": "plan-data-budget",
        "revision": 1,
        "scope": scope,
        "cases": cases,
        "run_tier": "on_demand",
        "plan_grant": _grant_scope() | {"max_data_mutation": 1},
    }
    saved = controlled_publication_save(
        core,
        "publish_plan",
        project=inputs.project_id,
        parameters=parameters,
        request="data-budget-request",
        intent="data-budget-intent",
    )
    assert saved.error is not None
    assert "data mutation" in saved.error.message
    assert core.unit_of_work.repo.current_revision(
        aggregate_kind="plan", record_id="plan-data-budget"
    ) == 1
    from aitest.interfaces.local.api import EntryKind, Session

    state = core.api.dispatch(
        Command(
            action="plan_confirmation_state",
            project_id=inputs.project_id,
            request_id="data-budget-state",
            parameters={"plan_id": "plan-data-budget", "plan_revision": 1},
        ),
        Session("data-budget-state", EntryKind.HUMAN_UI, True),
    )
    assert state.error is None, state.error
    assert state.result["state"] == "derivation_failed"
    assert "data mutation" in state.result["reason"]


def test_plan_confirmation_state_reports_ready_after_derivation(authoritative):
    from aitest.interfaces.local.api import EntryKind, Session

    core, inputs, _ = authoritative
    _, scope, cases = _publication_material(core, inputs)
    saved = controlled_publication_save(
        core,
        "publish_plan",
        project=inputs.project_id,
        parameters={
            "plan_id": "plan-state",
            "revision": 1,
            "scope": scope,
            "cases": cases,
            "run_tier": "on_demand",
            "plan_grant": _grant_scope(),
        },
        request="plan-state-request",
        intent="plan-state-intent",
    )
    assert saved.error is None, saved.error
    state = core.api.dispatch(
        Command(
            action="plan_confirmation_state",
            project_id=inputs.project_id,
            request_id="plan-state-read",
            parameters={"plan_id": "plan-state", "plan_revision": 1},
        ),
        Session("plan-state-read", EntryKind.HUMAN_UI, True),
    )
    assert state.error is None, state.error
    assert state.result["state"] == "ready"
    assert state.result["grant_id"] == saved.result["grant_id"]
    basis_ids = set(saved.result["basis_confirmation_ids"])
    case_rows = {item["case_id"]: item for item in state.result["cases"]}
    assert set(case_rows) == {case["case_id"] for case in cases}
    assert all(
        item["basis_confirmation_id"] in basis_ids for item in case_rows.values()
    )
    assert all(item["case_revision"] >= 1 for item in case_rows.values())
