"""Saved repository authority, rather than mutually consistent client DTOs."""

from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from aitest.application.execution.commit import ExecutionCommitCoordinator, _run_pointer_id
from aitest.application.execution.facts import project_attempt_fact
from aitest.application.planning.draft import text_digest
from aitest.application.planning.plan_builder import build_plan
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.saved_runtime_revision import SavedRuntimeRevisionAssessment
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
    case_from_payload,
    case_to_payload,
)
from aitest.application.planning.substrate_adapter import PortsRecordReader
from aitest.contracts.execution_facts import RunControlStateFact
from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    PlanRevisionRef,
    RecoveryCheckpoint,
    RecoveryRecord,
    SideEffectClass,
    StepRevisionRef,
)
from aitest.domain.planning.plans import PlanPublicationStatus, RunDriver
from aitest.domain.planning.runtime_revision import CaseRuntimeChange, RuntimeRevisionRequest
from tests.support.controlled_confirmation import basis_command, controlled_basis_confirm
from tests.support.legacy_execution_snapshot import stage_legacy_snapshot
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_initial_run_registration import register, service


def publish_snapshot(core, coordinator, facts, request="assessment-controlled-snapshot"):
    core.unit_of_work.begin(request, facts.project_id, intent_id=request + "-intent")
    try:
        _, saved = coordinator._stage_snapshot(facts)
        core.unit_of_work.commit(request)
        return saved
    except BaseException:
        core.unit_of_work.rollback()
        raise


@pytest.fixture
def runtime(authoritative):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    initial = register(core, prepared)
    coordinator = ExecutionCommitCoordinator(core.unit_of_work, records=core.unit_of_work.repo)
    # This controlled paused projection proves repository mechanics, not a real half-run AC.
    facts = publish_snapshot(
        core,
        coordinator,
        initial.model_copy(
            update={
                "run_revision": 2,
                "run": initial.run.model_copy(
                    update={"run_revision": 2, "control_state": RunControlStateFact.PAUSED}
                ),
            }
        ),
    )
    reader = PortsRecordReader(core.unit_of_work.repo)
    raw = reader.read(
        aggregate_kind="plan", record_id=inputs.plan_revision.revision_id, revision=1
    ).payload
    cases = tuple(
        case_from_payload(
            reader.read(
                aggregate_kind="case", record_id=ref["case_id"], revision=ref["revision"]
            ).payload
        )
        for ref in raw["case_revisions"]
    )
    scope = acceptance_scope_from_payload(
        reader.read(
            aggregate_kind="acceptance_scope",
            record_id=raw["scope_id"],
            revision=raw["scope_revision"],
        ).payload
    )
    # Plan is an untrusted view; its confirmation label is never used as saved authority.
    plan = replace(
        build_plan(
            plan_id=raw["plan_id"],
            revision=raw["revision"],
            scope=scope,
            cases=cases,
            project_id=inputs.project_id,
        ),
        status=PlanPublicationStatus.PUBLISHED,
        confirmation_id="view-label",
        record_revision=1,
    )
    request = RuntimeRevisionRequest(
        base_plan_revision_id=plan.plan_id,
        base_plan_revision_no=1,
        base_plan_revision_digest=inputs.plan_revision.digest,
        observed_snapshot_cursor=facts.snapshot_cursor,
        case_changes=(CaseRuntimeChange(replace(cases[0], revision=2)),),
        reason="核对未执行内容",
        operator_ref="controlled-test-operator",
        requested_driver=RunDriver.STEPWISE,
    )
    origin = service(core)
    assessment = SavedRuntimeRevisionAssessment(
        reader=reader,
        execution=coordinator,
        approvals=origin.approvals,
        controlled_writes=origin.controlled_writes,
    )
    return core, assessment, plan, cases, facts, request


def assess(runtime, **changes):
    _, service, plan, _, facts, request = runtime
    return service.assess(
        project_id=facts.project_id,
        run_id=facts.run_id,
        plan=changes.pop("plan", plan),
        request=changes.pop("request", request),
        **changes,
    )


