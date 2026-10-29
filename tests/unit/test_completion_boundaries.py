"""Saved bytes and typed component reuse proof; no real dynamic/Trae acceptance."""

import io
import json
import zipfile
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.application.planning.substrate import RecordPage
from aitest.application.review.reuse_consumption import ReuseOutcome
from aitest.application.review.saved_exports import ExportRefused
from aitest.application.review.saved_reports import _ReuseReading
from aitest.contracts.commands import HUMAN_ACTIONS, Command
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.interfaces.local.view_queries import CommittedViewReader, SnapshotRequiredError
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_default_execution_authorization import RELAY
from tests.unit.test_initial_run_registration import register
from tests.unit.test_reuse_consumption import _outcome, _reference
from tests.unit.test_saved_reports import PROJECT_ID, _create, _Harness


def _reuse_stack(tmp_path):
    stack = _Harness(tmp_path)
    target = stack.facts
    case = stack.cases[0]
    original = _outcome().source_snapshots[0]

    def bind(value):
        if isinstance(value, str):
            return {original.project_id: PROJECT_ID, "case-1": case}.get(value, value)
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        return value

    source = ExecutionFacts.model_validate_json(json.dumps(bind(original.model_dump(mode="json"))))
    stack.save_facts(source)
    source = stack.facts
    stack.facts = target
    stack.facts_reader._facts = target
    stack.facts_reader._by_run[source.run_id] = source
    reference = _reference(case_id=case, target_run_id=target.run_id)
    outcome = ReuseOutcome(
        reused=frozenset({case}),
        verified=frozenset({case}),
        passed=frozenset({case}),
        references=(reference,),
        source_snapshots=(source,),
    )
    # This seam isolates publication/frozen-source reading from dynamic qualification.
    stack.service._reuse_outcome = lambda _facts: _ReuseReading(outcome, 1)
    return stack, target, source, reference


def test_report_freezes_reuse_source_and_exports_original_attachment(tmp_path):
    stack, target, source, reference = _reuse_stack(tmp_path)
    assert _create(stack)["revision"] == 1
    payload = stack.saved_report("report-1")
    assert payload["reuse"]["source_snapshots"] == [
        {
            "run_id": source.run_id,
            "snapshot_ref": SnapshotContentRef.of(source).model_dump(mode="json"),
        },
    ]
    assert set(reference.evidence_refs) <= set(payload["evidence_refs"])
    # Move the current source after publication; the original report must remain readable.
    stack.facts_reader._by_run[source.run_id] = source.model_copy(update={"evidence_refs": ()})
    result = stack.command(
        action="export_report",
        request_id="export-reuse",
        intent_id="export-reuse-intent",
        expected=0,
        parameters={
            "report_id": "report-1",
            "kind": "evidence_bundle",
            "attachment_refs": list(reference.evidence_refs),
        },
    )
    stored = stack.saved_export(result["record_id"])
    with zipfile.ZipFile(
        io.BytesIO(
            stack.artifact_bytes(stored["artifact_ref"], expected_sha256=stored["artifact_digest"])
        )
    ) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["missing"] == []
        # 证据成员名以实现为准：`evidence/<sha256(evidence_id)>.bin`
        # （不透明摘要，防止别名、保留名与路径穿越）。因此从 manifest 取真实成员，
        # 而不是假设存在可读的 `evidence-1.bin`。
        members = [str(item["path"]) for item in manifest["files"]]
        evidence = [path for path in members if path.startswith("evidence/")]
        assert evidence, f"证据包应含 evidence/ 成员：{members}"
        assert archive.read(evidence[0]) == b"abc"
    assert stack.saved_report("report-1") == payload
    assert target.run_id != source.run_id


def test_legacy_reuse_report_has_named_gap_not_latest_source_fallback(tmp_path):
    stack, _, _, _ = _reuse_stack(tmp_path)
    _create(stack)
    payload = stack.saved_report("report-1")
    del payload["reuse"]["source_snapshots"]
    from aitest.application.review.saved_reports import report_from_payload

    evidence, gaps = stack.exports._frozen_reuse_evidence(report_from_payload(payload), payload)
    assert evidence == ()
    assert gaps[0]["reason"] == "frozen_reuse_sources_unavailable"


@pytest.mark.parametrize("damage", ["target", "case", "duplicate", "foreign_evidence"])
def test_frozen_reuse_metadata_cannot_change_ownership(tmp_path, damage):
    stack, _, _, _ = _reuse_stack(tmp_path)
    _create(stack)
    payload = stack.saved_report("report-1")
    from aitest.application.review.saved_reports import report_from_payload

    snapshot = report_from_payload(payload)
    if damage == "target":
        payload["reuse"]["references"][0]["target_run_id"] = "foreign-run"
    elif damage == "case":
        payload["reuse"]["references"][0]["case_id"] = "foreign-case"
    elif damage == "duplicate":
        payload["reuse"]["source_snapshots"] *= 2
    else:
        payload["reuse"]["references"][0]["evidence_refs"] = ["foreign-evidence"]
    with pytest.raises(ExportRefused):
        stack.exports._frozen_reuse_evidence(snapshot, payload)


