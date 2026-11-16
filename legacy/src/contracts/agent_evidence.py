"""Versioned Agent tool-call/evidence capture (phase 1, `aitest.agent-evidence/1.0`)."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from aitest.contracts.prepared_run import ContractModel

AgentObservation = Literal[
    "matched", "mismatched", "no_result", "query_error", "deadline_reached", "unknown"
]


class AgentToolCallInput(ContractModel):
    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments_digest: str = Field(min_length=1)
    result_digest: str = Field(min_length=1)
    success: bool
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _references_are_nonempty(self) -> Self:
        if any(not item.strip() for item in self.evidence_refs):
            raise ValueError("Agent tool-call evidence references must not be empty")
        return self


class AgentEvaluationInput(ContractModel):
    business_object_id: str = Field(min_length=1)
    observation: AgentObservation
    tool_call_ids: tuple[str, ...] = Field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _references_are_nonempty(self) -> Self:
        if any(not item.strip() for item in (*self.tool_call_ids, *self.evidence_refs)):
            raise ValueError("Agent evaluation references must not be empty")
        if not self.tool_call_ids and not self.evidence_refs:
            raise ValueError("Agent text self-report requires a tool call or evidence")
        return self


class AgentEvidenceInput(ContractModel):
    schema_version: Literal["aitest.agent-evidence/1.0"] = "aitest.agent-evidence/1.0"
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    tool_calls: tuple[AgentToolCallInput, ...] = Field(min_length=1)
    evaluations: tuple[AgentEvaluationInput, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _call_ids_are_unique(self) -> Self:
        ids = {item.call_id for item in self.tool_calls}
        if len(ids) != len(self.tool_calls):
            raise ValueError("Agent tool-call identities must be unique")
        return self


__all__ = [
    "AgentEvaluationInput",
    "AgentEvidenceInput",
    "AgentObservation",
    "AgentToolCallInput",
]
