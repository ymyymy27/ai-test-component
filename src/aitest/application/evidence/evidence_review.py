"""Independent evidence review framework; no final business decision logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aitest.domain.evidence.evidence import Verification


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    verification_of: str
    business_object_id: str
    query_method: str
    deadline_condition: str
    target_deployment_ref: str
    query_interval: str = "configured"
    evidence_refs: tuple[str, ...] = ()


class BusinessVerificationPort(Protocol):
    def verify(self, request: VerificationRequest) -> Verification: ...


class EvidenceReviewService:
    """Invoke an independent verifier and validate identity of returned facts."""

    def __init__(self, verifier: BusinessVerificationPort) -> None:
        self._verifier = verifier

    def review(self, request: VerificationRequest) -> Verification:
        result = self._verifier.verify(request)
        if result.verification_of != request.verification_of:
            raise ValueError("verification result points at a different operation")
        if result.business_object_id != request.business_object_id:
            raise ValueError("verification result points at a different business object")
        return result


__all__ = [
    "BusinessVerificationPort",
    "EvidenceReviewService",
    "VerificationRequest",
]