def test_saved_assessment_is_read_only_and_freezes_the_exact_snapshot(runtime):
    core, _, plan, cases, facts, request = runtime
    sequence = core.unit_of_work.current_commit_sequence()
    decision = assess(runtime)
    assert decision.accepted
    assert decision.revision_no == 1
    assert decision.snapshot_cursor == facts.snapshot_cursor
    assert decision.snapshot_commit_id == facts.snapshot_commit_id
    assert decision.effective_driver is RunDriver.STEPWISE
    assert set(decision.affected_step_ids) == {
        step.step_id for step in facts.steps if step.case_id == cases[0].case_id
    }
    assert not decision.preserved_step_ids and not decision.invalidated_basis_step_ids
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert plan.record_revision == 1 and request.case_changes[0].next_case.revision == 2


@pytest.mark.parametrize("change", ["independent", "mandatory_link"])
def test_actual_frozen_content_prevents_client_body_substitution(runtime, change):
    _, _, _, cases, _, request = runtime
    next_case = (
        replace(cases[0], revision=2, independent_verification=None)
        if change == "independent"
        else replace(
            cases[0],
            revision=2,
            links=replace(cases[0].links, acceptance_item_ids=frozenset({"AC-substitute"})),
        )
    )
    decision = assess(
        runtime, request=replace(request, case_changes=(CaseRuntimeChange(next_case),))
    )
    assert not decision.accepted
    expected = (
        "independent_verification_removed" if change == "independent" else "applicability_weakened"
    )
    assert expected in {refusal.code.value for refusal in decision.refusals}
    assert not decision.affected_step_ids


@pytest.mark.parametrize("change", ["record_revision", "body_revision", "scope", "case_digest"])
def test_plan_view_cannot_change_saved_material(runtime, change):
    _, _, plan, _, _, _ = runtime
    if change == "record_revision":
        wrong = replace(plan, record_revision=7)
    elif change == "body_revision":
        wrong = replace(plan, revision=7)
    elif change == "scope":
        wrong = replace(plan, scope=replace(plan.scope, name="伪造范围正文"))
    else:
        wrong = replace(
            plan,
            case_revisions=(
                replace(plan.case_revisions[0], digest="sha256:fake"),
                *plan.case_revisions[1:],
            ),
        )
    with pytest.raises(ValueError, match="plan view"):
        assess(runtime, plan=wrong)


def test_legacy_plan_view_without_record_revision_does_not_prove_repository_read(runtime):
    with pytest.raises(ValueError, match="exact saved plan revision"):
        assess(runtime, plan=replace(runtime[2], record_revision=None))


def test_newer_case_and_plan_records_do_not_replace_frozen_history(runtime):
    core, _, plan, cases, _, _ = runtime
    core.unit_of_work.begin(
        "later-content", runtime[4].project_id, intent_id="later-content-intent"
    )
    core.unit_of_work.stage_record(
        aggregate_kind="case",
        record_id=cases[0].case_id,
        expected_revision=1,
        payload=case_to_payload(
            replace(cases[0], revision=2, expected="后来的不同预期"),
            project_id=runtime[4].project_id,
        ),
    )
    core.unit_of_work.stage_record(
        aggregate_kind="plan",
        record_id=plan.plan_id,
        expected_revision=1,
        payload={"project_id": runtime[4].project_id, "revision": 99},
    )
    core.unit_of_work.commit("later-content")
    assert assess(runtime).accepted


@pytest.mark.parametrize("change", ["content", "owner", "envelope", "missing"])
def test_reader_cannot_substitute_the_frozen_case(runtime, monkeypatch, change):
    core, service, _, cases, facts, _ = runtime
    original = service.reader.read

    def read(**kwargs):
        record = original(**kwargs)
        if kwargs["aggregate_kind"] == "case" and kwargs["record_id"] == cases[0].case_id:
            if change == "missing":
                raise ValueError("controlled missing frozen case")
            if change == "envelope":
                return replace(record, record_id="foreign-case")
            payload = dict(record.payload)
            payload.update(expected="different") if change == "content" else payload.update(
                project_id="foreign-project"
            )
            return replace(record, payload=payload)
        return record

    monkeypatch.setattr(type(service.reader), "read", lambda self, **kwargs: read(**kwargs))
    sequence = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError):
        assess(runtime)
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert facts.runtime_revision_refs == ()


def test_stale_cursor_returns_whole_decision_refusal(runtime):
    decision = assess(runtime, request=replace(runtime[5], observed_snapshot_cursor=0))
    assert not decision.accepted
    assert "stale_snapshot" in {refusal.code.value for refusal in decision.refusals}
    assert not decision.affected_step_ids


