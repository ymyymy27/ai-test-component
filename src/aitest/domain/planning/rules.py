"""Rule drafts and immutable published versions; publication use case is pending."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuleDraft:
    rule_id: str
    text: str
    source: str


@dataclass(frozen=True, slots=True)
class RuleVersion:
    rule_id: str
    revision: int
    text: str
    confirmation_id: str
