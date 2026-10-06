"""Captured agent claims and available references are not independent verification."""

import pytest

from aitest.domain.evidence.evidence import VerificationObservation
from aitest.infrastructure.adapters.execution.agent import (
    AgentAdapter,
    AgentEvaluation,
    AgentToolCall,
)


class ExistingEvidence:
    def exists(self, ref):
        return True


@pytest.mark.parametrize(
    "claimed",
    [
        VerificationObservation.MATCHED,
        VerificationObservation.MISMATCHED,
        VerificationObservation.QUERY_ERROR,
    ],
)
@pytest.mark.parametrize("tool_success", [True, False])
def test_agent_claim_does_not_become_an_independently_observed_business_fact(claimed, tool_success):
    adapter = AgentAdapter(evidence_validator=ExistingEvidence())
    adapter.record_tool_call(
        AgentToolCall("call", "query", "sha256:args", "sha256:result", tool_success, ("evidence",))
    )
    result = adapter.evaluate(
        AgentEvaluation("verification", "query", "object", claimed, ("call",))
    )
    assert result.observation is VerificationObservation.NO_RESULT
    assert result.evidence_refs == ("evidence",)
    assert "agent_independent_verification_missing" in result.gap_ids


@pytest.mark.parametrize("value", ["true", 1, None])
def test_tool_success_metadata_must_be_a_boolean(value):
    with pytest.raises(ValueError):
        AgentToolCall("call", "query", "sha256:args", "sha256:result", value)


@pytest.mark.parametrize("value", ["false", 1, object()])
def test_evidence_existence_requires_an_exact_positive_fact(value):
    class UnverifiedEvidence:
        def exists(self, ref):
            return value

    adapter = AgentAdapter(evidence_validator=UnverifiedEvidence())
    with pytest.raises(ValueError):
        adapter.evaluate(
            AgentEvaluation(
                "verification",
                "query",
                "object",
                VerificationObservation.MATCHED,
                evidence_refs=("evidence",),
            )
        )


def test_unknown_agent_observation_is_not_accepted_as_a_fact():
    with pytest.raises(ValueError):
        AgentEvaluation("verification", "query", "object", "matched")


@pytest.mark.parametrize("refs", ["evidence", ["evidence"]])
def test_tool_evidence_metadata_is_an_immutable_reference_tuple(refs):
    with pytest.raises(ValueError):
        AgentToolCall("call", "query", "sha256:args", "sha256:result", True, refs)


def test_duplicate_tool_identity_cannot_invent_more_actual_calls():
    with pytest.raises(ValueError):
        AgentEvaluation(
            "verification", "query", "object", VerificationObservation.MATCHED, ("call", "call")
        )


def test_call_only_claim_retains_the_missing_material_gap():
    adapter = AgentAdapter()
    adapter.record_tool_call(AgentToolCall("call", "query", "sha256:args", "sha256:result", True))
    result = adapter.evaluate(
        AgentEvaluation(
            "verification", "query", "object", VerificationObservation.MATCHED, ("call",)
        )
    )
    assert result.observation is VerificationObservation.NO_RESULT
    assert set(result.gap_ids) == {
        "agent_evidence_missing",
        "agent_independent_verification_missing",
    }
