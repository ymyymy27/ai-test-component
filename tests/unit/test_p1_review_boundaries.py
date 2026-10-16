"""P1 review regressions: exact saved boundaries; synthetic execution/actors."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aitest.application.review.authoritative_basis import SavedCaseBasisCheck
from aitest.application.review.execution_decision_adapter import (
    DecisionContextFacts,
    assertion_outcomes_from_facts,
    build_decision_facts,
)
from aitest.application.review.reuse_consumption import derive_reuse_outcome
from aitest.application.review.saved_defects import IssueGuardRefused, issue_to_payload
from aitest.application.review.saved_exports import ExportIntentConflict, ExportRefused
from aitest.application.review.saved_reuse import ReuseRevisionConflict
from aitest.contracts.commands import Command
from aitest.contracts.prepared_run import AssertionBasisStateFact
from aitest.domain.planning.plans import AssertionBasisState
from aitest.domain.review.defects import IssueRecord, IssueSeverity, IssueStatus
from aitest.domain.review.reports import BusinessOutcome, evaluate_review
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from tests.unit.test_case_reuse_selection import _command, _harness
from tests.unit.test_frozen_run_plan import saved as saved
from tests.unit.test_reuse_consumption import (
    _attempt,
    _complete_basis,
    _facts,
    _outcome,
    _reference,
    _verification,
    _verified_source,
)
from tests.unit.test_saved_case_basis_proof import material as material
from tests.unit.test_saved_defect_management import (
    _CLOSE_PARAMETERS,
    _evidence,
    _Objects,
    _ready_issue,
)
from tests.unit.test_saved_defect_management import (
    _facts as defect_facts,
)
from tests.unit.test_saved_defect_management import (
    _Harness as Defects,
)
from tests.unit.test_saved_defect_management import (
    _verified_source as defect_source,
)
from tests.unit.test_saved_exports import _export
from tests.unit.test_saved_reports import _create
from tests.unit.test_saved_reports import _Harness as Reports


@pytest.mark.parametrize(
    "action",
    [
        "import_rules",
        "import_rules_markdown",
        "request_model_draft",
        "save_binding",
        "select_case_reuse",
        "future_registered_write",
    ],
)
def test_maintenance_blocks_every_registered_write_alias(action):
    entered = []
    api = LocalAPI(
        instance_id="maintenance",
        maintenance=lambda: "migration active",
        handlers={action: lambda _: entered.append(action) or {"written": True}},
    )
    response = api.dispatch(
        Command(
            action=action,
            request_id="request-write",
            intent_id="intent-write",
            expected_revision=0,
            project_id="project-1",
        ),
        Session("human", EntryKind.INTERACTIVE_CLI, True),
    )
    assert response.error is not None and response.error.code == "maintenance_required"
    assert entered == []


def test_maintenance_keeps_pure_rule_export_available():
    api = LocalAPI(
        instance_id="maintenance",
        maintenance=lambda: "migration active",
        handlers={"export_rules": lambda _: {"content": "saved rules"}},
    )
    result = api.dispatch(
        Command(action="export_rules", request_id="read-rules"),
        Session("reader", EntryKind.INTERACTIVE_CLI, True),
    )
    assert result.error is None


def test_stale_reuse_selection_preserves_record_and_intent(tmp_path):
    core, selection, _ = _harness(tmp_path)
    try:
        original = selection.apply(_command())
        with pytest.raises(ReuseRevisionConflict):
            selection.apply(_command(request_id="stale-request", intent_id="stale-intent"))
        assert core.unit_of_work.repo.current_revision("reuse_decision", original["record_id"]) == 1
        assert selection.apply(_command(request_id="original-replay")) == original
        with pytest.raises(Exception) as changed:
            selection.apply(_command(request_id="changed-expectation", expected_revision=1))
        assert getattr(changed.value, "code", None) == "INTENT_CONFLICT"
    finally:
        core.lifetime_lock.release()


@pytest.mark.parametrize("damage", ["historical", "no_result", "foreign_attempt", "no_proof"])
def test_reuse_does_not_upgrade_unknown_or_unrelated_material(damage):
    source = _facts(
        run_id="run-source",
        attempts=(_attempt(),),
        source_verifications=(_verified_source(),),
        verifications=(_verification(),),
    )
    if damage == "historical":
        source = source.model_copy(
            update={"attempts": (source.attempts[0].model_copy(update={"is_current": False}),)}
        )
    if damage in {"no_result", "foreign_attempt"}:
        verification = source.verifications[0].model_dump(mode="json")
        verification["observation" if damage == "no_result" else "verification_of"] = (
            "no_result" if damage == "no_result" else "another-attempt"
        )
        source = type(source).model_validate(
            {**source.model_dump(mode="json"), "verifications": [verification]}
        )
    if damage == "no_proof":
        outcome = derive_reuse_outcome(
            _facts(),
            (_reference(),),
            source_facts=lambda _: source,
            evidence_readable=lambda _: True,
            basis_confirmed=lambda _: True,
            basis_revision_for=lambda *_: 1,
        )
    else:
        outcome = _outcome(source=source)
    assert not outcome.reused and not outcome.verified and not outcome.passed


def test_real_directory_path_is_not_compared_to_source_content_digest():
    source = _facts(
        run_id="run-source",
        attempts=(_attempt(),),
        source_verifications=(_verified_source("E:/fixed/workdir"),),
        verifications=(_verification(),),
    )
    assert _outcome(source=source).passed == frozenset({"case-1"})


def test_ten_new_executions_and_two_qualified_reuses_share_one_decision():
    cases = tuple(f"case-{number}" for number in range(1, 13))
    steps = tuple((f"target-{number}", case) for number, case in enumerate(cases, 1))
    attempts = tuple(
        _attempt(attempt_id=f"attempt-{number}", run_id="run-target", step_id=f"target-{number}")
        for number in range(1, 11)
    )
    verifications = tuple(
        {
            **_verification((f"evidence-{number}",)),
            "verification_id": f"verification-{number}",
            "verification_of": f"attempt-{number}",
        }
        for number in range(1, 11)
    )
    target = _facts(
        selected=cases,
        case_steps=steps,
        attempts=attempts,
        verifications=verifications,
        source_verifications=(_verified_source(),),
    )
    references = []
    sources = {}
    for number in (11, 12):
        run, step, attempt = f"source-{number}", f"source-step-{number}", f"source-attempt-{number}"
        references.append(
            _reference(
                case_id=cases[number - 1],
                source_run_id=run,
                source_step_id=step,
                source_attempt_id=attempt,
                source_attempt_by_step=((step, attempt),),
                evidence_refs=(f"evidence-{number}",),
            )
        )
        sources[run] = _facts(
            run_id=run,
            selected=(cases[number - 1],),
            case_steps=((step, cases[number - 1]),),
            attempts=(_attempt(attempt_id=attempt, run_id=run, step_id=step),),
            source_verifications=(_verified_source(),),
            verifications=(
                {
                    **_verification((f"evidence-{number}",)),
                    "verification_of": attempt,
                },
            ),
        )
    reuse = derive_reuse_outcome(
        target,
        tuple(references),
        source_facts=sources.get,
        evidence_readable=lambda _: True,
        basis_confirmed=lambda _: True,
        complete_basis=_complete_basis,
        basis_revision_for=lambda *_: 1,
    )
    outcomes = assertion_outcomes_from_facts(
        target, basis_revision_for=lambda _: 1, basis_confirmed_for=lambda _: True
    )
    facts = build_decision_facts(
        target,
        assertion_outcomes=outcomes,
        reuse=reuse,
        context=DecisionContextFacts(template_required_case_ids=frozenset(cases)),
    )
    assert (
        len(facts.coverage.executed),
        len(facts.coverage.reused),
        len(facts.coverage.verified),
        len(facts.coverage.passed),
    ) == (10, 2, 12, 12)
    assert not facts.required_unresolved
    assert evaluate_review(facts).business_outcome is BusinessOutcome.PASSED


@pytest.mark.parametrize("damage", ["failed_claim", "wrong_version", "changed_criteria", "unknown"])
def test_closure_refuses_self_report_and_unproven_original_conditions(tmp_path, damage):
    issue = _ready_issue()
    facts = defect_facts(
        evidence=(_evidence(),),
        verifications=(defect_source(),),
        business_observation="mismatched" if damage == "failed_claim" else "matched",
    )
    if damage == "wrong_version":
        issue = replace(issue, fix_version="unexecuted-version")
    if damage == "changed_criteria":
        issue = replace(issue, closure_criteria="different assertion")
    if damage == "unknown":
        facts = facts.model_copy(update={"verifications": ()})
    harness = Defects(tmp_path, facts=facts, objects=_Objects())
    with pytest.raises(IssueGuardRefused):
        harness.service._closure_evidence(issue, dict(_CLOSE_PARAMETERS))


@pytest.mark.parametrize("changed", ["expected", "basis", "rules"])
def test_regression_must_keep_the_original_closed_standard(tmp_path, changed):
    from tests.support.prepared_run_factory import build_scenario

    original = build_scenario("plain").cases[0]
    current = original
    current_rules = "rules@1"
    if changed == "expected":
        current = replace(original, expected="different acceptance")
    elif changed == "basis":
        current = replace(
            original,
            assertion_basis=replace(
                original.assertion_basis,
                text_digest="different-basis",
            ),
        )
    else:
        current_rules = "rules@2"
    service = Defects(tmp_path, facts=defect_facts()).service
    service._attempt_case = Mock(side_effect=[(original, "rules@1"), (current, current_rules)])
    with pytest.raises(IssueGuardRefused, match="original closure standard"):
        service._original_criteria(
            _ready_issue(),
            {"run_id": "old-run", "attempt_id": "old-attempt", "case_id": original.case_id},
            dict(_CLOSE_PARAMETERS),
        )


def _save_issue(harness, identity="issue-blocking", *, case=None):
    issue = IssueRecord(
        issue_id=identity,
        project_id=harness.facts.project_id,
        revision=1,
        title="blocking defect",
        status=IssueStatus.CONFIRMED,
        severity=IssueSeverity.P1,
        owner="owner",
        confirmed_by="session:user",
    )
    request = "seed-" + identity
    harness.unit.begin(request, issue.project_id, intent_id=request + "-intent")
    harness.unit.stage_record(
        aggregate_kind="issue",
        record_id=identity,
        expected_revision=0,
        payload=issue_to_payload(
            issue,
            updated_sequence=1,
            open_state=True,
            blocking="blocking",
            links={
                "module_ids": [],
                "layers": [],
                "case_id": case,
                "run_id": None,
                "attempt_id": None,
                "regressions": [],
            },
        ),
    )
    harness.unit.commit(request)


def test_report_cannot_omit_saved_blocking_issues(tmp_path):
    harness = Reports(tmp_path)
    _save_issue(harness)
    derive = Mock(wraps=harness.service._decision)
    harness.service._decision = derive
    _create(harness, issue_ids=[])
    record = harness.saved_report("report-1")
    assert record["issue_revisions"] == [["issue-blocking", 1]]
    assert derive.call_args.kwargs["blocking_issue_ids"] == frozenset({"issue-blocking"})


def test_export_original_intent_survives_a_new_report_and_records_new_intents(tmp_path):
    harness = Reports(tmp_path)
    _create(harness)
    first = _export(harness, review_ids=[])
    new_intent = _export(
        harness, review_ids=[], intent_id="another-export", request_id="another-request"
    )
    assert new_intent == first
    _create(harness, expected=1, intent_id="second-report", request_id="second-report-request")
    assert _export(harness, review_ids=[], request_id="replay-original") == first
    assert (
        _export(harness, review_ids=[], intent_id="another-export", request_id="replay-another")
        == first
    )
    with pytest.raises(ExportIntentConflict):
        _export(harness, kind="evidence_bundle", review_ids=[], request_id="changed-command")


def test_export_never_substitutes_latest_or_foreign_run_evidence(tmp_path):
    harness = Reports(tmp_path, evidence=("evidence-1",))
    _create(harness)
    original = harness.saved_report("report-1")["execution_snapshot_ref"]
    harness.save_facts(harness.facts.model_copy(update={"evidence_refs": ()}))
    _export(harness, review_ids=[], kind="evidence_bundle", attachment_refs=["evidence-1"])
    assert harness.saved_report("report-1")["execution_snapshot_ref"] == original
    with pytest.raises(ExportRefused):
        _export(
            harness,
            review_ids=[],
            run_id="foreign-run",
            intent_id="foreign",
            request_id="foreign-request",
        )


def test_effective_confirmation_ignores_frozen_label_and_keeps_bytes(material, saved, monkeypatch):
    reader, records, confirmation, _, project = material
    prepared, _, repository, scenario = saved
    check = SavedCaseBasisCheck(
        reader=Mock(),
        records=repository,
        workspace_id=prepared.workspace_id,
        basis_proof=reader.proof,
    )
    check._bases = reader
    pending = prepared.model_copy(
        update={
            "assertion_bases": tuple(
                entry.model_copy(
                    update={
                        "assertion_basis_state": AssertionBasisStateFact.PRESENT_UNCONFIRMED,
                        "confirmation_refs": (),
                    }
                )
                for entry in prepared.assertion_bases
            )
        }
    )
    original = pending.model_dump_json()
    check._preparation = lambda *_: pending
    from tests.unit.test_saved_reports import _facts as basis_facts

    facts = basis_facts(pending, cases=(confirmation.case_id,)).model_copy(
        update={
            "runtime_revision_refs": ("controlled-runtime-reference",),
        }
    )
    # Isolate effective-case proof; strict saved revision/origin has separate suites.
    monkeypatch.setattr(
        "aitest.application.review.authoritative_basis.SavedRuntimeRevisionReader",
        lambda _: SimpleNamespace(
            read_effective_cases=lambda **_: scenario.cases,
            read_record=lambda **_: SimpleNamespace(
                run_id=facts.run_id,
                origin_workspace_id=prepared.workspace_id,
                confirmation_ids=(confirmation.confirmation_id,),
            ),
        ),
    )
    assert not check(project, pending.intent_id, confirmation.case_id)
    assert check.for_facts(facts, confirmation.case_id)
    assert check.revision(project, pending.intent_id, confirmation.case_id, facts=facts) == 1
    assert pending.model_dump_json() == original
    reader.proof.validate_basis_confirmation.assert_called()
    changed = replace(
        scenario.cases[0],
        assertion_basis=replace(
            scenario.cases[0].assertion_basis,
            revision=2,
            state=AssertionBasisState.PRESENT_UNCONFIRMED,
        ),
    )
    scenario_cases = (changed, *scenario.cases[1:])
    monkeypatch.setattr(
        "aitest.application.review.authoritative_basis.SavedRuntimeRevisionReader",
        lambda _: SimpleNamespace(
            read_effective_cases=lambda **_: scenario_cases,
            read_record=lambda **_: SimpleNamespace(
                run_id=facts.run_id,
                origin_workspace_id=prepared.workspace_id,
                confirmation_ids=(confirmation.confirmation_id,),
            ),
        ),
    )
    assert not check.for_facts(facts, confirmation.case_id)
