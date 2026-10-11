"""Agent reference capture; model claims require independent business verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aitest.application.ports import EvidenceReferenceValidator as EvidenceReferenceValidator
from aitest.domain.evidence.evidence import (
    Verification,
    VerificationObservation,
)


@dataclass(frozen=True, slots=True)
class AgentToolCall:
    call_id: str
    tool_name: str
    arguments_digest: str
    result_digest: str
    success: bool
    evidence_refs: tuple[str, ...] = ()
    started_at: datetime | None = None
    ended_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("call_id", "tool_name", "arguments_digest", "result_digest"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if type(self.success) is not bool:
            raise ValueError("tool success metadata must be a boolean")
        _require_references(self.evidence_refs)


@dataclass(frozen=True, slots=True)
class AgentEvaluation:
    verification_id: str
    verification_of: str
    business_object_id: str
    observation: VerificationObservation
    tool_call_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    query_method: str = "agent_tool_replay"
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("verification_id", "verification_of", "business_object_id", "query_method"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if not isinstance(self.observation, VerificationObservation):
            raise ValueError("agent observation claim is unknown")
        _require_references(self.tool_call_ids)
        _require_references(self.evidence_refs)
        if len(set(self.tool_call_ids)) != len(self.tool_call_ids):
            raise ValueError("agent tool call identities must be unique")


def _require_references(values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise ValueError("agent references require an immutable nonempty-string tuple")


class AgentAdapter:
    """Capture claim references; independent business verification is a separate fact."""

    def __init__(
        self,
        *,
        evidence_validator: EvidenceReferenceValidator | None = None,
    ) -> None:
        self._calls: dict[str, AgentToolCall] = {}
        self._evidence_validator = evidence_validator

    def record_tool_call(self, call: AgentToolCall) -> None:
        if call.call_id in self._calls and self._calls[call.call_id] != call:
            raise ValueError("agent tool call id conflicts with recorded call")
        self._calls[call.call_id] = call

    def evaluate(self, evaluation: AgentEvaluation) -> Verification:
        if not evaluation.tool_call_ids and not evaluation.evidence_refs:
            raise ValueError("agent text self-report requires an actual tool call or evidence")
        missing = [call_id for call_id in evaluation.tool_call_ids if call_id not in self._calls]
        if missing:
            raise ValueError(f"unknown agent tool calls: {missing}")
        call_evidence = tuple(
            evidence_ref
            for call_id in evaluation.tool_call_ids
            for evidence_ref in self._calls[call_id].evidence_refs
        )
        self._validate_evidence_refs(call_evidence)
        self._validate_evidence_refs(evaluation.evidence_refs)
        evidence_refs = tuple(dict.fromkeys((*call_evidence, *evaluation.evidence_refs)))
        return Verification(
            verification_id=evaluation.verification_id,
            verification_of=evaluation.verification_of,
            business_object_id=evaluation.business_object_id,
            query_method=evaluation.query_method,
            # Available material and a captured tool call cannot prove a model's verdict.
            observation=VerificationObservation.NO_RESULT,
            evidence_refs=evidence_refs,
            gap_ids=("agent_independent_verification_missing",)
            + (() if evidence_refs else ("agent_evidence_missing",)),
            created_at=evaluation.created_at,
        )

    def _validate_evidence_refs(self, evidence_refs: tuple[str, ...]) -> None:
        if not evidence_refs:
            return
        if self._evidence_validator is None:
            raise ValueError("agent evidence refs require a real evidence validator")
        missing = [
            evidence_ref
            for evidence_ref in evidence_refs
            if self._evidence_validator.exists(evidence_ref) is not True
        ]
        if missing:
            raise ValueError(f"unknown agent evidence refs: {missing}")


__all__ = [
    "AgentAdapter",
    "AgentEvaluation",
    "AgentToolCall",
    "EvidenceReferenceValidator",
]
