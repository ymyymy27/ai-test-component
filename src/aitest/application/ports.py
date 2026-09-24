"""Single source of phase-one ports.

Only consumed signatures are frozen here. Reserved ports name responsibilities,
not a claim of implementation; expand with typed contracts when implementing a slice.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from aitest.domain.execution.runs import (
    CapturedOutputBlock,
    ExecutionCollectionResult,
    ExecutionHandle,
    ExecutionInspectionResult,
    ExecutionRequest,
    OutputCursor,
    SpoolManifest,
    StopRequestResult,
)


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class WorkspaceUnitOfWork(Protocol):
    """Expected revisions, epoch, intent results and atomic publication."""


class RecordRepository(Protocol):
    """Immutable revisions and project-scoped pagination."""


class EvidenceObjectStore(Protocol):
    """Project-owned immutable bytes, reference and digest validation."""


class SourceSnapshotPort(Protocol):
    """Pin, retrieve and materialize source with actual byte identity."""


class SourceControlPort(Protocol):
    """Local Git and optional read-only GitHub; absent for plain projects."""


class ExecutionPort(Protocol):
    """Start, inspect, collect, and stop one actual execution handle."""

    def start(self, request: ExecutionRequest) -> ExecutionHandle: ...

    def inspect(self, handle: ExecutionHandle) -> ExecutionInspectionResult: ...

    def collect(
        self,
        handle: ExecutionHandle,
        cursor: OutputCursor | None = None,
    ) -> ExecutionCollectionResult: ...

    def request_stop(self, handle: ExecutionHandle) -> StopRequestResult: ...


class SpoolStore(Protocol):
    """Persist sealed capture blocks and read their verified metadata."""

    def persist_blocks(
        self,
        blocks: Sequence[CapturedOutputBlock],
    ) -> SpoolManifest: ...

    def read_manifest(self, attempt_id: str) -> SpoolManifest: ...


class VerificationPort(Protocol):
    """Independent read-only verification of the same business object."""


class ModelProvider(Protocol):
    """Normalized response/errors for policy-approved projected input."""


class ProjectionPort(Protocol):
    """Safe projected byte references with actual digests and exclusions."""


class ReportArtifactPort(Protocol):
    """Generate and verify summary/bundle before application registration."""


class SecretPort(Protocol):
    """Resolve references for a particular purpose; never return to a view."""


class MaintenancePort(Protocol):
    """Integrity, space, backup and guarded migration; no business deletion."""
