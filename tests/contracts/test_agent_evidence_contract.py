"""Agent evidence contract: prose without a tool call or evidence is refused."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aitest.contracts.agent_evidence import (
    AgentEvaluationInput,
    AgentEvidenceInput,
    AgentToolCallInput,
)


def _call() -> AgentToolCallInput:
    return AgentToolCallInput(
        call_id="call-1",
        tool_name="http.get",
        arguments_digest="sha256:args",
        result_digest="sha256:result",
        success=True,
    )


def test_minimal_agent_evidence_is_accepted() -> None:
    document = AgentEvidenceInput(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        tool_calls=(_call(),),
        evaluations=(
            AgentEvaluationInput(
                business_object_id="order-1",
                observation="matched",
                tool_call_ids=("call-1",),
            ),
        ),
    )
    assert document.tool_calls[0].call_id == "call-1"


def test_agent_evidence_requires_a_tool_call_and_evaluation() -> None:
    with pytest.raises(ValidationError):
        AgentEvidenceInput(
            run_id="run-1",
            step_id="step-1",
            attempt_id="attempt-1",
            tool_calls=(),
            evaluations=(),
        )


def test_text_only_evaluation_is_refused() -> None:
    with pytest.raises(ValidationError, match="tool call or evidence"):
        AgentEvaluationInput(business_object_id="order-1", observation="matched")


def test_duplicate_tool_call_ids_are_refused() -> None:
    with pytest.raises(ValidationError, match="unique"):
        AgentEvidenceInput(
            run_id="run-1",
            step_id="step-1",
            attempt_id="attempt-1",
            tool_calls=(_call(), _call()),
            evaluations=(
                AgentEvaluationInput(
                    business_object_id="order-1",
                    observation="matched",
                    tool_call_ids=("call-1",),
                ),
            ),
        )
