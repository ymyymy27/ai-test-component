from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    source_version: str
    target_version: str
    dry_run: bool = True


def supported_workspace_schema() -> str:
    return "aatp.workspace-state/1.0"

