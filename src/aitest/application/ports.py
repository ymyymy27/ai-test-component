"""A-package abstract ports; implementations live in infrastructure."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from aitest.application.planning.model_ports import (
    CredentialResolution as CredentialResolution,
)
from aitest.application.planning.model_ports import (
    CredentialStatus as CredentialStatus,
)
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
    AggregateKind as AggregateKind,
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
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.contracts.identity import IntentId, RequestId
from aitest.contracts.prepared_run import EnvironmentRefFact
from aitest.contracts.queries import Query, QuerySpec
from aitest.contracts.responses import Response
from aitest.contracts.secrets import ResolvedSecret
from aitest.contracts.verification import VerificationFact
from aitest.domain.approvals import ActionBasis, TrustedActor
from aitest.domain.evidence.evidence import RedactionSummary as DomainRedactionSummary
from aitest.domain.evidence.evidence import StoredObjectRef, Verification
from aitest.domain.execution.authorization import ResolvedExecutionAction
from aitest.domain.execution.runs import (
    Attempt,
    CapturedOutputBlock,
    ExecutionCollectionResult,
    ExecutionHandle,
    ExecutionInspectionResult,
    ExecutionRequest,
    OutputBlockRef,
    OutputCursor,
    OutputStreamName,
    Run,
    SpoolManifest,
    Step,
    StopRequestResult,
)
from aitest.domain.planning.model_outbound import MaterialKind
from aitest.domain.planning.plans import Case, Plan


@dataclass(frozen=True, slots=True)
class EnvironmentResolutionRequest:
    project_id: str
    environment_id: str
    isolation_mode: str
    interpreter_requirement: str


class EnvironmentResolver(Protocol):
    """Probe only a core-registered tested carrier, outside the business UOW."""

    def resolve(self, request: EnvironmentResolutionRequest) -> EnvironmentRefFact: ...


class ExecutionActionResolver(Protocol):
    """Trusted assembly only; resolve actual single-step input outside a transaction."""

    def resolve(
        self,
        *,
        run: Run,
        step: Step,
        intent_id: str,
        prepared: Mapping[str, object],
        step_content: Mapping[str, object],
    ) -> ResolvedExecutionAction: ...


class ExecutionAuthorizationProof(Protocol):
    """Read original authority and occupy it inside the coordinator's transaction."""

    def validate_new(self, *, project_id: str, attempt: Attempt) -> None: ...

    def stage_occupation(
        self,
        *,
        project_id: str,
        attempt: Attempt,
        superseded_attempt_ids: tuple[str, ...] = (),
        changed_step_ids: tuple[str, ...] = (),
    ) -> Mapping[str, object]: ...

    def stage_revoke_affected(
        self,
        *,
        project_id: str,
        run_id: str,
        superseded_attempt_ids: tuple[str, ...],
        changed_step_ids: tuple[str, ...],
    ) -> None: ...

    def validate_occupation(
        self, *, project_id: str, attempt: Attempt, proof: Mapping[str, object]
    ) -> None: ...


class TransactionPort(Protocol):
    def begin(
        self,
        *,
        request_id: RequestId,
        workspace_id: str,
        project_id: str,
        intent_id: IntentId | None = None,
    ) -> Response: ...
    def commit(self, *, request_id: RequestId, workspace_id: str) -> Response: ...
    def rollback(self, *, request_id: RequestId, workspace_id: str) -> Response: ...
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
    def inspect(self, *, request_id: RequestId, workspace_id: str) -> Response: ...
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
    def capabilities(self, *, request_id: RequestId, workspace_id: str) -> CapabilitySet: ...


class LocalProtocolPort(Protocol):
    def dispatch(self, command: Command) -> Response: ...
    def event(self, event: Event) -> Response: ...
    def error(self, error: ErrorDTO) -> Response: ...


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class ControlledActorContext(Protocol):
    """Allocated by the core entry; never populated from command parameters."""

    def current(self) -> TrustedActor: ...


class ApprovalActionResolver(Protocol):
    """Freeze/revalidate actual action material without inventing a user gesture."""

    def resolve(
        self,
        *,
        project_id: str,
        intent_id: str,
        action: str,
        target: str,
        parameters: Mapping[str, object],
    ) -> ActionBasis: ...


