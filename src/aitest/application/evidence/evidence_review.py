"""Independent evidence review framework; no final business decision logic."""

from __future__ import annotations

from aitest.application.ports import BusinessVerificationPort, VerificationRequest
from aitest.domain.evidence.evidence import Verification, VerificationObservation


class EvidenceReviewService:
    """Invoke an independent verifier and validate identity of returned facts."""

    def __init__(self, verifier: BusinessVerificationPort) -> None:
        self._verifier = verifier

    def review(self, request: VerificationRequest) -> Verification:
        result = self._verifier.verify(request)
        if not isinstance(result, Verification):
            raise ValueError("verification result has an unknown shape")
        if not isinstance(result.observation, VerificationObservation):
            raise ValueError("verification observation is unknown")
        if result.verification_of != request.verification_of:
            raise ValueError("verification result points at a different operation")
        if result.business_object_id != request.business_object_id:
            raise ValueError("verification result points at a different business object")
        if any(
            getattr(result, field) != getattr(request, field)
            for field in (
                "target_deployment_ref",
                "query_method",
                "query_interval",
                "deadline_condition",
            )
        ):
            raise ValueError("verification result has a different frozen query scope")
        if not isinstance(result.evidence_refs, tuple) or any(
            not isinstance(value, str) or not value.strip() for value in result.evidence_refs
        ):
            raise ValueError("verification evidence references cannot be verified")
        if not set(request.evidence_refs).issubset(result.evidence_refs):
            raise ValueError("verification result dropped requested evidence references")
        if result.actual_result_ref is not None and (
            not isinstance(result.actual_result_ref, str) or not result.actual_result_ref.strip()
        ):
            raise ValueError("verification actual result reference cannot be verified")
        if result.observation in {
            VerificationObservation.MATCHED,
            VerificationObservation.MISMATCHED,
        } and not (result.actual_result_ref or result.evidence_refs):
            raise ValueError("verification observation lacks a material reference")
        return result


__all__ = [
    "BusinessVerificationPort",
    "EvidenceReviewService",
    "VerificationRequest",
]
