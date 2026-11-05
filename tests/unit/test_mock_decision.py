"""Static Mock candidates are hints, not critical mock declarations."""

from __future__ import annotations

import json
from pathlib import Path

from aitest.application.review.execution_decision_adapter import build_decision_facts
from aitest.contracts.execution_facts import ExecutionFacts

_FIXTURE = Path("docs/接口对接/已完成/CD-001-ExecutionFacts/fixtures/success.json")


def _facts(declaration_source: str) -> ExecutionFacts:
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    raw["mock_declarations"] = [
        {
            "mock_declaration_id": "mock-1",
            "content_revision": 1,
            "project_id": raw["project_id"],
            "idempotency_key": "mock-1",
            "declaration_source": declaration_source,
            "replaced_object_ref": "module:payment",
            "replaced_behavior_ref": "charge",
            "scope": "payment",
            "verification_state": "unverified",
        }
    ]
    return ExecutionFacts.model_validate(raw)


def test_static_candidate_is_not_a_critical_mock() -> None:
    assert build_decision_facts(_facts("static_candidate")).critical_mock is False


def test_declared_unverified_mock_is_critical() -> None:
    assert build_decision_facts(_facts("development_declaration")).critical_mock is True
