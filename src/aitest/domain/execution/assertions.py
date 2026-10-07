"""Pure JSON assertion rules; unavailable observations remain unknown."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


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


_PATH_PART = re.compile(r"(?P<name>[^.\[\]]*)(?:\[(?P<index>0|[1-9][0-9]*)\])?\Z")


def read_json_path(payload: object, path: str) -> tuple[bool, object]:
    """Read only the complete supported dotted path, never a permissive prefix."""
    if not isinstance(path, str):
        return False, None
    if path == "":
        return True, payload
    parts = []
    for part in path.split("."):
        match = _PATH_PART.fullmatch(part)
        if match is None or not part:
            return False, None
        parts.append((match["name"], match["index"]))
    current = payload
    for name, index in parts:
        if name:
            if not isinstance(current, dict) or name not in current:
                return False, None
            current = current[name]
        if index is not None:
            # Compare decimal text before int() to bound adversarial integer parsing.
            if not isinstance(current, list) or len(index) > len(str(len(current))):
                return False, None
            position = int(index)
            if position >= len(current):
                return False, None
            current = current[position]
    return True, current


def freeze_json_value(value: object) -> object:
    """Detach JSON comparison material before crossing an external I/O boundary."""
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("JSON comparison material contains a nonfinite number")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON comparison material requires string keys")
        return {key: freeze_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [freeze_json_value(item) for item in value]
    raise ValueError("JSON comparison material has an unsupported value")
