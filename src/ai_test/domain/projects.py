"""Project, task, and module context models."""

from dataclasses import dataclass, field


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
    schema_version: str = "aatp.project/1.0"
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
