"""Task and acceptance item domain models."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AcceptanceItem:
    acceptance_item_id: str
    observable_result: str
    required: bool = True

    def __post_init__(self) -> None:
        if not self.acceptance_item_id.strip():
            raise ValueError("acceptance_item_id must not be empty")
        if not self.observable_result.strip():
            raise ValueError("observable_result must not be empty")


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    project_id: str
    goal: str
    scope: str
    acceptance_items: tuple[AcceptanceItem, ...]
    inputs: tuple[str, ...] = field(default_factory=tuple)
    outputs: tuple[str, ...] = field(default_factory=tuple)
    preconditions: tuple[str, ...] = field(default_factory=tuple)
    owner: str | None = None
    acceptor: str | None = None
    schema_version: str = "aatp.task/1.0"
    revision: int = 0

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.project_id.strip():
            raise ValueError("project_id must not be empty")
        if not self.goal.strip():
            raise ValueError("task goal must not be empty")
        if not self.scope.strip():
            raise ValueError("task scope must not be empty")
        if not self.acceptance_items:
            raise ValueError("task must contain at least one acceptance item")
        acceptance_item_ids = [item.acceptance_item_id for item in self.acceptance_items]
        if len(acceptance_item_ids) != len(set(acceptance_item_ids)):
            raise ValueError("acceptance_item_id must be unique within a task")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
