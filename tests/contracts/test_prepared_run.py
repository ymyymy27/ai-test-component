"""Contract tests for the PreparedRun snapshot.

The three fixtures are the cross-package interface consumed by C and D; every
gate in the model is exercised here so an inadmissible snapshot cannot be
constructed silently.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aitest.contracts.prepared_run import (
    AssertionBasisStateFact,
    BindingFormFact,
    ConclusionCeilingFact,
    PreparedRun,
    PreparedRunStatusFact,
    RunTierFact,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "prepared_run"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["success", "failure", "unknown"])
def test_fixtures_parse(name: str) -> None:
    run = PreparedRun.model_validate(load(name))
    assert run.schema_version == "aitest.prepared-run/1.0"


def test_success_fixture_is_admissible() -> None:
    run = PreparedRun.model_validate(load("success"))
    assert run.status is PreparedRunStatusFact.PREPARED
    assert run.run_tier is RunTierFact.FULL
    assert run.conclusion_ceiling is ConclusionCeilingFact.PASSABLE
    assert set(run.template_required_case_ids) <= set(run.frozen_required_case_ids)
    assert set(run.frozen_required_case_ids) <= set(run.selected_case_ids)
    assert not run.skipped_scope
    assert not run.blocking_reasons
    assert not run.gaps


def test_failure_fixture_is_blocked_and_explains_why() -> None:
    run = PreparedRun.model_validate(load("failure"))
    assert run.status is PreparedRunStatusFact.BLOCKED
    assert run.blocking_reasons
    missing = [
        entry.case_id
        for entry in run.assertion_bases
        if entry.assertion_basis_state is AssertionBasisStateFact.MISSING
    ]
    assert missing
    assert not set(missing) & set(run.frozen_required_case_ids)


def test_plain_fixture_omits_every_git_field() -> None:
    payload = load("unknown")
    assert payload["binding_form"] == "plain"
    assert "git_base_commit" not in payload
    assert "git_diff_digest" not in payload

    run = PreparedRun.model_validate(payload)
    assert run.binding_form is BindingFormFact.PLAIN
    assert run.git_base_commit is None
    assert run.git_diff_digest is None
    assert run.plain_manifest_digest


def test_plain_binding_rejects_git_fields() -> None:
    payload = load("unknown")
    payload["git_base_commit"] = "0" * 40
    with pytest.raises(ValidationError, match="omit git fields"):
        PreparedRun.model_validate(payload)


def test_git_binding_rejects_a_plain_manifest_digest() -> None:
    payload = load("success")
    payload["plain_manifest_digest"] = "sha256:manifest-1"
    with pytest.raises(ValidationError, match="must not carry a plain manifest"):
        PreparedRun.model_validate(payload)


def test_conclusion_ceiling_rejects_a_value_outside_the_enum() -> None:
    """C-01 的取值不属于结论上限：枚举本身即拒绝。"""
    payload = load("success")
    payload["conclusion_ceiling"] = "full"
    with pytest.raises(ValidationError, match="partial.*passable"):
        PreparedRun.model_validate(payload)


def test_conclusion_ceiling_must_be_derived_from_tier() -> None:
    """取值合法但与档位不符时，由派生校验拒绝。"""
    payload = load("success")
    payload["conclusion_ceiling"] = "partial"
    with pytest.raises(ValidationError, match="derived from run_tier"):
        PreparedRun.model_validate(payload)


def test_full_tier_requires_the_whole_frozen_scope() -> None:
    payload = load("success")
    payload["selected_case_ids"] = ["case-1"]
    with pytest.raises(ValidationError, match="include every frozen required case"):
        PreparedRun.model_validate(payload)


def test_full_tier_must_not_skip_scope() -> None:
    payload = load("success")
    payload["skipped_scope"] = [{"case_id": "case-2", "reason": "skipped"}]
    with pytest.raises(ValidationError, match="must not skip"):
        PreparedRun.model_validate(payload)


def test_template_floor_must_be_inside_frozen_required() -> None:
    payload = load("success")
    payload["frozen_required_case_ids"] = ["case-1"]
    with pytest.raises(ValidationError, match="template requirements"):
        PreparedRun.model_validate(payload)


def test_missing_basis_cannot_enter_the_frozen_required_scope() -> None:
    payload = load("success")
    payload["assertion_bases"][0]["assertion_basis_state"] = "missing"
    payload["assertion_bases"][0]["confirmation_refs"] = []
    with pytest.raises(ValidationError, match="missing assertion basis"):
        PreparedRun.model_validate(payload)


def test_frozen_required_case_needs_a_basis_entry() -> None:
    payload = load("success")
    payload["assertion_bases"] = payload["assertion_bases"][:1]
    with pytest.raises(ValidationError, match="need an assertion basis entry"):
        PreparedRun.model_validate(payload)


def test_blocked_status_requires_a_reason() -> None:
    payload = load("failure")
    payload["blocking_reasons"] = []
    with pytest.raises(ValidationError, match="blocking reason"):
        PreparedRun.model_validate(payload)


def test_scope_must_reference_frozen_cases() -> None:
    payload = load("success")
    payload["selected_case_ids"] = ["case-1", "case-2", "case-ghost"]
    with pytest.raises(ValidationError, match="unknown cases"):
        PreparedRun.model_validate(payload)


def test_confirmed_basis_requires_a_confirmation() -> None:
    payload = load("success")
    payload["assertion_bases"][0]["confirmation_refs"] = []
    with pytest.raises(ValidationError, match="confirmed basis requires"):
        PreparedRun.model_validate(payload)


def test_unconfirmed_basis_must_not_carry_a_confirmation() -> None:
    payload = load("success")
    payload["assertion_bases"][0]["assertion_basis_state"] = "present_unconfirmed"
    with pytest.raises(ValidationError, match="only a confirmed basis"):
        PreparedRun.model_validate(payload)


def test_unresolved_authorization_must_state_why() -> None:
    payload = load("success")
    payload["authorization_requirements"] = [
        {
            "action_id": "run-registered-command",
            "requires_side_effect": True,
            "credential_scope": "none",
        }
    ]
    with pytest.raises(ValidationError, match="pending reason"):
        PreparedRun.model_validate(payload)


def test_snapshot_is_immutable() -> None:
    run = PreparedRun.model_validate(load("success"))
    with pytest.raises(ValidationError):
        run.status = PreparedRunStatusFact.BLOCKED
