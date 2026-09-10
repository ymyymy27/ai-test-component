"""Acceptance item and test planning models."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AcceptanceItem:
    acceptance_item_id: str
    observable_result: str


@dataclass(frozen=True, slots=True)
class TestCase:
    test_case_id: str
    acceptance_item_ids: tuple[str, ...]
    expected_result: str
    mock_allowed: bool = False

