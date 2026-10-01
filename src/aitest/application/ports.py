"""A-package abstract ports; implementations live in infrastructure."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from aitest.application.planning.model_ports import (
    ModelCall as ModelCall,
)
from aitest.application.planning.model_ports import (
    ModelCallResult as ModelCallResult,
)
from aitest.application.planning.model_ports import (
    ModelCallStatus as ModelCallStatus,
)
from aitest.application.planning.model_ports import (
    ProjectedMaterial as ProjectedMaterial,
)
from aitest.application.planning.model_ports import (
    Projection as Projection,
)
from aitest.application.planning.model_ports import (
    ProjectionStatus as ProjectionStatus,
)
from aitest.application.planning.substrate import (
    CommittedRecord as CommittedRecord,
)
from aitest.application.planning.substrate import (
    RecordQuery as RecordQuery,
)
from aitest.contracts.capabilities import CapabilitySet
from aitest.contracts.commands import Command
from aitest.contracts.errors import ErrorDTO
from aitest.contracts.events import Event
from aitest.contracts.identity import IntentId, RequestId
from aitest.contracts.queries import Query, QuerySpec
from aitest.contracts.responses import Response
from aitest.contracts.secrets import ResolvedSecret
from aitest.contracts.verification import VerificationFact
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
from aitest.domain.planning.model_outbound import MaterialKind


class TransactionPort(Protocol):
    def begin(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        project_id: str,
        intent_id: IntentId | None = None,
    ) -> Response: ...
    def commit(
        self, *, request_id: RequestId, workspace_id: str
    ) -> Response: ...
    def rollback(
        self, *, request_id: RequestId, workspace_id: str
    ) -> Response: ...
    def recover(self, *, workspace_id: str) -> Response: ...

class StoragePort(Protocol):
    def append_record(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        project_id: str,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: dict[str, object],
        intent_id: IntentId,
    ) -> Response: ...
    def read_record(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        project_id: str,
        aggregate_kind: str,
        record_id: str,
        revision: int,
    ) -> Response: ...
    def publish_object(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        project_id: str,
        content: bytes,
        media_type: str,
        intent_id: IntentId,
    ) -> Response: ...
    def read_object(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        project_id: str,
        digest: str,
    ) -> bytes: ...

class BackupPort(Protocol):
    def inspect(
        self, *, request_id: RequestId, workspace_id: str
    ) -> Response: ...
    def create(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        destination: str,
    ) -> Response: ...
    def verify(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        backup_id: str,
    ) -> Response: ...
    def restore(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        backup_id: str,
    ) -> Response: ...


class IndexPort(Protocol):
    def query(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        spec: QuerySpec,
    ) -> Response: ...
    def rebuild(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        index_name: str,
    ) -> Response: ...
    def status(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        index_name: str,
    ) -> Response: ...


class QueryPort(Protocol):
    def dispatch(self, query: Query) -> Response: ...
    def list_events(self, query: Query) -> Response: ...
    def capabilities(
        self, *, request_id: RequestId, workspace_id: str
    ) -> CapabilitySet: ...

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
    def stage_record(
        self,
        *,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: dict[str, object],
    ) -> object: ...
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

    def pin(
        self,
        *,
        canonical_path: str,
        purpose: str,
        selected_paths: Sequence[str] = (),
        exclusion_rules: Sequence[str] = (),
    ) -> Mapping[str, object]: ...

    def materialize(self, snapshot_id: str, destination: str) -> Mapping[str, object]: ...

    def read_pinned(self, snapshot_id: str) -> Mapping[str, object]: ...

    def detect_changes(self, snapshot_id: str) -> Mapping[str, object]: ...


class SourceControlPort(Protocol):
    """Local Git and optional read-only GitHub; absent for plain projects."""

    def is_available(self) -> bool: ...

    def is_repository(self, path: Path) -> bool: ...

    def describe(self, path: Path) -> Mapping[str, object]: ...

    def changes(self, path: Path) -> Mapping[str, object]: ...

    def upstream_counts(self, path: Path) -> Mapping[str, object]: ...


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

    def verify(self, path: Path, expected_sha256: str) -> VerificationFact: ...


class ModelProvider(Protocol):
    """Normalized response/errors for policy-approved projected input."""

    def call(self, request: ModelCall) -> ModelCallResult: ...


class ProjectionPort(Protocol):
    """Safe projected byte references with actual digests and exclusions."""

    def project(
        self,
        *,
        material: Mapping[MaterialKind, str],
        source_snippets_enabled: bool,
    ) -> Projection: ...


class ReportArtifactPort(Protocol):
    """Generate and verify summary/evidence bundles; never self-register success.

    The port only writes and verifies temporary artifacts. Application use cases
    register successful products through the unit of work; an unfinished bundle
    is never registered as a completed product.
    """

    def write_artifact(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        report_id: str,
        kind: str,
        content: bytes,
        media_type: str = "application/octet-stream",
    ) -> Response: ...

    def verify_artifact(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        report_id: str,
        expected_sha256: str,
    ) -> Response: ...


class SecretPort(Protocol):
    """Resolve references for a particular purpose; never return to a view."""

    def resolve(self, reference: str, *, purpose: str) -> ResolvedSecret: ...

    def has_secret(self, reference: str, *, purpose: str) -> bool: ...


class MaintenancePort(Protocol):
    """Integrity, space, guarded reclaim and migration; no business deletion.

    Backup operations are covered by :class:`BackupPort`. Reclaim only removes
    materials proven safe (atomic leftovers, empty staging); an unresolved
    active execution blocks migration.
    """

    def check_integrity(
        self, *, request_id: RequestId, workspace_id: str
    ) -> Response: ...

    def diagnose_space(
        self, *, request_id: RequestId, workspace_id: str
    ) -> Response: ...

    def preview_reclaim(
        self, *, request_id: RequestId, workspace_id: str
    ) -> Response: ...

    def reclaim(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        relative_paths: Sequence[str] = (),
    ) -> Response: ...

    def apply_migrations(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        revisions: Sequence[str] = (),
    ) -> Response: ...
