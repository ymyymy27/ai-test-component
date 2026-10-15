"""A saved report supplies evidence, never the developer's completion declaration."""

from dataclasses import replace

import pytest

from aitest.application.project.delivery_verification import (
    DeliveryVerificationReader,
    delivery_projection,
)
from aitest.domain.review.reports import Coverage
from tests.support.prepared_run_factory import PROJECT_ID
from tests.unit.test_saved_reports import _create, _Harness


def test_report_projection_requires_matching_source_and_new_execution(tmp_path, monkeypatch):
    import aitest.application.project.delivery_verification as module

    harness = _Harness(tmp_path, evidence=("evidence-1",))
    _create(harness)
    reader = DeliveryVerificationReader(harness.service, harness.repository, harness.objects)
    acceptance = tuple(sorted({item for case in harness.prepared.frozen_cases
                               for item in case.links.acceptance_item_ids}))
    source = harness.prepared.snapshot.content_identity
    projection, material = reader.read(PROJECT_ID, "report-1", 1, source, acceptance)
    assert projection["verified_in_scope"] == []
    assert projection["unverified_scope"] == list(acceptance)
    assert material.record_id == "report-1"
    with pytest.raises(ValueError, match="different source"):
        reader.read(PROJECT_ID, "report-1", 1, "different", acceptance)
    with pytest.raises(ValueError, match="identity"):
        reader.read("foreign", "report-1", 1, source, acceptance)

    # Coverage mapping fixture only; this does not turn this report into real execution.
    original = module.report_from_payload
    cases = frozenset(harness.cases)
    for executed, reused, expected in [(cases, frozenset(), list(acceptance)),
                                      (frozenset(), cases, [])]:
        monkeypatch.setattr(module, "report_from_payload",
                            lambda raw, executed=executed, reused=reused: replace(
            original(raw), decision=replace(original(raw).decision, coverage=Coverage(
                cases, cases, executed, reused, cases, frozenset(),
            )),
        ))
        result, _ = reader.read(PROJECT_ID, "report-1", 1, source, (*acceptance, "unmapped"))
        assert result["verified_in_scope"] == expected
        assert "unmapped" in result["unverified_scope"]
        assert result["business_outcome"] == "incomplete"


@pytest.mark.parametrize("change", [
    {"verified_in_scope": ["x"], "unverified_scope": ["x"]},
    {"verified_in_scope": ["outside"], "unverified_scope": []},
    {"verification_state": "verification_valid"},
    {"business_outcome": "success"}, {"verified_in_scope": "x"},
])
def test_projection_refuses_conflicting_or_unknown_scope(change):
    projection = {
        "schema_version": "aitest.delivery-verification/1", "report_id": "report",
        "report_record_revision": 1, "report_digest": "digest", "run_id": "run",
        "execution_snapshot_ref": {}, "verified_in_scope": [], "unverified_scope": ["x"],
        "business_outcome": "incomplete", "verification_state": "required_verification_incomplete",
        **change,
    }
    with pytest.raises(ValueError):
        delivery_projection({"acceptance_item_ids": ["x"], "verification_projection": projection})


