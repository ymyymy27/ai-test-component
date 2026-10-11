"""Returned verification facts must prove the same frozen request scope."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.evidence.evidence_review import EvidenceReviewService
from aitest.application.ports import VerificationRequest
from aitest.domain.evidence.evidence import Verification, VerificationObservation


def pair():
    request = VerificationRequest(
        "read-payment",
        "order-1",
        "query-payment",
        "within-30s",
        "deployment-original",
        query_interval="every-2s",
        evidence_refs=("saved-evidence",),
        expected_facts={"paid": True},
    )
    result = Verification(
        "verification-1",
        request.verification_of,
        request.business_object_id,
        request.query_method,
        VerificationObservation.MATCHED,
        query_interval=request.query_interval,
        deadline_condition=request.deadline_condition,
        target_deployment_ref=request.target_deployment_ref,
        actual_result_ref="saved-actual-result",
        evidence_refs=request.evidence_refs,
    )
    return request, result


@pytest.mark.parametrize(
    "field",
    [
        "verification_of",
        "business_object_id",
        "target_deployment_ref",
        "query_method",
        "query_interval",
        "deadline_condition",
    ],
)
def test_other_scope_cannot_be_attached_to_the_requested_object(field):
    request, result = pair()
    verifier = Mock()
    verifier.verify.return_value = replace(result, **{field: "different"})
    with pytest.raises(ValueError, match="different|scope"):
        EvidenceReviewService(verifier).review(request)
    verifier.verify.assert_called_once_with(request)


def test_expected_evidence_cannot_be_dropped_from_the_returned_fact():
    request, result = pair()
    verifier = Mock()
    verifier.verify.return_value = replace(result, evidence_refs=())
    with pytest.raises(ValueError, match="evidence"):
        EvidenceReviewService(verifier).review(request)


@pytest.mark.parametrize("change", ["shape", "observation", "actual_reference"])
def test_invalid_verification_shape_remains_unverified(change):
    request, result = pair()
    verifier = Mock()
    if change == "shape":
        result = {"passed": True}
    elif change == "observation":
        result = replace(result, observation="matched")
    else:
        request = replace(request, evidence_refs=())
        result = replace(result, actual_result_ref=None, evidence_refs=())
    verifier.verify.return_value = result
    with pytest.raises(ValueError, match="verification|observation|reference"):
        EvidenceReviewService(verifier).review(request)


def test_matching_result_retains_added_evidence_and_unknown_query_outcome():
    request, result = pair()
    verifier = Mock()
    verifier.verify.return_value = replace(
        result, evidence_refs=(*result.evidence_refs, "new-captured-evidence")
    )
    assert EvidenceReviewService(verifier).review(request) == verifier.verify.return_value
    verifier.verify.return_value = replace(
        result,
        observation=VerificationObservation.QUERY_ERROR,
        actual_result_ref=None,
        gap_ids=("actual_query_failed",),
    )
    assert (
        EvidenceReviewService(verifier).review(request).observation
        is VerificationObservation.QUERY_ERROR
    )