def test_conflicting_reuse_discards_its_frozen_source_material():
    outcome = _outcome(references=(_reference(), _reference()))
    assert not outcome.reused
    assert outcome.references == () and outcome.source_snapshots == ()


def _views(boundaries):
    pages = iter(boundaries)
    view = CommittedViewReader(
        decisions=SimpleNamespace(),
        reader=SimpleNamespace(query=lambda _request: next(pages)),
    )
    view.panel_view = lambda **_kwargs: SimpleNamespace(
        model_dump=lambda **_kwargs: {"project_id": "p", "run_id": "r"}
    )
    return view


def test_view_read_uses_one_unchanged_outer_pair():
    page = RecordPage(items=(), commit_id=8, event_cursor="cursor-8", generation=2, index_version=1)
    view = _views((page, page))
    result = view.dispatch(
        Command(
            action="read_view",
            request_id="view-1",
            project_id="p",
            parameters={"view": "panel", "run_id": "r"},
        )
    )
    assert result["commit_id"] == 8 and result["event_cursor"] == "cursor-8"
    assert result["view"] == {"project_id": "p", "run_id": "r"}


def test_view_read_refuses_a_changed_outer_pair():
    page = RecordPage(items=(), commit_id=8, event_cursor="cursor-8", generation=2, index_version=1)
    view = _views((page, replace(page, commit_id=9, event_cursor="cursor-9")))
    with pytest.raises(SnapshotRequiredError, match="boundary changed") as raised:
        view.dispatch(
            Command(
                action="read_view",
                request_id="view-1",
                project_id="p",
                parameters={"view": "panel", "run_id": "r"},
            )
        )
    assert raised.value.code == "snapshot_required"


@pytest.mark.parametrize(
    "parameters",
    [{"view": []}, {"view": "panel", "run_id": "r", "latest": True}, {"view": "other"}],
)
def test_view_read_rejects_unsupported_selectors_before_io(parameters):
    view = _views(())
    with pytest.raises(ValueError):
        view.dispatch(
            Command(
                action="read_view", request_id="bad-view", project_id="p", parameters=parameters
            )
        )


def test_reuse_selection_is_classified_as_human():
    assert "select_case_reuse" in HUMAN_ACTIONS


@pytest.mark.parametrize("view", ["run", "panel"])
def test_default_core_advertises_and_reads_committed_view_without_writes(authoritative, view):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    facts = register(core, prepared)
    before = core.unit_of_work.current_commit_sequence()
    doctor = core.api.dispatch(Command(action="doctor", request_id="view-doctor"), RELAY)
    assert "read_view" in doctor.result["supported_actions"]
    response = core.api.dispatch(
        Command(
            action="read_view",
            request_id="view-read",
            project_id=inputs.project_id,
            parameters={"view": view, "run_id": facts.run_id},
        ),
        RELAY,
    )
    assert response.error is None, response.error
    assert response.result["view"]["run_id"] == facts.run_id
    assert response.result["view"]["project_id"] == inputs.project_id
    assert response.result["commit_id"] == before
    assert response.result["event_cursor"]
    assert core.unit_of_work.current_commit_sequence() == before


@pytest.mark.parametrize("state", ["prepared", "rollback_pending", "corrupt"])
def test_unresolved_migration_blocks_writes_after_restart_but_allows_diagnostics(tmp_path, state):
    from aitest.bootstrap import assemble_workspace_core
    from aitest.infrastructure.file_store.migrations import FileMigrationManager, MigrationError

    core = assemble_workspace_core(tmp_path, instance_id="maintenance-before")
    manager = FileMigrationManager(tmp_path)
    migration_id = "0001-workspace-schema-version"
    plan = manager.plan([migration_id])
    step = tmp_path / "migrations" / "steps" / plan.plan_id / f"{migration_id}.json"
    step.parent.mkdir(parents=True, exist_ok=True)
    step.write_text(json.dumps({"state": state, "plan_id": plan.plan_id}), encoding="utf-8")
    if state == "corrupt":
        with pytest.raises(MigrationError):
            manager.maintenance_reason()
    else:
        assert manager.maintenance_reason()
    core.lifetime_lock.release()
    restarted = assemble_workspace_core(tmp_path, instance_id="maintenance-after")
    try:
        response = restarted.api.dispatch(
            Command(
                action="save_project",
                request_id="blocked-write",
                project_id="p",
                intent_id="blocked-intent",
                expected_revision=0,
                parameters={},
            ),
            RELAY,
        )
        assert response.error.code == "maintenance_required"
        doctor = restarted.api.dispatch(
            Command(action="doctor", request_id="maintenance-doctor"), RELAY
        )
        assert doctor.error is None
        step.write_text(
            json.dumps({"state": "rolled_back", "plan_id": plan.plan_id}), encoding="utf-8"
        )
        assert FileMigrationManager(tmp_path).maintenance_reason() is None
    finally:
        restarted.lifetime_lock.release()
