from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    command: tuple[str, ...]
    cwd: str
    timeout_seconds: int = 300
    environment: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    metadata: dict[str, Any] = field(default_factory=dict)


class ExecutionPort(Protocol):
    def execute(self, request: ExecutionRequest) -> ExecutionResult: ...

