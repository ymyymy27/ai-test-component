"""负责人裁定的真实仓储引用、范围与内容身份回归。"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from aitest.application.planning.drift import DriftReport, check_frozen_basis
from aitest.application.planning.plan_builder import rule_revision_ref
from aitest.application.planning.preparation import (
    PreparationDecision,
    decide_preparation,
    preparation_record_from_payload,
    preparation_record_payload,
)
from aitest.application.planning.prepare_run import prepare_run
from aitest.application.planning.publish import publish_plan, publish_rules
from aitest.application.planning.substrate import transaction
from aitest.application.planning.substrate_adapter import PortsRecordReader, PortsUnitOfWork
from aitest.contracts.prepared_run import PreparedRunStatusFact
from aitest.domain.planning.plans import Plan
from aitest.domain.planning.rules import RuleRevisionRef, RuleVersion
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.unit.test_prepare_run import FixedClock, _inputs
from tests.unit.test_publish_orchestration import _case, _draft, _draft_plan


def _stack(root: Path) -> tuple[PortsUnitOfWork, PortsRecordReader]:
    raw = FileUnitOfWork(root)
    return PortsUnitOfWork(raw, repository=raw.repo, sequence=raw), PortsRecordReader(raw.repo)


def test_published_body_versions_do_not_become_repository_references(tmp_path: Path) -> None:
    unit, reader = _stack(tmp_path)
    first = publish_rules(_draft(revision=9), project_id="p1", unit_of_work=unit, reader=reader)
    assert isinstance(first.value, RuleVersion)
    assert first.value.revision == 9
    assert first.value.record_revision == 1
    assert RuleRevisionRef.of(first.value).revision == 1
    assert rule_revision_ref(first.value).revision == 1
    assert (
        reader.read(aggregate_kind="rule_version", record_id="rule-1", revision=1).payload[
            "revision"
        ]
        == 9
    )
    with pytest.raises(ValueError):
        reader.read(aggregate_kind="rule_version", record_id="rule-1", revision=9)
    second = publish_rules(
        _draft(revision=27), project_id="p1", unit_of_work=unit, reader=reader, expected_revision=1
    )
    assert isinstance(second.value, RuleVersion)
    assert second.value.revision == 27 and second.value.record_revision == 2
    case = _case()
    publication = publish_plan(
        _draft_plan(case, revision=19),
        project_id="p1",
        cases=(case,),
        unit_of_work=unit,
        reader=reader,
    )
    assert isinstance(publication.value, Plan)
    assert publication.value.revision == 19 and publication.value.record_revision == 1
    restarted = _stack(tmp_path)[1]
    assert (
        restarted.read(
            aggregate_kind="plan", record_id=publication.value.plan_id, revision=1
        ).payload["revision"]
        == 19
    )


def test_snapshot_content_change_survives_restart_without_revision_change(tmp_path: Path) -> None:
    unit, reader = _stack(tmp_path)
    inputs = _inputs()
    first = prepare_run(inputs, unit_of_work=unit, reader=reader, clock=FixedClock())
    assert first.status is PreparedRunStatusFact.PREPARED
    before = unit.commit_seq()
    changed = replace(
        inputs,
        snapshot=inputs.snapshot.model_copy(
            update={"source_snapshot_id": "new-content-address", "content_identity": "sha256:new"}
        ),
    )
    unit, reader = _stack(tmp_path)
    result = prepare_run(changed, unit_of_work=unit, reader=reader, clock=FixedClock())
    assert result.payload_hash == first.payload_hash
    assert result.intent_id == first.intent_id
    assert result.status is PreparedRunStatusFact.BLOCKED
    assert {r.source_kind for r in result.invalidation_rules} == {"snapshot_content_identity"}
    assert unit.commit_seq() == before


def test_legacy_snapshot_observation_cannot_be_reused_as_current(tmp_path: Path) -> None:
    unit, reader = _stack(tmp_path)
    inputs = _inputs()
    prepare_run(inputs, unit_of_work=unit, reader=reader, clock=FixedClock())
    original = reader.find_preparation(
        project_id=inputs.project_id,
        client_id=inputs.client_id,
        prepare_request_id=inputs.prepare_request_id,
    )
    assert original is not None
    old_payload = preparation_record_payload(original)
    old_payload.pop("observed_snapshot_content_identity")
    restored = preparation_record_from_payload(old_payload)
    decision = decide_preparation(original.request, restored)
    assert decision.decision is PreparationDecision.NEEDS_REPREPARE
    assert decision.intent_id == original.intent_id


def test_missing_scope_blocks_new_preparation_without_claiming_an_intent(tmp_path: Path) -> None:
    unit, reader = _stack(tmp_path)
    result = prepare_run(
        _inputs(scope_id=None), unit_of_work=unit, reader=reader, clock=FixedClock()
    )
    assert result.status is PreparedRunStatusFact.BLOCKED
    assert result.blocking_reasons[0].code == "needs_reprepare"
    assert unit.commit_seq() == "0"
    assert not (tmp_path / "records.json").exists()


def test_scope_identity_change_with_same_revision_is_detected_after_restart(tmp_path: Path) -> None:
    unit, reader = _stack(tmp_path)
    first = prepare_run(
        _inputs(scope_id="scope-a"), unit_of_work=unit, reader=reader, clock=FixedClock()
    )
    assert first.scope_id == "scope-a"
    unit, reader = _stack(tmp_path)
    result = prepare_run(
        _inputs(scope_id="scope-b"), unit_of_work=unit, reader=reader, clock=FixedClock()
    )
    assert result.intent_id == first.intent_id
    assert result.status is PreparedRunStatusFact.BLOCKED
    assert "scope_id" in {r.source_kind for r in result.invalidation_rules}
    payload = json.loads((tmp_path / "records.json").read_text())
    assert payload["commit"] == 1


def test_legacy_prepared_scope_is_explicitly_missing_in_basis_check(tmp_path: Path) -> None:
    unit, reader = _stack(tmp_path)
    prepared = prepare_run(_inputs(), unit_of_work=unit, reader=reader, clock=FixedClock())
    old = prepared.model_copy(update={"scope_id": None})
    report = check_frozen_basis(old, reader=reader)
    scope = next(c for c in report.checks if c.source_kind == "acceptance_scope")
    assert not scope.readable and "needs_reprepare" in scope.detail
    assert not report.intact
    assert not DriftReport(checks=(), uncovered=("unknown",)).intact


def test_frozen_basis_rejects_foreign_scope_even_with_matching_revision(tmp_path: Path) -> None:
    unit, reader = _stack(tmp_path)
    prepared = prepare_run(_inputs(), unit_of_work=unit, reader=reader, clock=FixedClock())
    with transaction(unit, "other") as tx:
        tx.stage_record(
            aggregate_kind="acceptance_scope",
            record_id="scope-1",
            expected_revision=0,
            payload={"project_id": "other", "revision": 1},
        )
        tx.commit()
    scope = next(
        c
        for c in check_frozen_basis(prepared, reader=reader).checks
        if c.source_kind == "acceptance_scope"
    )
    assert not scope.readable
