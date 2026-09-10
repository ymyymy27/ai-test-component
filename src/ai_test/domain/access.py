"""Identity and authorization value objects."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: str
    roles: frozenset[str]
    project_ids: frozenset[str]

    def can_access(self, project_id: str) -> bool:
        return project_id in self.project_ids

