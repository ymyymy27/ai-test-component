"""Synthetic current-pointer/provenance boundaries; no real reuse qualification."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aitest.application.execution.reuse_inspection import CaseReuseInspection, CaseReuseUnverified
from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import (
    AttemptStateFact,
    CaptureCompletenessFact,
    ConsumedConditionFact,
    ConsumedOutputFact,
    StepLevelFact,
    VerificationFact,
    VerificationObservationFact,
)
from aitest.contracts.prepared_run import AssertionBasisStateFact, ConfirmationRef
from aitest.domain.planning.plans import AssertionBasisState, ConfirmationRecord
from tests.unit.test_frozen_run_plan import saved as saved


@pytest.fixture
def inspection(saved):
    prepared, _, repo, scenario = saved
    case = scenario.cases[0]
    frozen = next(x for x in prepared.frozen_cases if x.case_id == case.case_id)
    case_ref = next(x for x in prepared.case_revisions if x.case_id == case.case_id)
    sources = {}
    current = {}

    def facts(run, extra=None):
        raw = {"run": run, "extra": extra}
        return SimpleNamespace(
            snapshot_commit_id=run + "-snapshot", snapshot_cursor=5, attempts=(),
            runtime_revision_refs=(),
            model_dump=lambda **_: deepcopy(raw),
        )

    for run in ("source-run", "target-run"):
        value = facts(run)
        current[run] = value
        sources[run] = SimpleNamespace(
            reference=SnapshotContentRef.of(value), facts=value, case_revision=case_ref,
            original_preparation=prepared,
            steps=tuple(SimpleNamespace(
                step=SimpleNamespace(step_id=run + "-" + item.step_id,
                                     level=StepLevelFact(item.layer), required_for_case=True),
                content=SimpleNamespace(case_step_index=index, frozen_step=item,
                                        checked_case=lambda: case), attempt=None,
            ) for index, item in enumerate(frozen.steps)),
        )
    service = CaseReuseInspection(SimpleNamespace(
        read_runtime_revision_facts=lambda **kw: current[kw["run_id"]],
    ), repo, repo, SimpleNamespace(), SimpleNamespace())
    calls = []

    def read(**kw):
        calls.append(kw)
        assert kw["project_id"] == prepared.project_id and kw["case_id"] == case.case_id
        result = sources[kw["run_id"]]
        assert SnapshotContentRef.of(result.facts) == kw["reference"]
        return result

    service.sources = SimpleNamespace(read=read)
    command = Command(
        action="inspect_case_reuse", request_id="reuse-preview", project_id=prepared.project_id,
        target=case.case_id, parameters={
            "case_id": case.case_id, "source_run_id": "source-run", "target_run_id": "target-run",
            "source_snapshot": SnapshotContentRef.of(current["source-run"]).model_dump(mode="json"),
            "target_snapshot": SnapshotContentRef.of(current["target-run"]).model_dump(mode="json"),
        },
    )
    return service, command, sources, current, calls, facts


def test_matching_frozen_inputs_never_claim_dynamic_qualification(inspection):
    service, command, sources, _, calls, _ = inspection
    result = service.inspect(command)
    assert result["status"] == "unverified"
    assert "environment_dynamic_digest_unverified" in result["denial_reasons"]
    assert "dependency_digest_unverified" in result["denial_reasons"]
    assert "source_attempts_missing" in result["denial_reasons"]
    assert not any(x.endswith("_changed") for x in result["denial_reasons"])
    assert len(result["step_mapping"]) == len(sources["source-run"].steps)
    assert all(x["source_step_id"] != x["target_step_id"] and x["source_attempt_id"] is None
               for x in result["step_mapping"])
    assert len(calls) == 2
    assert all(not source.facts.attempts for source in sources.values())


def exact_basis(inspection):
    service, command, sources, _, _, _ = inspection
    case = sources["target-run"].steps[0].content.checked_case()
    confirmation = ConfirmationRecord(
        "known-confirmation", case.case_id, case.assertion_basis.revision,
        case.assertion_basis.text_digest, "12",
    )
    ref = ConfirmationRef(
        confirmation_id=confirmation.confirmation_id, case_id=case.case_id,
        basis_revision=confirmation.basis_revision,
        confirmed_at_commit=confirmation.confirmed_at_commit,
    )
    prepared = sources["target-run"].original_preparation
    sources["target-run"].original_preparation = prepared.model_copy(update={
        "assertion_bases": tuple(entry.model_copy(update={
            "assertion_basis_state": AssertionBasisStateFact.CONFIRMED,
            "confirmation_refs": (ref,),
        }) if entry.case_id == case.case_id else entry for entry in prepared.assertion_bases),
    })
    # This fixture covers consumption; the exact reader/origin chain has separate tests.
    service.bases = SimpleNamespace(read=lambda **kw: confirmation)
    return service, command, sources, case, confirmation


def test_later_target_confirmation_applies_to_identical_source_basis_without_freeze_rewrite(
    inspection,
):
    service, command, sources, case, confirmation = exact_basis(inspection)
    assert not sources["source-run"].original_preparation.assertion_bases[0].confirmation_refs
    result = service.inspect(command)
    assert result["schema_version"] == "aitest.case-reuse-inspection/1.1"
    assert result["basis_confirmation"]["source"]["state"] == "confirmed"
    assert result["basis_confirmation"]["target"]["state"] == "confirmed"
    assert result["basis_confirmation"]["source"]["confirmation_refs"][0][
        "confirmation_id"
    ] == confirmation.confirmation_id
    assert "basis_confirmed_unverified" not in result["denial_reasons"]
    assert result["status"] == "unverified"
    assert "verification_valid_unverified" in result["denial_reasons"]
    assert case.assertion_basis.state is AssertionBasisState.PRESENT_UNCONFIRMED


@pytest.mark.parametrize("changed", ["basis", "case", "reference", "proof", "frozen_label"])
def test_confirmation_cannot_follow_changed_basis_or_fake_frozen_label(inspection, changed):
    service, command, sources, case, confirmation = exact_basis(inspection)
    if changed in ("basis", "case"):
        new = replace(case, assertion_basis=replace(case.assertion_basis, revision=2)) if (
            changed == "basis"
        ) else replace(case, case_id="another-case")
        for item in sources["source-run"].steps:
            item.content.checked_case = lambda: new
        result = service.inspect(command)
        assert result["basis_confirmation"]["source"]["state"] == "present_unconfirmed"
        assert result["basis_confirmation"]["source"]["confirmation_refs"] == []
        assert "basis_confirmed_unverified" in result["denial_reasons"]
    elif changed == "frozen_label":
        for selected in sources.values():
            prepared = selected.original_preparation
            selected.original_preparation = prepared.model_copy(update={
                "assertion_bases": tuple(entry.model_copy(update={"confirmation_refs": ()})
                                         for entry in prepared.assertion_bases),
            })
        result = service.inspect(command)
        assert result["basis_confirmation"]["source"]["state"] == "present_unconfirmed"
        assert "basis_confirmed_unverified" in result["denial_reasons"]
    else:
        if changed == "reference":
            service.bases = SimpleNamespace(read=lambda **kw: replace(
                confirmation, confirmed_at_commit="other-commit",
            ))
        else:
            def fail(**kw):
                raise ValueError("missing controlled origin")
            service.bases = SimpleNamespace(read=fail)
        with pytest.raises(CaseReuseUnverified):
            service.inspect(command)


def test_basis_confirmation_is_independent_of_later_case_body_revision(inspection):
    service, _, sources, case, _ = exact_basis(inspection)
    later = replace(case, revision=2, objective="later case objective; same basis")
    for item in sources["target-run"].steps:
        item.content.checked_case = lambda: later
    basis = service._basis_confirmation(sources["source-run"], sources["target-run"])
    assert basis["source"]["state"] == basis["target"]["state"] == "confirmed"
    assert case.revision == 1 and later.revision == 2


@pytest.mark.parametrize("wrong_owner", [False, True])
def test_runtime_confirmation_candidate_uses_known_exact_chain_and_owner(
    inspection, monkeypatch, wrong_owner,
):
    service, _, sources, _, confirmation = exact_basis(inspection)
    target = sources["target-run"]
    target.original_preparation = target.original_preparation.model_copy(update={
        "assertion_bases": (),
    })
    target.facts.runtime_revision_refs = ("known-runtime-reference",)
    target.facts.project_id = target.original_preparation.project_id
    target.facts.run_id = "target-run"
    target.facts.run = SimpleNamespace(origin_workspace_id="workspace")
    reads = []

    def record(_, **kw):
        reads.append(kw)
        return SimpleNamespace(
            run_id="another-run" if wrong_owner else "target-run", origin_workspace_id="workspace",
            confirmation_ids=(confirmation.confirmation_id,),
        )

    monkeypatch.setattr(
        "aitest.application.execution.reuse_inspection.SavedRuntimeRevisionReader.read_record",
        record,
    )
    if wrong_owner:
        with pytest.raises(CaseReuseUnverified, match="runtime owner"):
            service._basis_confirmation(sources["source-run"], target)
    else:
        result = service._basis_confirmation(sources["source-run"], target)
        assert result["target"]["state"] == "confirmed"
    assert reads == [{"project_id": target.original_preparation.project_id,
                      "reference": "known-runtime-reference"}]


@pytest.mark.parametrize("change", ["case_content", "source_content", "entry", "adapter"])
def test_real_frozen_field_drift_is_reported_by_the_domain_comparison(inspection, change):
    service, command, sources, _, _, _ = inspection
    source = sources["target-run"]
    prepared = source.original_preparation
    expected = {
        "case_content": "case_content_digest_changed",
        "source_content": "source_content_identity_changed",
        "entry": "entry_input_digest_changed", "adapter": "adapter_digest_changed",
    }[change]
    if change == "case_content":
        source.case_revision = source.case_revision.model_copy(update={"digest": "sha256:changed"})
    elif change == "source_content":
        source.original_preparation = prepared.model_copy(update={
            "snapshot": prepared.snapshot.model_copy(update={"content_identity": "sha256:changed"}),
        })
    else:
        key, value = ("entry_arguments", ("--another-target",)) if change == "entry" else (
            "adapter_versions", {"python": "changed"},
        )
        source.original_preparation = prepared.model_copy(update={
            "execution_source": prepared.execution_source.model_copy(update={key: value}),
        })
    result = service.inspect(command)
    assert result["status"] == "incompatible" and expected in result["denial_reasons"]


def test_partial_or_reordered_layout_is_never_zipped_into_a_reuse_mapping(inspection):
    service, command, sources, _, _, _ = inspection
    sources["target-run"].steps = sources["target-run"].steps[:-1]
    result = service.inspect(command)
    assert result["status"] == "incompatible" and not result["step_mapping"]
    assert "case_step_layout_changed" in result["denial_reasons"]


def test_any_target_attempt_including_history_blocks_reuse(inspection):
    service, command, sources, _, _, _ = inspection
    target = sources["target-run"]
    target.facts.attempts = (SimpleNamespace(step_id=target.steps[-1].step.step_id),)
    result = service.inspect(command)
    assert result["status"] == "incompatible"
    assert "target_case_already_started" in result["denial_reasons"]


@pytest.mark.parametrize("change", [
    "stale", "bool_cursor", "extra_snapshot", "grant", "same_run", "target",
])
def test_invalid_or_stale_input_is_rejected_before_source_reads(inspection, change):
    service, command, _, _, calls, _ = inspection
    values = deepcopy(command.parameters)
    if change == "stale":
        values["target_snapshot"]["digest"] = "sha256:old"
    elif change == "bool_cursor":
        values["target_snapshot"]["snapshot_cursor"] = True
    elif change == "extra_snapshot":
        values["source_snapshot"]["eligible"] = True
    elif change == "grant":
        values["qualified"] = True
    elif change == "same_run":
        values["source_run_id"] = values["target_run_id"]
    command = command.model_copy(update={"parameters": values,
        **({"target": "another-case"} if change == "target" else {})})
    with pytest.raises(CaseReuseUnverified):
        service.inspect(command)
    assert not calls


@pytest.mark.parametrize("run", ["source-run", "target-run"])
def test_pointer_drift_during_actual_material_reads_never_returns_a_mixed_preview(inspection, run):
    service, command, _, current, _, facts = inspection
    original = service.sources.read
    def read(**kwargs):
        result = original(**kwargs)
        if kwargs["run_id"] == "target-run":
            current[run] = facts(run, extra="pointer-changed")
        return result
    service.sources = SimpleNamespace(read=read)
    with pytest.raises(CaseReuseUnverified, match="changed while"):
        service.inspect(command)


@pytest.mark.parametrize("state,capture", [
    (AttemptStateFact.RUNNING, CaptureCompletenessFact.COMPLETE),
    (AttemptStateFact.CANCELLED, CaptureCompletenessFact.GAP),
    (AttemptStateFact.COMPLETED, CaptureCompletenessFact.PARTIAL),
])
def test_existing_attempt_is_not_completed_source_proof(inspection, state, capture):
    service, command, sources, _, _, _ = inspection
    for index, step in enumerate(sources["source-run"].steps):
        step.attempt = SimpleNamespace(
            attempt_id=f"source-attempt-{index}", state=state, capture_completeness=capture,
        )
    result = service.inspect(command)
    assert "source_execution_incomplete" in result["denial_reasons"]
    assert result["status"] == "unverified"


def test_frozen_scope_identity_is_stable_across_python_hash_seeds():
    script = """
