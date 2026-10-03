"""Python source-check adapter framework built on the controlled command adapter."""

from __future__ import annotations

from aitest.domain.execution.runs import (
    ExecutionCollectionResult,
    ExecutionHandle,
    ExecutionInspectionResult,
    ExecutionRequest,
    OutputCursor,
    StopRequestResult,
)
from aitest.infrastructure.adapters.execution.command import (
    CommandAdapter,
    CommandRegistration,
    SecretResolver,
)


class PythonChecksAdapter:
    """ExecutionPort-compatible wrapper for registered Python checks."""

    def __init__(self, secret_resolver: SecretResolver | None = None) -> None:
        self._commands = CommandAdapter(secret_resolver)

    def register(self, registration: CommandRegistration) -> None:
        self._commands.register(registration)

    def start(self, request: ExecutionRequest) -> ExecutionHandle:
        return self._commands.start(request)

    def inspect(self, handle: ExecutionHandle) -> ExecutionInspectionResult:
        return self._commands.inspect(handle)

    def collect(
        self,
        handle: ExecutionHandle,
        cursors: tuple[OutputCursor, ...] | None = None,
    ) -> ExecutionCollectionResult:
        return self._commands.collect(handle, cursors)

    def request_stop(self, handle: ExecutionHandle) -> StopRequestResult:
        return self._commands.request_stop(handle)


__all__ = ["PythonChecksAdapter"]