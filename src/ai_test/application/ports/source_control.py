from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Revision:
    commit: str
    branch: str | None
    dirty: bool


class SourceControlPort(Protocol):
    def revision(self, repository_path: str) -> Revision: ...