def test_unreadable_saved_runtime_revision_sequence_is_blocked(runtime):
    core, service, _, _, facts, _ = runtime
    later = facts.model_copy(
        update={
            "runtime_revision_refs": ("unreadable-revision",),
            "run": facts.run.model_copy(update={"runtime_revision_refs": ("unreadable-revision",)}),
        }
    )
    sequence = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="frozen run"):
        publish_snapshot(core, service.execution, later, "opaque-revision-sequence")
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert (
        service.execution.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
        == facts
    )

    # Seed exact legacy material through storage, never through ordinary business publication.
    unit = core.unit_of_work
    request = "legacy-opaque-sequence"
    unit.begin(request, facts.project_id, intent_id=request + "-intent")
    try:
        cursor = int(unit.next_commit_seq()) + 1
        later = later.model_copy(
            update={"snapshot_commit_id": f"commit-{cursor}", "snapshot_cursor": cursor}
        )
        pointer = _run_pointer_id(facts.project_id, facts.run_id)
        unit.stage_record(
            aggregate_kind="execution_facts_current",
            record_id=pointer,
            expected_revision=unit.current_revision(
                aggregate_kind="execution_facts_current", record_id=pointer
            ),
            payload={
                "schema_version": "aitest.execution-facts-reference/1.0",
                "project_id": facts.project_id,
                "run_id": facts.run_id,
                "snapshot_commit_id": later.snapshot_commit_id,
                "snapshot_revision": 1,
                "digest": payload_digest(later.model_dump(mode="json")),
                "previous_snapshot_commit_id": facts.snapshot_commit_id,
            },
        )
        unit.stage_record(
            aggregate_kind="execution_facts",
            record_id=later.snapshot_commit_id,
            expected_revision=0,
            payload=later.model_dump(mode="json"),
        )
        unit.commit(request)
    except BaseException:
        unit.rollback()
        raise
    assert (
        service.execution.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
        == later
    )
    with pytest.raises(ValueError, match="sequence is not yet readable"):
        assess(runtime)


def test_snapshot_change_while_reading_material_is_rejected(runtime, monkeypatch):
    core, service, _, _, facts, _ = runtime
    original = service.reader.read
    changed = False

    def read(self, **kwargs):
        nonlocal changed
        record = original(**kwargs)
        if not changed and kwargs["aggregate_kind"] == "plan":
            changed = True
            publish_snapshot(
                core,
                service.execution,
                facts.model_copy(
                    update={
                        "run_revision": 3,
                        "run": facts.run.model_copy(update={"run_revision": 3}),
                    }
                ),
                "changed-during-read",
            )
        return record

    monkeypatch.setattr(type(service.reader), "read", read)
    with pytest.raises(ValueError, match="changed while reading"):
        assess(runtime)


def test_proposed_assertion_digest_cannot_be_self_reported(runtime):
    case = runtime[5].case_changes[0].next_case
    wrong = replace(case, assertion_basis=replace(case.assertion_basis, text_digest="sha256:fake"))
    with pytest.raises(ValueError, match="proposed assertion text"):
        assess(runtime, request=replace(runtime[5], case_changes=(CaseRuntimeChange(wrong),)))


def save_next_basis_and_confirm(runtime):
    core, service, _, cases, facts, request = runtime
    basis = replace(
        cases[0].assertion_basis,
        revision=2,
        text="新依据正文",
        text_digest=text_digest("新依据正文"),
    )
    next_case = replace(cases[0], revision=2, assertion_basis=basis)
    core.unit_of_work.begin("save-next-case", facts.project_id, intent_id="save-next-case-intent")
    core.unit_of_work.stage_record(
        aggregate_kind="case",
        record_id=next_case.case_id,
        expected_revision=1,
        payload=case_to_payload(next_case, project_id=facts.project_id),
    )
    core.unit_of_work.commit("save-next-case")
    response = controlled_basis_confirm(
        core,
        basis_command(
            facts.project_id,
            {
                "case_id": next_case.case_id,
                "case_revision": 2,
                "basis_revision": 2,
                "basis_text_digest": basis.text_digest,
            },
            request="confirm-next-case",
            intent="confirm-next-basis-intent",
        ),
    )
    assert response.error is None, response.error
    return replace(request, case_changes=(CaseRuntimeChange(next_case),)), response.result[
        "confirmation_id"
    ]


