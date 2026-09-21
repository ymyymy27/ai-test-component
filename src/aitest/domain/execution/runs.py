"""Execution facts are separate from assertions and human confirmations."""

from dataclasses import dataclass
from enum import StrEnum


class ExecutionStatus(StrEnum):
    NOT_STARTED = "not_started"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    PENDING_VERIFICATION = "pending_verification"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Attempt:
    attempt_id: str
    run_id: str
    step_id: str
    attempt_index: int
    status: ExecutionStatus = ExecutionStatus.NOT_STARTED

    def __post_init__(self) -> None:
        if self.attempt_index < 1:
            raise ValueError("attempt_index starts at one")

    @property
    def retry_count(self) -> int:
        return self.attempt_index - 1
