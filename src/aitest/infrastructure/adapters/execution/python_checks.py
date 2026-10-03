"""Python source-check and command-check adapter."""

from __future__ import annotations

import time

from aitest.domain.execution.runs import (
    AdapterKind,
    ExecutionCollectionResult,
    ExecutionHandle,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    ExecutionRequest,
    FailureClass,
    OutputCursor,
    StopRequestResult,
)
from aitest.domain.execution.sources import SourceCheckResult, SourceCheckType
from aitest.infrastructure.adapters.execution.command import (
    CommandAdapter,
    CommandRegistration,
    SecretResolver,
)


class PythonChecksAdapter:
    """ExecutionPort-compatible wrapper for registered Python checks."""

    adapter_version = "python-checks/1.0"

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

    def run_check(
        self,
        request: ExecutionRequest,
        *,
        check_result_id: str,
        check_type: SourceCheckType,
        scope: str,
        source_snapshot_ref: str,
        environment_ref: str,
        rules_revision: str,
        raw_output_evidence_ref: str | None = None,
        expected_exit_code: int = 0,
        poll_interval_seconds: float = 0.01,
        timeout_seconds: float = 60.0,
    ) -> SourceCheckResult:
        """Run one registered Python check and save only its actual facts."""
        handle = self.start(request)
        deadline = time.monotonic() + timeout_seconds
        inspection = self.inspect(handle)
        while (
            inspection.state is ExecutionInspectionState.RUNNING
            and time.monotonic() < deadline
        ):
            time.sleep(poll_interval_seconds)
            inspection = self.inspect(handle)
        collection = self.collect(handle)
        if inspection.state is ExecutionInspectionState.RUNNING:
            self.request_stop(handle)
            failure_class = FailureClass.ENVIRONMENT_UNREACHABLE
        elif collection.exit_fact_ref is None or not collection.complete:
            failure_class = FailureClass.TOOL_FAILURE
        elif collection.exit_fact_ref.real_exit_code == expected_exit_code:
            failure_class = FailureClass.PASSED
        else:
            failure_class = FailureClass.SOURCE_ERROR
        return SourceCheckResult(
            check_result_id=check_result_id,
            attempt_id=request.attempt_id,
            check_type=check_type,
            scope=scope,
            source_snapshot_ref=source_snapshot_ref,
            environment_ref=environment_ref,
            rules_revision=rules_revision,
            adapter_version=self.adapter_version,
            failure_class=failure_class,
            adapter_kind=AdapterKind.PYTHON_CHECKS,
            entry_ref=request.registered_entry.entrypoint,
            argument_refs=request.registered_entry.arguments,
            raw_output_evidence_ref=raw_output_evidence_ref,
        )


__all__ = ["PythonChecksAdapter"]