def submit_report_delivery(core, inputs, *, complete=False, mapped=False):
    from aitest.application.project.serialization import delivery_to_payload, task_to_payload
    from aitest.contracts.commands import Command
    from aitest.domain.project.context import AcceptanceItem, Delivery, SelfReport, Task
    from tests.unit.test_delivery_submission_origin import RELAY, confirm, prepare

    acceptance_id = "acceptance-create-ticket" if complete or mapped else "ticket-created"
    task = Task("workflow-task", inputs.project_id, "function output", "explicit test scope",
                (AcceptanceItem(acceptance_id, "explicit independent expected value"),))
    draft = Delivery("workflow-delivery", task.task_id, "tested-source", "python main.py",
                     self_report=SelfReport(completed=("developer says complete",)))
    for action, parameters in [
        ("save_task", {"task": task_to_payload(task)}),
        ("save_delivery", {"delivery": delivery_to_payload(draft, project_id=inputs.project_id),
                           "task_revision": 1}),
    ]:
        response = core.api.dispatch(Command(
            action=action, project_id=inputs.project_id,
            request_id=action+"-request", intent_id=action,
            expected_revision=0, parameters=parameters,
        ), RELAY)
        assert response.error is None, response.error
    command = Command(
        action="submit_delivery", target="workflow-submission", project_id=inputs.project_id,
        request_id="submit-workflow-request", intent_id="submit-workflow", expected_revision=0,
        parameters={
            "project_revision": inputs.input_revisions.project_revision, "expected_revision": 0,
            "submission_id": "workflow-submission",
            "delivery_ref": {"delivery_id": draft.delivery_id, "record_revision": 1},
            "source_ref": {"snapshot_id": inputs.snapshot.source_snapshot_id,
                           "record_revision": inputs.snapshot.record_revision},
            "verification_report_ref": {"report_id": "order-report", "record_revision": 1},
        },
    )
    challenge = prepare(core, command)
    assert challenge.error is None, challenge.error
    assert "report" in {item["aggregate_kind"] for item in challenge.result["basis"]["materials"]}
    result = confirm(core, command, challenge.result)
    assert result.error is None, result.error
    assert result.result["verified_in_scope"] == ([acceptance_id] if complete else [])
    assert result.result["verification_state"] == (
        "verification_valid" if complete else "required_verification_incomplete")
    view = core.view_queries.delivery_view(
        project_id=inputs.project_id, delivery_id=draft.delivery_id,
        submission_id="workflow-submission",
    )
    assert view.verified_in_scope == ((acceptance_id,) if complete else ())
    assert view.self_report_completed == ("developer says complete",)
    assert view.verification_report_id == "order-report"
    assert view.verification_business_outcome == ("passed" if complete else "incomplete")

    # Losing the material reader must downgrade the view, never retain valid scope.
    reader = core.view_queries._delivery_verification
    core.view_queries._delivery_verification = None
    try:
        invalid = core.view_queries.delivery_view(
            project_id=inputs.project_id, delivery_id=draft.delivery_id,
            submission_id="workflow-submission",
        )
        assert invalid.verification_state == "material_invalid"
        assert invalid.verified_in_scope == ()
        assert invalid.unverified_scope == (acceptance_id,)
        assert invalid.self_report_completed == view.self_report_completed
    finally:
        core.view_queries._delivery_verification = reader


def test_delivery_report_rechecks_evidence_bytes(tmp_path, monkeypatch):
    harness = _Harness(tmp_path, evidence=("evidence-1",))
    _create(harness)
    reader = DeliveryVerificationReader(harness.service, harness.repository, harness.objects)

    def unavailable(reference):
        raise OSError("evidence lost")

    monkeypatch.setattr(harness.objects, "read_bytes", unavailable)
    with pytest.raises(OSError, match="evidence lost"):
        reader.read(PROJECT_ID, "report-1", 1, harness.prepared.snapshot.content_identity, ())


def test_runtime_mapping_keeps_original_obligations_and_adds_effective_cases(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import aitest.application.project.delivery_verification as module

    harness = _Harness(tmp_path)
    _create(harness)
    raw = harness.repository.read(
        aggregate_kind="report", record_id="report-1", revision=1,
    ).payload
    reference = module.SnapshotContentRef.model_validate(raw["execution_snapshot_ref"])
    facts = module.SavedRuntimeRevisionReader(harness.repository).read_snapshot(
        project_id=PROJECT_ID, run_id=raw["run_id"], reference=reference,
    )
    original = module.report_from_payload
    first, second = harness.prepared.frozen_cases
    acceptance = tuple(first.links.acceptance_item_ids)
    first_only = frozenset({first.case_id})
    cases = frozenset({first.case_id, second.case_id})
    monkeypatch.setattr(module, "report_from_payload", lambda payload: replace(
        original(payload), decision=replace(original(payload).decision, coverage=Coverage(
            cases, cases, first_only, frozenset(), first_only, frozenset(),
        )),
    ))
    reader = DeliveryVerificationReader(harness.service, harness.repository, harness.objects)
    baseline, _ = reader.read(PROJECT_ID, "report-1", 1,
                              harness.prepared.snapshot.content_identity, acceptance)
    assert baseline["verified_in_scope"] == sorted(acceptance)
    # Reader dispatch/mapping fixture: persistence of revision chains has its own suite.
    changed = second.model_copy(update={"links": first.links})
    monkeypatch.setattr(module, "FrozenRunPlanReader", lambda records: SimpleNamespace(
        read=lambda prepared: SimpleNamespace(plan=object(), cases=object()),
    ))
    monkeypatch.setattr(module, "SavedRuntimeRevisionReader", lambda records: SimpleNamespace(
        read_snapshot=lambda **kwargs: facts.model_copy(
            update={"runtime_revision_refs": (object(),)},
        ),
        read_effective_cases=lambda **kwargs: (changed,),
    ))
    result, _ = reader.read(PROJECT_ID, "report-1", 1,
                            harness.prepared.snapshot.content_identity, acceptance)
    assert result["verified_in_scope"] == []
    assert result["unverified_scope"] == sorted(acceptance)
