"""A-package abstract ports; implementations live in infrastructure."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from aitest.contracts.capabilities import CapabilitySet
from aitest.contracts.commands import Command
from aitest.contracts.errors import ErrorDTO
from aitest.contracts.events import Event
from aitest.contracts.identity import IntentId, RequestId
from aitest.contracts.queries import Query, QuerySpec
from aitest.contracts.responses import Response

from aitest.domain.evidence.evidence import RedactionSummary as DomainRedactionSummary
from aitest.domain.evidence.evidence import StoredObjectRef
from aitest.domain.execution.runs import (
    CapturedOutputBlock,
    ExecutionCollectionResult,
    ExecutionHandle,
    ExecutionInspectionResult,
    ExecutionRequest,
    OutputBlockRef,
    OutputCursor,
    OutputStreamName,
    SpoolManifest,
    StopRequestResult,
)

class TransactionPort(Protocol):
    def begin(self, *, request_id: RequestId, workspace_id: str, project_id: str, intent_id: IntentId | None = None) -> Response: ...
    def commit(self, *, request_id: RequestId, workspace_id: str) -> Response: ...
    def rollback(self, *, request_id: RequestId, workspace_id: str) -> Response: ...
    def recover(self, *, workspace_id: str) -> Response: ...

class StoragePort(Protocol):
    def append_record(self, *, request_id: RequestId, workspace_id: str, project_id: str, aggregate_kind: str, record_id: str, expected_revision: int | None, payload: dict[str, object], intent_id: IntentId) -> Response: ...
    def read_record(self, *, request_id: RequestId, workspace_id: str, project_id: str, aggregate_kind: str, record_id: str, revision: int) -> Response: ...
    def publish_object(self, *, request_id: RequestId, workspace_id: str, project_id: str, content: bytes, media_type: str, intent_id: IntentId) -> Response: ...
    def read_object(self, *, request_id: RequestId, workspace_id: str, project_id: str, digest: str) -> bytes: ...

class BackupPort(Protocol):
    def inspect(self, *, request_id: RequestId, workspace_id: str) -> Response: ...
    def create(self, *, request_id: RequestId, workspace_id: str, destination: str) -> Response: ...
    def verify(self, *, request_id: RequestId, workspace_id: str, backup_id: str) -> Response: ...
    def restore(self, *, request_id: RequestId, workspace_id: str, backup_id: str) -> Response: ...

class IndexPort(Protocol):
    def query(self, *, request_id: RequestId, workspace_id: str, spec: QuerySpec) -> Response: ...
    def rebuild(self, *, request_id: RequestId, workspace_id: str, index_name: str) -> Response: ...
    def status(self, *, request_id: RequestId, workspace_id: str, index_name: str) -> Response: ...

class QueryPort(Protocol):
    def dispatch(self, query: Query) -> Response: ...
    def list_events(self, query: Query) -> Response: ...
    def capabilities(self, *, request_id: RequestId, workspace_id: str) -> CapabilitySet: ...

class LocalProtocolPort(Protocol):
    def dispatch(self, command: Command) -> Response: ...
    def event(self, event: Event) -> Response: ...
    def error(self, error: ErrorDTO) -> Response: ...


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class WorkspaceUnitOfWork(Protocol):
    """Expected revisions, epoch, intent results and atomic publication."""
    def open(self, project_id: str) -> None: ...
    def stage_record(self, *, aggregate_kind: str, record_id: str, expected_revision: int|None, payload: dict[str, object]) -> object: ...
    def commit(self) -> object: ...
    def rollback(self) -> None: ...


class RecordRepository(Protocol):
    """Immutable revisions and project-scoped pagination."""
    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> object: ...
    def query(self, query: object) -> object: ...


class EvidenceObjectStore(Protocol):
    """Project-owned immutable bytes, reference and digest validation."""

    def publish_bytes(
        self,
        project_id: str,
        content: bytes,
        *,
        media_type: str = "application/octet-stream",
    ) -> StoredObjectRef: ...

    def read_bytes(self, ref: StoredObjectRef) -> bytes: ...


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
        cursors: tuple[OutputCursor, ...] | None = None,
    ) -> ExecutionCollectionResult: ...

    def request_stop(self, handle: ExecutionHandle) -> StopRequestResult: ...


class SpoolStreamWriter(Protocol):
    """Append filtered bytes to one output stream and seal blocks."""

    def append(self, content: bytes) -> tuple[OutputBlockRef, ...]: ...

    def close(self, *, complete: bool = True) -> tuple[OutputBlockRef, ...]: ...


class SpoolStore(Protocol):
    """Persist sealed capture blocks and read their verified metadata."""

    def open_stream(
        self,
        *,
        run_id: str,
        step_id: str,
        attempt_id: str,
        stream_name: OutputStreamName,
        capture_source: str = "command",
        block_size: int = 64 * 1024,
        redaction_summary_id: str | None = None,
    ) -> SpoolStreamWriter: ...

    def persist_blocks(
        self,
        blocks: Sequence[CapturedOutputBlock],
    ) -> SpoolManifest: ...

    def read_manifest(self, attempt_id: str) -> SpoolManifest: ...

    def read_block(self, ref: OutputBlockRef) -> bytes: ...

    def salvage_streams(self, attempt_id: str) -> SpoolManifest: ...

    def persist_redaction_summary(
        self,
        attempt_id: str,
        stream_name: OutputStreamName,
        summary: DomainRedactionSummary,
    ) -> str: ...


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
