"""Versioned testing rule models."""

from dataclasses import dataclass
from enum import StrEnum


class RuleStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


@dataclass(frozen=True, slots=True)
class RuleSet:
    rule_set_id: str
    version: int
    status: RuleStatus = RuleStatus.DRAFT