def test_saved_exact_new_basis_confirmation_changes_only_confirmation_requirement(runtime):
    request, identity = save_next_basis_and_confirm(runtime)
    without = assess(runtime, request=request)
    with_confirmation = assess(runtime, request=request, confirmation_ids=(identity,))
    assert without.accepted and with_confirmation.accepted
    assert without.confirmation_required_case_ids == (request.case_changes[0].next_case.case_id,)
    assert not with_confirmation.confirmation_required_case_ids
    assert without.affected_step_ids == with_confirmation.affected_step_ids
    assert not with_confirmation.preserved_step_ids


@pytest.mark.parametrize(
    "field",
    [
        "project_id",
        "intent_id",
        "input_digest",
        "case_revision",
        "basis_revision",
        "basis_text_digest",
        "confirmation_id",
    ],
)
def test_confirmation_reference_requires_saved_exact_provenance(runtime, monkeypatch, field):
    request, identity = save_next_basis_and_confirm(runtime)
    service = runtime[1]
    original = service.reader.read

    def read(self, **kwargs):
        record = original(**kwargs)
        if kwargs["aggregate_kind"] == "case_link":
            raw = dict(record.payload)
            raw[field] = 99 if field.endswith("revision") else "forged"
            return replace(record, payload=raw)
        return record

    monkeypatch.setattr(type(service.reader), "read", read)
    with pytest.raises(ValueError):
        assess(runtime, request=request, confirmation_ids=(identity,))


def test_unknown_run_and_foreign_project_cannot_use_saved_authority(runtime):
    _, service, plan, _, facts, request = runtime
    for project, run in ((facts.project_id, "missing-run"), ("foreign-project", facts.run_id)):
        with pytest.raises(ValueError, match="registered current run"):
            service.assess(project_id=project, run_id=run, plan=plan, request=request)


def save_current_attempt(runtime, *, checkpoint_change=None):
    core, service, _, cases, facts, request = runtime
    step = next(step for step in facts.steps if step.case_id == cases[0].case_id)
    attempt = Attempt(
        attempt_id="controlled-current-attempt",
        run_id=facts.run_id,
        step_id=step.step_id,
        attempt_index=1,
        resolved_input_digest="sha256:controlled-input",
        step_revision_ref=StepRevisionRef(**step.step_revision_ref.model_dump()),
        expected_plan_revision_ref=PlanRevisionRef(**facts.plan_revision.model_dump()),
        source_binding_digest="sha256:controlled-source",
        side_effect_class=SideEffectClass.READ_ONLY,
        adapter_kind=AdapterKind.COMMAND,
        adapter_version="controlled-test",
        state=AttemptState.RUNNING,
    )
    checkpoint = RecoveryRecord(
        checkpoint=RecoveryCheckpoint(
            run_id=attempt.run_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            last_committed_stage="running",
        ),
        attempt=attempt,
        project_id=facts.project_id,
    )
    snapshot = facts.model_copy(
        update={
            "steps": tuple(
                s.model_copy(update={"current_attempt_id": attempt.attempt_id})
                if s.step_id == step.step_id
                else s
                for s in facts.steps
            ),
            "attempts": (project_attempt_fact(attempt, is_current=True),),
            "current_attempt_by_step": {
                **facts.current_attempt_by_step,
                step.step_id: attempt.attempt_id,
            },
        }
    )
    core.unit_of_work.begin(
        "controlled-attempt", facts.project_id, intent_id="controlled-attempt-intent"
    )
    checkpoint_refs = {}
    if checkpoint_change != "missing":
        if checkpoint_change == "state":
            checkpoint = replace(checkpoint, attempt=replace(attempt, state=AttemptState.COMPLETED))
        elif checkpoint_change == "input":
            checkpoint = replace(
                checkpoint, attempt=replace(attempt, resolved_input_digest="sha256:other")
            )
        elif checkpoint_change == "plan":
            checkpoint = replace(
                checkpoint,
                attempt=replace(
                    attempt,
                    expected_plan_revision_ref=PlanRevisionRef("foreign-plan", 1, "sha256:foreign"),
                ),
            )
        elif checkpoint_change == "missing_plan":
            checkpoint = replace(
                checkpoint, attempt=replace(attempt, expected_plan_revision_ref=None)
            )
        payload = TypeAdapter(RecoveryRecord).dump_python(checkpoint, mode="json")
        revision = core.unit_of_work.stage_record(
            aggregate_kind="execution_checkpoint",
            record_id=attempt.attempt_id,
            expected_revision=0,
            payload=payload,
        )
        checkpoint_refs[attempt.attempt_id] = (revision, payload)
    if checkpoint_change in {"missing", "state", "input", "plan", "missing_plan"}:
        # Malformed legacy material is a reader fixture, not new publisher admission.
        saved = stage_legacy_snapshot(core.unit_of_work, snapshot, facts)
    else:
        _, saved = service.execution._stage_snapshot(
            snapshot, allow_current_change=True, checkpoint_refs=checkpoint_refs,
        )
    core.unit_of_work.commit("controlled-attempt")
    return replace(request, observed_snapshot_cursor=saved.snapshot_cursor), saved


