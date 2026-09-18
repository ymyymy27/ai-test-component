"""Delivery declaration models."""

from dataclasses import dataclass, field


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
    schema_version: str = "aatp.delivery/1.0"
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