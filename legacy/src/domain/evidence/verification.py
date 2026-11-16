"""One comparison of actual JSON fields; filtered required data stays unknown."""

from collections.abc import Mapping

from aitest.domain.evidence.evidence import VerificationObservation
from aitest.domain.execution.assertions import compare_expected_fields


def compare_business_fields(
    observed: Mapping[str, object],
    expected: Mapping[str, object],
    *,
    unavailable_fields: tuple[str, ...] = (),
) -> tuple[VerificationObservation, tuple[str, ...]]:
    if not expected:
        return VerificationObservation.NO_RESULT, ("expected_facts_missing",)
    unavailable = set(unavailable_fields)
    if not unavailable <= expected.keys():
        raise ValueError("unavailable verification fields are outside the expected basis")
    missing, mismatched = compare_expected_fields(
        observed, {key: value for key, value in expected.items() if key not in unavailable}
    )
    observation = (
        VerificationObservation.MISMATCHED
        if mismatched
        else VerificationObservation.NO_RESULT
        if missing or unavailable
        else VerificationObservation.MATCHED
    )
    return observation, (
        tuple(f"business_fact_missing:{key}" for key in missing)
        + tuple(f"business_fact_mismatch:{key}" for key in mismatched)
        + tuple(f"business_fact_filtered:{key}" for key in sorted(unavailable))
    )
