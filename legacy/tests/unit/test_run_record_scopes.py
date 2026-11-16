"""A frozen set may change JSON order, never membership, multiplicity or fields."""

import pytest

from aitest.application.execution.run_record import read_run_record, run_record_payload
from aitest.domain.execution.runs import PlanRevisionRef, Run, RunTier
from aitest.domain.project.context import IsolationMode


def run():
    return Run(
        "run",
        "project",
        "workspace",
        "prepare-intent",
        RunTier.QUICK,
        "manual",
        "incomplete",
        PlanRevisionRef("plan", 1, "sha256:plan"),
        "environment@1",
        IsolationMode.VENV,
        "rules",
        revision=1,
        required_scope=frozenset({"case-a", "case-b", "case-c"}),
        selected_scope=frozenset({"case-a", "case-b", "case-c"}),
    )


def test_saved_scope_order_is_canonical_and_legacy_order_remains_readable():
    original = run()
    raw = run_record_payload(original)
    assert raw["required_scope"] == raw["selected_scope"] == ["case-a", "case-b", "case-c"]
    assert read_run_record(raw) == original
    legacy = raw | {
        "required_scope": list(reversed(raw["required_scope"])),
        "selected_scope": list(reversed(raw["selected_scope"])),
    }
    assert read_run_record(legacy) == original
    assert run_record_payload(read_run_record(legacy)) == raw


@pytest.mark.parametrize("field", ["required_scope", "selected_scope"])
@pytest.mark.parametrize("value", [["case-a", "case-a"], [True], [{"id": "case-a"}], None])
def test_duplicate_or_malformed_saved_scope_cannot_be_normalized_away(field, value):
    with pytest.raises(ValueError, match="unique saved text"):
        read_run_record(run_record_payload(run()) | {field: value})


def test_unknown_fields_do_not_gain_authority_via_dataclass_deserialization():
    with pytest.raises(ValueError, match="unknown fields"):
        read_run_record(run_record_payload(run()) | {"authorization": "self-signed"})
