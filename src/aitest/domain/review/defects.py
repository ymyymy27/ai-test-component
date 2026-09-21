"""Unique issue record, with disposition separate from execution assertions.

Closure and duplicate-chain guards remain pending; no close action is exposed.
"""

from dataclasses import dataclass
from enum import StrEnum


class IssueStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    READY_FOR_RETEST = "ready_for_retest"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class IssueRecord:
    issue_id: str
    project_id: str
    revision: int
    title: str
    status: IssueStatus = IssueStatus.DRAFT
