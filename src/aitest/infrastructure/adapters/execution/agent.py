"""Agent tool-call capture and evidence-backed evaluation adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

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
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")


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
        for name in ("verification_id", "verification_of", "business_object_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")


class EvidenceReferenceValidator(Protocol):
    def exists(self, evidence_ref: str) -> bool: ...


class AgentAdapter:
    """Persist actual calls and reject text-only self-report as evidence."""

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
        missing = [
            call_id
            for call_id in evaluation.tool_call_ids
            if call_id not in self._calls
        ]
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
            observation=evaluation.observation,
            evidence_refs=evidence_refs,
            gap_ids=() if evidence_refs else ("agent_evidence_missing",),
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
            if not self._evidence_validator.exists(evidence_ref)
        ]
        if missing:
            raise ValueError(f"unknown agent evidence refs: {missing}")


__all__ = [
    "AgentAdapter",
    "AgentEvaluation",
    "AgentToolCall",
    "EvidenceReferenceValidator",
]
