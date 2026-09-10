"""Run, attempt, and checkpoint state."""

from dataclasses import dataclass
from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class Checkpoint:
    step_id: str
    execution_handle: str | None
    next_input_digest: str

