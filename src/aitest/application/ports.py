"""Single source of phase-one ports.

Only consumed signatures are frozen here. Reserved ports name responsibilities,
not a claim of implementation; expand with typed contracts when implementing a slice.
"""

from datetime import datetime
from typing import Protocol


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
    """start/inspect/collect/request_stop; typed signatures pending execution slice."""


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
