"""Phase-one selection and scope guards; no I/O or framework dependencies."""

from dataclasses import dataclass
from enum import StrEnum


class RunMode(StrEnum):
    QUICK = "quick"
    ON_DEMAND = "on_demand"
    FULL = "full"


class Driver(StrEnum):
    PLANNED = "planned"
    STEPWISE = "stepwise"


def narrow_driver(current: Driver, requested: Driver) -> Driver:
    if current == Driver.STEPWISE and requested == Driver.PLANNED:
        raise ValueError("driver cannot expand authorization")
    return requested


@dataclass(frozen=True, slots=True)
class AcceptanceScope:
    scope_id: str
    revision: int
    name: str
    required_case_ids: frozenset[str]
    template_case_ids: frozenset[str]

    def __post_init__(self) -> None:
        if not self.scope_id.strip() or not self.name.strip() or self.revision < 1:
            raise ValueError("scope requires identity, name and published revision")
        if not self.template_case_ids <= self.required_case_ids:
            raise ValueError("template requirements must be included in required cases")

    def validate_selection(self, mode: RunMode, selected: frozenset[str]) -> None:
        if not selected:
            raise ValueError("empty selection is not not_applicable")
        if mode == RunMode.FULL and not self.required_case_ids <= selected:
            raise ValueError("full selection must include all required cases")