import json
from tests.unit.test_frozen_run_plan import saved
from tests.unit.test_case_reuse_inspection import inspection
service, command, *_ = inspection.__wrapped__(saved.__wrapped__())
result = service.inspect(command)
print(json.dumps(result['source_identity'], sort_keys=True))
"""
    results = []
    for seed in ("1", "2", "3"):
        run = subprocess.run(
            [sys.executable, "-c", script], env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=True,
        )
        results.append(json.loads(run.stdout))
    assert results[0] == results[1] == results[2]


def later_source(inspection):
    service, command, sources, current, _, facts = inspection
    original = sources["source-run"]
    original.steps[0].attempt = SimpleNamespace(
        attempt_id="selected-attempt", state=AttemptStateFact.COMPLETED,
        capture_completeness=CaptureCompletenessFact.COMPLETE,
        consumed_outputs=(), consumed_conditions=(),
    )
    later = deepcopy(original)
    later.facts = facts("source-run", extra="later-source-boundary")
    current["source-run"] = later.facts
    read = service.sources.read

    def selected(**kwargs):
        if kwargs["run_id"] == "source-run" and kwargs["reference"] == (
            SnapshotContentRef.of(later.facts)
        ):
            return later
        return read(**kwargs)

    service.sources = SimpleNamespace(read=selected)
    return service, command, original, later


def verification(identity="query-1", attempt="selected-attempt"):
    return VerificationFact(
        verification_id=identity, verification_of=attempt, business_object_id="same-object",
        query_method="independent", observation=VerificationObservationFact.MATCHED,
    )


@pytest.mark.parametrize("field", [
    "evidence_refs", "source_check_results", "verifications", "dependency_invalidations",
    "source_verifications", "mock_declarations", "unknowns", "gaps",
])
def test_same_attempt_later_proof_invalidates_old_case_basis(inspection, field):
    service, command, original, later = later_source(inspection)
    proof = SimpleNamespace(
        attempt_id="selected-attempt", verification_of="selected-attempt",
        affected_attempt_id="selected-attempt", model_dump=lambda **_: {"proof": "late"},
    )
    setattr(original.facts, field, ())
    setattr(later.facts, field, (proof,))
    result = service.inspect(command)
    assert result["status"] == "incompatible"
    assert "source_case_basis_changed" in result["denial_reasons"]
    assert original.steps == later.steps and not original.facts.attempts


@pytest.mark.parametrize("change", ["delete", "observation", "reorder", "other_case"])
def test_query_basis_compares_exact_content_and_scope_without_order_noise(inspection, change):
    service, command, original, later = later_source(inspection)
    first, second = verification(), verification("query-2")
    original.facts.verifications = (first, second)
    if change == "delete":
        later.facts.verifications = (first,)
    elif change == "observation":
        later.facts.verifications = (first.model_copy(update={
            "observation": VerificationObservationFact.MISMATCHED,
        }), second)
    elif change == "reorder":
        later.facts.verifications = (second, first)
    else:
        later.facts.verifications = (first, second, verification("other", "other-case-attempt"))
    result = service.inspect(command)
    assert ("source_case_basis_changed" in result["denial_reasons"]) is (
        change in {"delete", "observation"}
    )


@pytest.mark.parametrize("condition", [False, True])
@pytest.mark.parametrize("change", ["pointer", "state", "capture", "missing", "unchanged"])
def test_actual_consumed_ancestors_outside_case_are_checked(inspection, condition, change):
    service, command, original, later = later_source(inspection)
    field = "consumed_conditions" if condition else "consumed_outputs"
    used = ConsumedConditionFact(
        upstream_attempt_id="upstream", condition_fact_ref="condition", condition_digest="sha256:a",
    ) if condition else ConsumedOutputFact(
        upstream_attempt_id="upstream", output_object_digest="sha256:a", value_ref="value",
    )
    for source in (original, later):
        setattr(source.steps[0].attempt, field, (used,))
        source.facts.current_attempt_by_step = {"upstream-step": "upstream"}
        source.facts.attempts = (SimpleNamespace(
            attempt_id="upstream", step_id="upstream-step", is_current=True,
            state=AttemptStateFact.COMPLETED, capture_completeness=CaptureCompletenessFact.COMPLETE,
            consumed_outputs=(), consumed_conditions=(),
            model_dump=lambda **_: {"upstream": "original"},
        ),)
    if change == "pointer":
        later.facts.current_attempt_by_step["upstream-step"] = "new-upstream"
    elif change == "state":
        later.facts.attempts[0].state = AttemptStateFact.INVALIDATED
        later.facts.attempts[0].model_dump = lambda **_: {"upstream": "invalidated"}
    elif change == "capture":
        later.facts.attempts[0].capture_completeness = CaptureCompletenessFact.PARTIAL
        later.facts.attempts[0].model_dump = lambda **_: {"upstream": "partial"}
    elif change == "missing":
        later.facts.attempts = ()
    result = service.inspect(command)
    assert ("source_dependency_basis_changed" in result["denial_reasons"]) is (
        change != "unchanged"
    )
    assert ("source_dependency_basis_unverified" in result["denial_reasons"]) is (
        change != "unchanged"
    )
    assert "dependency_digest_unverified" in result["denial_reasons"]
    assert "dependencies_valid_unverified" in result["denial_reasons"]


def test_shared_completeness_change_is_a_current_basis_change(inspection):
    service, command, original, later = later_source(inspection)
    original.facts.completeness = "complete"
    later.facts.completeness = "partial"
    assert "source_case_basis_changed" in service.inspect(command)["denial_reasons"]


def test_dependency_invalidation_without_attempt_id_still_belongs_to_selected_step(inspection):
    service, command, original, later = later_source(inspection)
    later.facts.dependency_invalidations = (SimpleNamespace(
        affected_attempt_id=None, affected_step_id=original.steps[0].step.step_id,
        model_dump=lambda **_: {"invalidation": "selected step"},
    ),)
    assert "source_case_basis_changed" in service.inspect(command)["denial_reasons"]


@pytest.mark.parametrize("change", ["pointer", "missing", "unchanged"])
def test_consumed_ancestors_follow_transitive_sources_outside_selected_case(inspection, change):
    service, command, original, later = later_source(inspection)
    for source in (original, later):
        source.steps[0].attempt.consumed_outputs = (ConsumedOutputFact(
            upstream_attempt_id="middle", output_object_digest="sha256:a", value_ref="middle",
        ),)
        middle = SimpleNamespace(
            attempt_id="middle", step_id="middle-step", is_current=True,
            state=AttemptStateFact.COMPLETED, capture_completeness=CaptureCompletenessFact.COMPLETE,
            consumed_outputs=(), consumed_conditions=(ConsumedConditionFact(
                upstream_attempt_id="grandparent", condition_fact_ref="condition",
                condition_digest="sha256:a",
            ),), model_dump=lambda **_: {"middle": "original"},
        )
        grandparent = SimpleNamespace(
            attempt_id="grandparent", step_id="grandparent-step", is_current=True,
            state=AttemptStateFact.COMPLETED, capture_completeness=CaptureCompletenessFact.COMPLETE,
            consumed_outputs=(), consumed_conditions=(),
            model_dump=lambda **_: {"grandparent": "original"},
        )
        source.facts.attempts = (middle, grandparent)
        source.facts.current_attempt_by_step = {
            "middle-step": "middle", "grandparent-step": "grandparent",
        }
    if change == "pointer":
        later.facts.current_attempt_by_step["grandparent-step"] = "new-grandparent"
    elif change == "missing":
        later.facts.attempts = later.facts.attempts[:1]
    result = service.inspect(command)
    assert ("source_dependency_basis_changed" in result["denial_reasons"]) is (
        change != "unchanged"
    )
    assert ("source_dependency_basis_unverified" in result["denial_reasons"]) is (
        change != "unchanged"
    )
