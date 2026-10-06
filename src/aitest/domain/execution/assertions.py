"""Pure JSON assertion rules; unavailable observations remain unknown."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class HttpAssertionOperator(StrEnum):
    EQUALS = "equals"
    CONTAINS = "contains"
    EXISTS = "exists"


@dataclass(frozen=True, slots=True)
class HttpAssertion:
    assertion_id: str
    json_path: str
    operator: HttpAssertionOperator
    expected: object = None

    def __post_init__(self) -> None:
        if not self.assertion_id.strip() or not isinstance(self.json_path, str):
            raise ValueError("assertion requires an identity and a JSON path")
        if not isinstance(self.operator, HttpAssertionOperator):
            raise ValueError("unknown HTTP assertion operator")


def json_equal(actual: object, expected: object) -> bool:
    """Preserve JSON boolean/number distinctions, including nested values."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            json_equal(value, expected[key]) for key, value in actual.items()
        )
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            json_equal(left, right) for left, right in zip(actual, expected, strict=True)
        )
    return actual == expected


def evaluate_http_assertion(
    actual: object, assertion: HttpAssertion, *, value_available: bool
) -> bool | None:
    if not value_available:
        return None
    if assertion.operator is HttpAssertionOperator.EXISTS:
        # Keep the established non-null semantics of this operator.
        return actual is not None
    if assertion.operator is HttpAssertionOperator.EQUALS:
        return json_equal(actual, assertion.expected)
    if isinstance(actual, str):
        return isinstance(assertion.expected, str) and assertion.expected in actual
    if isinstance(actual, list):
        return any(json_equal(item, assertion.expected) for item in actual)
    if isinstance(actual, dict):
        return isinstance(assertion.expected, str) and assertion.expected in actual
    return False


def compare_expected_fields(
    observed: Mapping[str, object], expected: Mapping[str, object]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Distinguish absent data from JSON null; report known mismatches separately."""
    missing = tuple(sorted(key for key in expected if key not in observed))
    mismatched = tuple(
        sorted(
            key
            for key, value in expected.items()
            if key in observed and not json_equal(observed[key], value)
        )
    )
    return missing, mismatched