class ApprovalIdentitySource(Protocol):
    """Opaque identity allocation is independent of approval permission."""

    def create(self) -> str: ...


class ApprovalRecords(Protocol):
    """Exact immutable approval reads; no scan or arbitrary query capability."""

    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> object: ...

    def current_revision(self, *, aggregate_kind: str, record_id: str) -> int: ...


class BasisConfirmationProof(Protocol):
    """Validate saved controlled origin, without inventing a new confirmation."""

    def validate_basis_confirmation(
        self, *, project_id: str, payload: Mapping[str, object]
    ) -> None: ...


class ModelPolicyConfirmationProof(Protocol):
    """Validate exact policy origin before model sending; no new user event is invented."""

    def validate_model_policy(
        self,
        *,
        project_id: str,
        record_revision: int,
        payload: Mapping[str, object],
    ) -> None: ...


class ControlledWriteProof(Protocol):
    """Read exact human origin for a saved registered write, without granting a new action."""

    def validate_saved_write(
        self,
        *,
        project_id: str,
        action: str,
        aggregate_kind: str,
        record_id: str,
        record_revision: int,
        payload: Mapping[str, object],
    ) -> None: ...


class WorkspaceUnitOfWork(Protocol):
    """Expected revisions, epoch, intent results and atomic publication."""

    def open(self, project_id: str) -> None: ...
    def commit_seq(self) -> str:
        """当前工作空间全局提交序号（``records.json`` 的 ``commit``）。

        冻结于 AB-001 §8.8：**未开事务也可读**。``prepare_run`` 的阻塞与
        复用分支不暂存任何记录，但仍要按它判断业务顺序（不使用系统时间）。
        """
        ...

    def next_commit_seq(self) -> str:
        """本次提交后下一条暂存记录将得到的序号。

        冻结于 AB-001 §8.8：A 的提交序号**按记录递增**，已暂存 N 条时
        取值为「当前序号 + N + 1」，供调用方在 ``commit()`` **之前**把
        ``created_at_commit`` 写进不可变 payload。
        """
        ...

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


