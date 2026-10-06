"""External assertion declarations do not replace actual imported JSON material."""

import pytest

from aitest.domain.evidence.evidence import VerificationObservation
from aitest.infrastructure.adapters.execution.external_result import (
    ExternalResultAdapter,
    ExternalResultPayload,
)


def payload(content, declared, identity="import-1"):
    return ExternalResultPayload(
        identity, "fixture-result/1.0", "source-1", "object-1", content, declared
    )


@pytest.mark.parametrize(
    "content,declared,expected,state",
    [
        ({"paid": False}, {"paid": True}, {"paid": True}, VerificationObservation.MISMATCHED),
        ({}, {"paid": None}, {"paid": None}, VerificationObservation.NO_RESULT),
        ({"paid": False}, {"paid": None}, {"paid": None}, VerificationObservation.MISMATCHED),
        ({"paid": True}, {"paid": 1}, {"paid": 1}, VerificationObservation.MISMATCHED),
        (
            {"value": {"paid": False}},
            {"value": {"paid": True}},
            {"value": {"paid": True}},
            VerificationObservation.MISMATCHED,
        ),
        ({"paid": True}, {}, {"paid": True}, VerificationObservation.MATCHED),
    ],
)
def test_import_verification_is_recomputed_from_original_content(
    content, declared, expected, state
):
    result = ExternalResultAdapter().validate(
        payload(content, declared),
        expected_schema="fixture-result/1.0",
        expected_assertions=expected,
        verification_id="verification-1",
    )
    assert result.verification.observation is state
    assert result.verification.actual_result_ref == result.import_ref.content_digest


@pytest.mark.parametrize(
    "material", [{1: "ambiguous key"}, {"nested": {1: "ambiguous key"}}, {"value": float("inf")}]
)
def test_non_json_content_does_not_get_an_import_receipt(material):
    with pytest.raises(ValueError):
        ExternalResultAdapter().validate(
            payload(material, {}),
            expected_schema="fixture-result/1.0",
            expected_assertions={"value": None},
            verification_id="verification-1",
        )


def test_failed_validation_does_not_claim_the_import_id():
    adapter = ExternalResultAdapter()
    with pytest.raises(ValueError):
        adapter.validate(
            payload({"paid": False}, {"paid": False}),
            expected_schema="fixture-result/1.0",
            expected_assertions={"paid": True},
            verification_id=" ",
        )
    result = adapter.validate(
        payload({"paid": True}, {"paid": True}),
        expected_schema="fixture-result/1.0",
        expected_assertions={"paid": True},
        verification_id="verification-1",
    )
    assert result.import_ref.idempotency_state == "new"
    assert result.verification.observation is VerificationObservation.MATCHED
