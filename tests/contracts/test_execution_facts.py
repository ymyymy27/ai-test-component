import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aitest.contracts.execution_facts import ExecutionFacts

FIXTURES = Path(__file__).parent / "fixtures/execution_facts"


@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    [
        ("success.json", "complete"),
        ("failure.json", "partial"),
        ("unknown.json", "unknown"),
    ],
)
def test_execution_facts_fixtures_validate(fixture_name: str, expected: str) -> None:
    payload = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
    facts = ExecutionFacts.model_validate(payload)
    assert facts.completeness == expected
    assert facts.schema_version == "aitest.execution-facts/1.0"


def test_unknown_fixture_does_not_fabricate_cancelled_or_terminal_result() -> None:
    payload = json.loads((FIXTURES / "unknown.json").read_text(encoding="utf-8"))
    facts = ExecutionFacts.model_validate(payload)
    assert facts.run.control_state == "pending_verification"
    assert facts.attempts[0].state == "pending_verification"
    assert facts.attempts[0].exit_fact is None
    assert facts.attempts[0].unknown_reason_ids


def test_failure_fixture_preserves_blocked_dependent_step() -> None:
    payload = json.loads((FIXTURES / "failure.json").read_text(encoding="utf-8"))
    facts = ExecutionFacts.model_validate(payload)
    assert facts.attempts[0].state == "execution_error"
    assert facts.steps[1].state == "blocked"


def test_attempt_retry_count_is_not_authoritative() -> None:
    payload = json.loads((FIXTURES / "success.json").read_text(encoding="utf-8"))
    payload["attempts"][0]["retry_count"] = 2
    with pytest.raises(ValidationError, match="retry_count"):
        ExecutionFacts.model_validate(payload)


def test_quick_fixture_does_not_derive_evidence_level() -> None:
    payload = json.loads((FIXTURES / "quick.json").read_text(encoding="utf-8"))
    facts = ExecutionFacts.model_validate(payload)
    assert facts.run.tier == "quick"
    assert facts.run.conclusion_ceiling == "partial"
    assert facts.run.evidence_level is None


def test_plan_revision_must_match_run_plan_revision() -> None:
    payload = json.loads((FIXTURES / "success.json").read_text(encoding="utf-8"))
    payload["plan_revision"]["digest"] = "sha256:different"
    with pytest.raises(ValidationError, match="plan_revision"):
        ExecutionFacts.model_validate(payload)