class StageableWorkspaceUnitOfWork(Protocol):
    """Common B/C atomic writer with a frozen persistent business intent."""

    def open(self, project_id: str) -> None: ...
    def next_commit_seq(self) -> str: ...
    def begin(
        self,
        request_id: str,
        project_id: str,
        workspace_id: str | None = None,
        intent_id: str | None = None,
    ) -> object: ...
    def stage_record(
        self,
        *,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> object: ...
    def commit(self, request_id: str | None = None) -> object: ...
    def rollback(self, request_id: str | None = None) -> object: ...


class RecordRepository(Protocol):
    """Immutable revisions and project-scoped pagination."""

    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> object: ...
    def query(self, query: object) -> object: ...
    def current_revision(self, *, aggregate_kind: str, record_id: str) -> int:
        """按业务身份读取**当前修订**（修订条数）；只读，不经索引。

        冻结于 AB-001 §8.8：修订冲突提示与按业务身份查回准备记录都依赖
        该值；新工作空间尚无索引文件，此读取不得经过索引分页。
        """
        ...


class RuntimeExecutionReader(Protocol):
    """Read saved facts and all consumed current/history checkpoints (BC-001 section 20)."""

    def read_runtime_revision_facts(self, *, project_id: str, run_id: str) -> ExecutionFacts: ...


class RuntimeRevisionBasisReader(Protocol):
    """Verify the exact saved sequence and readable effective case/step content."""

    def read_effective_cases(
        self, *, facts: ExecutionFacts, plan: Plan, initial_cases: tuple[Case, ...]
    ) -> tuple[Case, ...]: ...


class RuntimeRevisionOriginProof(Protocol):
    """Verify original controlled consent for every default runtime revision."""

    def validate_runtime_origins(self, facts: ExecutionFacts) -> None: ...


class RevisionRecordReader(Protocol):
    """Only exact B material reads, without query or preparation lookup capabilities."""

    def read(
        self, *, aggregate_kind: AggregateKind, record_id: str, revision: int
    ) -> CommittedRecord: ...


class EvidenceReferenceValidator(Protocol):
    """Reference availability only; it cannot establish a business observation."""

    def exists(self, evidence_ref: str) -> bool: ...


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


class ModelResponseStore(Protocol):
    """Durable safe response material, independent of authority publication success."""

    def validate(
        self,
        *,
        project_id: str,
        request_id: str,
        identity: Mapping[str, object],
    ) -> None: ...

    def save(
        self,
        *,
        project_id: str,
        request_id: str,
        identity: Mapping[str, object],
        response: Mapping[str, object],
    ) -> StoredObjectRef: ...

    def find(
        self,
        *,
        project_id: str,
        request_id: str,
        identity: Mapping[str, object],
    ) -> tuple[StoredObjectRef, Mapping[str, object]] | None: ...


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

    def verify_pinned(self, snapshot_id: str) -> Mapping[str, object]:
        """Return original metadata after checking every fixed blob; never repin."""
        ...

    def detect_changes(self, snapshot_id: str) -> Mapping[str, object]: ...


class SourceControlPort(Protocol):
    """Local Git and optional read-only GitHub; absent for plain projects."""

    def is_available(self) -> bool: ...

    def is_repository(self, path: Path) -> bool: ...

    def describe(self, path: Path) -> Mapping[str, object]: ...

    def changes(self, path: Path) -> Mapping[str, object]: ...

    def snapshot_identity(self, path: Path) -> Mapping[str, object]: ...

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
    def abort(self) -> None:
        """Release the capture lease while preserving unconfirmed output for recovery."""
        ...

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


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    """Business-level independent verification request."""

    verification_of: str
    business_object_id: str
    query_method: str
    deadline_condition: str
    target_deployment_ref: str
    query_interval: str = "configured"
    evidence_refs: tuple[str, ...] = ()
    expected_facts: Mapping[str, object] = field(default_factory=dict)


class BusinessVerificationPort(Protocol):
    """Read-only verifier that reports only actual observed facts."""

    def verify(self, request: VerificationRequest) -> Verification: ...


class ReadOnlyBusinessQueryPort(Protocol):
    """Independent business read; adapters implement this application-owned port."""

    def read_business_object(
        self, *, business_object_id: str, target_deployment_ref: str
    ) -> Mapping[str, object] | None: ...


@dataclass(frozen=True, slots=True)
class CapturedBusinessVerification:
    """Transient actual material; no digest alone proves that it was saved."""

    verification: Verification
    actual_fields: Mapping[str, object] | None = None


class BusinessVerificationCapturePort(Protocol):
    def capture(self, request: VerificationRequest) -> CapturedBusinessVerification: ...


class BusinessVerificationResolver(Protocol):
    """Resolve the original operation/object and published query/expected basis."""

    def resolve(
        self, *, facts: ExecutionFacts, step_id: str, attempt_id: str
    ) -> VerificationRequest: ...


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
    """Resolve references for a particular purpose; never return to a view.

    冻结语义（AB-001 §3.5）：

    - 解析与能力探测都必须显式携带 ``purpose``；不同用途**分别授权**，
      一个用途下可用的凭据不得顶替另一用途。一期登记用途以
      ``contracts.secrets.KNOWN_PURPOSES`` 为准，未知用途必须显式失败，
      不静默回退、不遍历未登记来源。
    - :meth:`resolve` 只返回 :class:`ResolvedSecret`，明文仅存在于受控
      内存，由调用方显式 reveal/clear；协议层面**没有**向视图返回正文
      的方法。凭据正文不进配置、日志、面板、导出或上传。
    - :meth:`has_secret` 只做能力探测，不得返回或缓存正文。
    """

    def resolve(self, reference: str, *, purpose: str) -> ResolvedSecret: ...

    def has_secret(self, reference: str, *, purpose: str) -> bool: ...


class MaintenancePort(Protocol):
    """Integrity, space, guarded reclaim and migration; no business deletion.

    Backup operations are covered by :class:`BackupPort`. Reclaim only removes
    materials proven safe (atomic leftovers, empty staging); an unresolved
    active execution blocks migration.
    """

    def check_integrity(self, *, request_id: RequestId, workspace_id: str) -> Response: ...

    def diagnose_space(self, *, request_id: RequestId, workspace_id: str) -> Response: ...

    def preview_reclaim(self, *, request_id: RequestId, workspace_id: str) -> Response: ...

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