def test_saved_active_attempt_overrides_a_lagging_pending_step(runtime):
    request, _ = save_current_attempt(runtime)
    decision = assess(runtime, request=request)
    assert not decision.accepted
    assert "step_is_executing" in {refusal.code.value for refusal in decision.refusals}
    assert not decision.affected_step_ids


@pytest.mark.parametrize("change", ["missing", "owner", "state", "input", "plan", "missing_plan"])
def test_current_projection_must_match_the_exact_saved_checkpoint(runtime, change, monkeypatch):
    request, facts = save_current_attempt(runtime, checkpoint_change=change)
    service = runtime[1]
    if change == "owner":
        original = service.execution._read_payload

        def read(kind, identity):
            payload = original(kind, identity)
            if kind == "execution_checkpoint":
                return {**payload, "project_id": "foreign-project"}
            return payload

        monkeypatch.setattr(service.execution, "_read_payload", read)
    # The old current reader alone accepts this internally consistent publication.
    assert (
        service.execution.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
        == facts
    )
    with pytest.raises(ValueError):
        assess(runtime, request=request)


def test_snapshot_change_during_checkpoint_read_is_rejected(runtime, monkeypatch):
    _, facts = save_current_attempt(runtime)
    core, service, *_ = runtime
    original = service.execution.read_checkpoint

    def read(**kwargs):
        checkpoint = original(**kwargs)
        publish_snapshot(
            core,
            service.execution,
            facts.model_copy(
                update={"run_revision": 3, "run": facts.run.model_copy(update={"run_revision": 3})}
            ),
            "checkpoint-read-race",
        )
        return checkpoint

    monkeypatch.setattr(service.execution, "read_checkpoint", read)
    with pytest.raises(ValueError, match="changed during runtime assessment"):
        service.execution.read_runtime_revision_facts(
            project_id=facts.project_id, run_id=facts.run_id
        )


def test_frozen_run_basis_cannot_change_even_when_current_attempt_change_is_allowed(runtime):
    core, service, _, _, facts, _ = runtime
    sequence = core.unit_of_work.current_commit_sequence()
    for field in (
        "plan_revision",
        "environment_ref",
        "environment_isolation_mode",
        "rules_revision",
        "conclusion_ceiling",
    ):
        raw = facts.model_dump(mode="json")
        if field == "plan_revision":
            raw["plan_revision"]["digest"] = "sha256:changed"
            raw["run"]["plan_revision"]["digest"] = "sha256:changed"
        else:
            raw["run"][field] = {
                "environment_isolation_mode": "none",
                "conclusion_ceiling": "partial",
            }.get(field, "changed")
        for allow_current_change in (False, True):
            core.unit_of_work.begin(
                "blocked-basis", facts.project_id, intent_id="blocked-basis-intent"
            )
            try:
                with pytest.raises(ValueError, match="frozen run identity"):
                    service.execution._stage_snapshot(
                        type(facts).model_validate(raw), allow_current_change=allow_current_change
                    )
            finally:
                core.unit_of_work.rollback("blocked-basis")
            assert core.unit_of_work.current_commit_sequence() == sequence
            assert (
                service.execution.read_current_facts(
                    project_id=facts.project_id, run_id=facts.run_id
                )
                == facts
            )


