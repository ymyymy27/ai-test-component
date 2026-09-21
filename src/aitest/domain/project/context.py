from dataclasses import dataclass, field

"""Project, task, and module context models."""


@dataclass(frozen=True, slots=True)
class Module:
    module_id: str
    name: str
    owner: str | None = None
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.module_id.strip():
            raise ValueError("module_id must not be empty")
        if not self.name.strip():
            raise ValueError("module name must not be empty")


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    name: str
    schema_version: str = "aitest.project/2.0"
    revision: int = 0
    modules: tuple[Module, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("project_id must not be empty")
        if not self.name.strip():
            raise ValueError("project name must not be empty")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        module_ids = [module.module_id for module in self.modules]
        if len(module_ids) != len(set(module_ids)):
            raise ValueError("module_id must be unique within a project")


"""Task and acceptance item domain models."""


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
    schema_version: str = "aitest.task/2.0"
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


"""Delivery declaration models."""


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_items(values: tuple[str, ...], name: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{name} must not contain empty values")


@dataclass(frozen=True, slots=True)
class Delivery:
    delivery_id: str
    task_id: str
    version: str
    completed: tuple[str, ...] = field(default_factory=tuple)
    incomplete: tuple[str, ...] = field(default_factory=tuple)
    changed_modules: tuple[str, ...] = field(default_factory=tuple)
    api_changes: tuple[str, ...] = field(default_factory=tuple)
    run_method: str = ""
    test_data: tuple[str, ...] = field(default_factory=tuple)
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    mocks: tuple[str, ...] = field(default_factory=tuple)
    known_issues: tuple[str, ...] = field(default_factory=tuple)
    self_test_evidence: tuple[str, ...] = field(default_factory=tuple)
    submitted_by: str | None = None
    schema_version: str = "aitest.delivery/2.0"
    revision: int = 0

    def __post_init__(self) -> None:
        _require_text(self.delivery_id, "delivery_id")
        _require_text(self.task_id, "task_id")
        _require_text(self.version, "version")
        _require_text(self.run_method, "run_method")
        if set(self.completed) & set(self.incomplete):
            raise ValueError("completed and incomplete items must not overlap")
        for name in (
            "completed",
            "incomplete",
            "changed_modules",
            "api_changes",
            "test_data",
            "dependencies",
            "mocks",
            "known_issues",
            "self_test_evidence",
        ):
            _require_items(getattr(self, name), name)
        if self.submitted_by is not None:
            _require_text(self.submitted_by, "submitted_by")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