def test_frozen_step_edits_leave_the_actual_current_snapshot_unchanged(runtime):
    core, service, _, _, facts, _ = runtime
    sequence = core.unit_of_work.current_commit_sequence()
    for field, value in (("case_id", "foreign-case"), ("required_for_case", False)):
        raw = facts.model_dump(mode="json")
        raw["steps"][0][field] = value
        for allow_current_change in (False, True):
            core.unit_of_work.begin(
                "blocked-step", facts.project_id, intent_id="blocked-step-intent"
            )
            try:
                with pytest.raises(ValueError, match="frozen step execution basis"):
                    service.execution._stage_snapshot(
                        type(facts).model_validate(raw), allow_current_change=allow_current_change
                    )
            finally:
                core.unit_of_work.rollback("blocked-step")
            assert core.unit_of_work.current_commit_sequence() == sequence
            assert (
                service.execution.read_current_facts(
                    project_id=facts.project_id, run_id=facts.run_id
                )
                == facts
            )


def test_saved_plan_body_version_seven_is_read_at_repository_revision_two(authoritative):
    from aitest.contracts.prepared_run import PlanRevisionRef as PreparedPlanRef
    from tests.support.controlled_publication import controlled_publication_save

    core, inputs, _ = authoritative
    reader = PortsRecordReader(core.unit_of_work.repo)
    original = reader.read(
        aggregate_kind="plan", record_id=inputs.plan_revision.revision_id, revision=1
    ).payload
    scope_record = reader.read(
        aggregate_kind="acceptance_scope",
        record_id=original["scope_id"],
        revision=original["scope_revision"],
    )
    cases = tuple(
        case_from_payload(
            reader.read(
                aggregate_kind="case", record_id=ref["case_id"], revision=ref["revision"]
            ).payload
        )
        for ref in original["case_revisions"]
    )
    response = controlled_publication_save(
        core,
        "publish_plan",
        project=inputs.project_id,
        expected=1,
        request="publish-body-seven",
        intent="publish-body-seven-intent",
        parameters={
            "plan_id": inputs.plan_revision.revision_id,
            "revision": 7,
            "scope": dict(scope_record.payload),
            "cases": [case_to_payload(case, project_id=inputs.project_id) for case in cases],
        },
    )
    assert response.error is None
    assert response.result["published"]
    assert response.result["revision"] == 7 and response.result["record_revision"] == 2
    saved = reader.read(
        aggregate_kind="plan", record_id=inputs.plan_revision.revision_id, revision=2
    )
    inputs = replace(
        inputs,
        plan_revision=PreparedPlanRef(
            revision_id=saved.record_id, revision_no=2, digest=payload_digest(saved.payload)
        ),
        input_revisions=replace(inputs.input_revisions, plan_revision=2),
    )
    prepared = prepare(core, inputs).result
    assert prepared["status"] == "prepared"
    initial = register(core, prepared)
    coordinator = ExecutionCommitCoordinator(core.unit_of_work, records=core.unit_of_work.repo)
    facts = publish_snapshot(
        core,
        coordinator,
        initial.model_copy(
            update={
                "run_revision": 2,
                "run": initial.run.model_copy(
                    update={"run_revision": 2, "control_state": RunControlStateFact.PAUSED}
                ),
            }
        ),
    )
    plan = replace(
        build_plan(
            plan_id=saved.record_id,
            revision=7,
            scope=acceptance_scope_from_payload(scope_record.payload),
            cases=cases,
            project_id=inputs.project_id,
        ),
        status=PlanPublicationStatus.PUBLISHED,
        confirmation_id=response.result["confirmation_id"],
        record_revision=2,
    )
    request = RuntimeRevisionRequest(
        base_plan_revision_id=plan.plan_id,
        base_plan_revision_no=2,
        base_plan_revision_digest=inputs.plan_revision.digest,
        observed_snapshot_cursor=facts.snapshot_cursor,
        case_changes=(CaseRuntimeChange(replace(cases[0], revision=2)),),
        reason="准确仓储版本",
        operator_ref="controlled-test",
    )
    origin = core.initial_run_registration
    assert origin is not None
    service = SavedRuntimeRevisionAssessment(
        reader=reader,
        execution=coordinator,
        approvals=origin.approvals,
        controlled_writes=origin.controlled_writes,
    )
    sequence = core.unit_of_work.current_commit_sequence()
    correct = service.assess(
        project_id=inputs.project_id, run_id=facts.run_id, plan=plan, request=request
    )
    wrong = service.assess(
        project_id=inputs.project_id,
        run_id=facts.run_id,
        plan=plan,
        request=replace(request, base_plan_revision_no=7),
    )
    assert correct.accepted and not wrong.accepted
    assert "plan_revision_mismatch" in {refusal.code.value for refusal in wrong.refusals}
    assert core.unit_of_work.current_commit_sequence() == sequence
