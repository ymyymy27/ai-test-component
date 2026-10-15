"""唯一 A 包装配入口。B/C/D 只能提交用例注册表。

装配路径分为两支：

- 进程内装配（``create_api`` / ``CoreBootstrap.create``）：供 A 包单测与
  无子进程核心场景使用，直接构造 :class:`LocalAPI`，不依赖命名管道；
- 跨进程装配（:func:`acquire_endpoint`）：发现或启动一个独立的子进程
  作为唯一核心，通过 :class:`NamedPipeClient` 完成身份核对连接。
  子进程入口由 :class:`SystemProcessLauncher` 用 :mod:`subprocess` 启动，
  入口模块默认为 :mod:`aitest.interfaces.local.core_worker`。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess as subprocess
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import cast
from uuid import uuid4

from aitest.application.approval_service import ApprovalService
from aitest.application.controlled_write import ControlledWriteService, SavedControlledWriteResolver
from aitest.application.errors import WorkspaceInUse
from aitest.application.evidence.execution_outputs import SavedExecutionEvidence
from aitest.application.evidence.external_imports import SavedExternalResultImport
from aitest.application.evidence.manual_capture import SavedManualEvidenceCapture
from aitest.application.evidence.saved_verification import SavedBusinessVerification
from aitest.application.evidence.verification_resolver import SavedBusinessVerificationResolver
from aitest.application.execution.action_resolver import build_saved_action_resolver
from aitest.application.execution.authorization import (
    ExecutionAuthorizationService,
    SavedExecutionAuthorizationResolver,
)
from aitest.application.execution.commands import ExecutionCommands
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.output_material import require_saved_output_material
from aitest.application.execution.registration import InitialRunRegistration
from aitest.application.execution.reuse_inspection import CaseReuseInspection
from aitest.application.execution.run_schedule import SavedRunSchedule
from aitest.application.execution.runtime_actions import SavedRuntimeRevisionActions
from aitest.application.execution.saved_control import SavedRunControl
from aitest.application.execution.source_checks import (
    SourceCheckRequest,
    SourceModuleFact,
    SourceProbeObservation,
    SourceSyntaxErrorFact,
    SourceVerificationService,
)
from aitest.application.execution.start_source_binding import SourceBindingUnverified
from aitest.application.execution.step_execution import SavedStepExecution
from aitest.application.maintenance.backup import SavedBackupEntry
from aitest.application.maintenance.migration import SavedMigrationEntry
from aitest.application.maintenance.mode import MaintenanceMode
from aitest.application.planning.basis_approval import SavedBasisApprovalResolver
from aitest.application.planning.basis_confirmation import BasisConfirmationService
from aitest.application.planning.model_policy_confirmation import (
    ModelPolicyConfirmationService,
    SavedHumanActionResolver,
    SavedModelPolicyApprovalResolver,
)
from aitest.application.planning.substrate import RecordReader
from aitest.application.planning.substrate_adapter import (
    PortsRecordReader,
    PortsUnitOfWork,
)
from aitest.application.ports import (
    BusinessVerificationCapturePort,
    BusinessVerificationResolver,
    EnvironmentResolver,
    ExecutionActionResolver,
    ExecutionPort,
    RecordRepository,
    SourceSnapshotPort,
)
from aitest.application.project.delivery_verification import DeliveryVerificationReader
from aitest.application.project.environment_resolution import EnvironmentResolutionService
from aitest.application.project.persistence import load_project
from aitest.application.project.serialization import source_manifest_from_payload
from aitest.application.project.source_analysis import SourceAnalysisService
from aitest.application.record_write import ORDINARY_WRITE_ACTIONS
from aitest.application.record_write import SCHEMA as RECORD_WRITE_SCHEMA
from aitest.application.review.repair_brief import SavedRepairBrief
from aitest.application.review.saved_defects import SavedDefectManagement
from aitest.application.review.saved_exports import SavedExportService
from aitest.application.review.saved_reports import SavedReportService
from aitest.application.review.saved_reuse import SavedCaseReuseSelection
from aitest.application.usecase_registry import BUseCaseDependencies, BUseCaseError
from aitest.contracts.commands import Command
from aitest.contracts.prepared_run import EnvironmentResolutionFact
from aitest.domain.approvals import TrustedActor
from aitest.domain.evidence.evidence import StoredObjectRef
from aitest.domain.execution.runs import FailureClass, PlanRevisionRef
from aitest.domain.execution.sources import (
    ExecutionSourceVerification,
    SourceCheckType,
    SourceVerificationState,
)
from aitest.domain.project.context import SourceFileDigest, source_content_identity
from aitest.infrastructure.adapters.execution.command import CommandAdapter
from aitest.infrastructure.adapters.execution.python_checks import (
    LoadSourceFact,
    PythonLoadSourceProbe,
    PythonSourceProbeError,
)
from aitest.infrastructure.adapters.execution.registered_entry import (
    RegisteredEntryExecutionAdapter,
)
from aitest.infrastructure.adapters.model import HttpModelProvider, ModelCredentialResolver
from aitest.infrastructure.adapters.source_control import GitSourceControl
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore
from aitest.infrastructure.business_query_configuration import HostConfiguredBusinessQueries
from aitest.infrastructure.capabilities import (
    CONNECTION,
    MODEL,
    SECRET,
    SOURCE,
    CapabilityGate,
    FileCapabilityConditionStore,
)
from aitest.infrastructure.clock import SystemClock
from aitest.infrastructure.connections import (
    ConnectionFactStore,
    EndpointConfig,
    LocalAPIConnectionBridge,
)
from aitest.infrastructure.credentials import SecretManager
from aitest.infrastructure.environment_configuration import (
    HostConfiguredEnvironmentResolver,
)
from aitest.infrastructure.environment_configuration import (
    carrier_configuration as carrier_configuration,
)
from aitest.infrastructure.exports import FileReportArtifactPort
from aitest.infrastructure.file_store.backup import FileBackupStore
from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from aitest.infrastructure.file_store.core_exit import FileCoreExitGuard
from aitest.infrastructure.file_store.core_launch import (
    FileCoreLaunchStore,
    ProcessFact,
    probe_process,
    python_launch_program,
    valid_instance_filename,
)
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.locking import LifetimeWriterLock
from aitest.infrastructure.file_store.maintenance import FileMaintenanceService
from aitest.infrastructure.file_store.migrations import FileMigrationManager, MigrationError
from aitest.infrastructure.file_store.model_responses import FileModelResponseStore
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.recovery import (
    RecoveryOrchestrator,
    RecoveryState,
    seal_inflight_outputs,
)
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.file_store.workspace import Workspace
from aitest.infrastructure.projections import SafeMaterialProjector
from aitest.infrastructure.security import guard_value
from aitest.interfaces.local.actor_context import CoreActorContext
from aitest.interfaces.local.api import Handler, LocalAPI, Session
from aitest.interfaces.local.b_registration import b_registration_for
from aitest.interfaces.local.d_registration import d_registration_for
from aitest.interfaces.local.editor_host import (
    Connector,
    CoreEndpoint,
    CoreLauncher,
    CoreStartupObservation,
    EditorHost,
)
from aitest.interfaces.local.view_queries import CommittedViewReader

_INSTANCE_ID_FILE = ".core-instance-id"
_CORE_DIR = "core"
_CONNECTION_LEDGER_FILE = "connection-ledger.jsonl"
_DEFAULT_WORKER_MODULE = "aitest.interfaces.local.core_worker"
_DEFAULT_CONNECT_TIMEOUT_MS = 200
_DEFAULT_WAIT_TIMEOUT_SECONDS = 5.0
_DEFAULT_POLL_INTERVAL_SECONDS = 0.05
_CONNECTION_FACT_SCHEMA = "aitest.core-connection/1.0"


@dataclass(frozen=True, slots=True)
class CoreInstance:
    instance_id: str
    workspace_id: str
    api: LocalAPI
    lifetime_lock: LifetimeWriterLock | None = None


class CoreAssemblyBlocked(RuntimeError):
    """工作空间完整性无法通过启动恢复，核心不得对外服务。"""


class CoreConnectionLedger:
    """核心连接事实的跨重启追加台账（A-10）。

    连接事实此前只活在 ``EditorHost`` / ``LocalAPI`` 的实例内存里，核心
    换实例或宿主重启后无法核对“谁在什么时候经哪条会话连到了哪个核心”。
    台账位于工作空间 ``core/connection-ledger.jsonl``，每行一条不可变事实，
    追加后显式 ``fsync``，目录首次创建时同样 fsync；读取只解析完整行，
    末尾半行（掉电撕裂）会在 :meth:`facts` 处显式抛错，不静默删除。

    台账**只记连接事实，不记任何业务载荷与凭据**；来源会话/用户 SID
    与管道对端身份核对使用同一份本机取证结果。
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        session_probe: Callable[[], int | None] | None = None,
        user_probe: Callable[[], str | None] | None = None,
    ) -> None:
        FileCommitStore.reject_links(workspace_root)
        self._root = workspace_root.resolve()
        self._dir = self._root / _CORE_DIR
        self._path = self._dir / _CONNECTION_LEDGER_FILE
        self._session_probe = session_probe
        self._user_probe = user_probe

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        kind: str,
        *,
        pipe_workspace_id: str,
        instance_id: str,
    ) -> dict[str, object]:
        """追加一条连接事实并返回落盘内容。"""
        fact: dict[str, object] = {
            "schema": _CONNECTION_FACT_SCHEMA,
            "seq": self._next_seq(),
            "kind": kind,
            "at": datetime.now(UTC).isoformat(),
            "pipe_workspace_id": pipe_workspace_id,
            "instance_id": instance_id,
            "source_session_id": self._source_session(),
            "source_user_sid": self._source_user(),
        }
        line = (json.dumps(fact, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        # 目录项首次落盘需要 fsync，掉电后目录里才保证看得到台账文件。
        _fsync_directory(self._dir)
        return fact

    def facts(self) -> tuple[dict[str, object], ...]:
        """读回全部完整事实行；撕裂尾行抛错，绝不静默截断历史。"""
        if not self._path.exists():
            return ()
        facts: list[dict[str, object]] = []
        for raw in self._path.read_bytes().splitlines():
            if not raw.strip():
                continue
            obj = json.loads(raw.decode("utf-8"))
            if isinstance(obj, dict):
                facts.append(obj)
        return tuple(facts)

    def last(self, kind: str) -> dict[str, object] | None:
        for fact in reversed(self.facts()):
            if fact.get("kind") == kind:
                return fact
        return None

    def _next_seq(self) -> int:
        existing = self.facts()
        if not existing:
            return 1
        last_seq = existing[-1].get("seq")
        return int(last_seq) + 1 if isinstance(last_seq, int) else 1

    def _source_session(self) -> int | None:
        if self._session_probe is not None:
            return self._session_probe()
        from aitest.interfaces.local.pipe import current_session_id

        return current_session_id()

    def _source_user(self) -> str | None:
        if self._user_probe is not None:
            return self._user_probe()
        from aitest.interfaces.local.pipe import current_user_sid

        return current_user_sid()


def _fsync_directory(path: Path) -> None:
    """尽力 fsync 目录；不支持的平台静默（持久化尽力点，非业务正确性）。"""
    if not sys.platform.startswith("win"):
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _json_value(value: object) -> object:
    """把只读维护结果转成 JSON 可承载的普通值。

    只做形状转换：模型走 `model_dump(mode="json")`，dataclass 逐字段递归，
    枚举取 `.value`，序列展开为列表；**不补造、不猜测**未知值。
    """

    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class CoreAssembly:
    """一次核心装配的产物；进程内与跨进程核心共用同一条装配路径。"""

    api: LocalAPI
    workspace: Workspace
    unit_of_work: FileUnitOfWork
    recovery: RecoveryState
    lifetime_lock: LifetimeWriterLock
    seal_inflight: Callable[[], tuple[str, ...]]
    gate: CapabilityGate | None = None
    secret_manager: SecretManager | None = None
    model_provider: HttpModelProvider | None = None
    snapshot_store: FileSourceSnapshotStore | None = None
    source_probe: PythonLoadSourceProbe | None = None
    initial_run_registration: InitialRunRegistration | None = None
    environment_resolution: EnvironmentResolutionService | None = None
    execution_authorizations: ExecutionAuthorizationService | None = None
    execution_coordinator: ExecutionCommitCoordinator | None = None
    step_execution: SavedStepExecution | None = None
    run_schedule: SavedRunSchedule | None = None
    continue_work: Callable[[], object] | None = None
    business_verification: SavedBusinessVerification | None = None
    external_imports: SavedExternalResultImport | None = None
    shutdown_blocker: Callable[[], str | None] | None = None
    run_control: SavedRunControl | None = None
    runtime_actions: SavedRuntimeRevisionActions | None = None
    model_policy_proof: ModelPolicyConfirmationService | None = None
    #: 进程内维护窗口（迁移等结构性动作期间暂停新写入，架构 04 §142）。
    maintenance_mode: MaintenanceMode | None = None
    #: 宿主只读视图查询（架构 06 §1/§54：面板读 DTO，不读原始记录载荷）。
    view_queries: CommittedViewReader | None = None


def _interpreter_verifier() -> Callable[[str, EnvironmentResolutionFact], str]:
    """装配层适配：把基础设施探针包成应用层可调用的「现场观测解释器」回调。

    应用层不得导入 ``infrastructure``（依赖方向），故探针只能在此注入。探针以**冻结环境事实**
    的解释器路径启动，交回该解释器**自报的 realpath**（``LoadSourceFact.executable``）——
    这是独立观测，不是把配置值回读一遍。比较口径由应用层按
    ``source_checks._derive_state``（解析后路径一致）执行。

    空模块清单是刻意的：解释器身份核对不需要项目模块；模块加载来源核对属另一项，尚未接入。
    """

    def verify(workdir: str, environment: EnvironmentResolutionFact) -> str:
        fact = PythonLoadSourceProbe(executable=environment.executable_path).probe(
            materialized_root=workdir,
            modules=(),
        )
        return fact.executable

    return verify


def _module_names_from_project(
    reader: RecordReader, project_id: str, project_revision: object, workdir: str
) -> tuple[str, ...]:
    """从项目登记推导**可导入模块名**（落地方式见交接说明 §6.2 第 1 点）。

    合同的 `Module.source_paths` 是「实际变化的文件 → 模块」的映射模式（示例：
    `src/ticket/**`、`src/ticket`、`tests/ticket`），**不是**模块名，也没有单独的「导入根」字段。
    因此这里不新增字段、也不猜布局，而是**按探针自己的 sys.path 推导**：探针以物化根作为唯一
    PYTHONPATH（见 `PythonLoadSourceProbe`），所以把 `source_paths` 去掉尾部通配后得到的相对目录，
    其在物化根下的点号名就是可导入名（PEP 420 命名空间包同样成立）。

    三条好处：① 推导出的名字**必然可被探针导入**，不会因此假阻塞；② 探针报告的实际加载路径
    若落在物化根之外（例如解析到 editable 安装或原仓库），`_derive_state` 会判
    `import_outside_materialized_source` 而阻塞；③ 推导不出的模块**不参与探测**，不伪造结论。

    只在物化根内**确实存在**且含 `.py` 的目录才计入，否则视为不可核对。
    """

    if type(project_revision) is not int or project_revision < 1:
        raise SourceBindingUnverified("frozen preparation has no exact project revision")
    project = load_project(reader, project_id=project_id, revision=project_revision)
    root = Path(workdir).resolve()
    names: set[str] = set()
    for module in project.modules:
        for pattern in module.source_paths:
            relative = _pattern_relative_dir(pattern)
            if relative is None:
                continue
            candidate = root / relative
            if not candidate.is_dir() or not any(candidate.rglob("*.py")):
                continue
            names.add(".".join(relative.parts))
    return tuple(sorted(names))


def _pattern_relative_dir(pattern: object) -> Path | None:
    """把 `source_paths` 的一个模式折成相对目录；含通配或上跳时返回 `None`。"""

    if not isinstance(pattern, str) or not pattern.strip():
        return None
    raw = pattern.strip().replace("\\", "/").rstrip("/")
    # 绝对路径不是「项目内的相对目录」，直接拒绝——不能靠后续 split 把前导分隔符吃掉。
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        return None
    while raw.endswith(("/**", "/*")):
        raw = raw[: raw.rfind("/")]
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." or "*" in part or "?" in part for part in parts):
        return None
    return Path(*parts)


def _required_source_analysis(dependencies: BUseCaseDependencies) -> SourceAnalysisService:
    """默认 start 来源链要求的 B 来源分析能力；缺失即显式拒绝，不静默降级。"""

    source_analysis = dependencies.source_analysis
    if source_analysis is None:
        raise SourceBindingUnverified("the source analysis capability is not configured")
    return source_analysis


def _technical_identity(saved: Mapping[str, object]) -> str:
    """已核对的业务快照记录 → 技术固定清单身份（键缺失/空值即拒绝）。"""

    technical = saved.get("pinned_snapshot_id")
    if not isinstance(technical, str) or not technical.strip():
        raise SourceBindingUnverified(
            "the saved business snapshot has no technical pinned identity"
        )
    return technical


def _frozen_step_timeout(
    reader: RecordReader, project_id: str, environment_id: str, revision: int
) -> int | None:
    """权威环境记录里声明的步骤截止（秒）。

    与 `ExecutionAuthorizationService._frozen_step_deadline_ms` 同一权威来源：冻结准备记录的
    `EnvironmentRefFact` 投影不带该字段，只读投影会让解析器算出 `None`、授权侧算出真实毫秒数，
    默认链因此在"未冻结的截止"处阻塞。声明缺失返回 `None`（历史事实，不是新的执行依据）。
    """

    from aitest.application.planning.substrate import read_scoped_record

    record = read_scoped_record(
        reader,
        project_id=project_id,
        aggregate_kind="environment",
        record_id=environment_id,
        revision=revision,
    )
    declared = record.payload.get("step_timeout_seconds")
    if declared is None:
        return None
    if type(declared) is not int or declared <= 0:
        raise ValueError("the frozen environment step timeout cannot be verified")
    return declared


def _pinned_snapshot_identity(
    source_analysis: SourceAnalysisService,
    project_id: str,
    source_snapshot_id: str,
    record_revision: int,
) -> str:
    """业务 `SourceSnapshot` 记录 → **技术固定清单身份**（C-15 的权威映射）。

    依据 AB-001 1.21/1.22 与 BC-001 第 6 节：B 保存不可变**业务**快照（`snapshot_id` 带
    项目/绑定修订命名空间）、`content_identity` 与 `pinned_snapshot_id`；物化端口只认技术
    `snap-*`。`SourceAnalysisService.verify_saved` 会按准确修订读回业务记录、重新核对固定
    blob 与业务清单逐项一致，再由它交回技术身份——既不做"业务 ID 当前缀"的猜测，也不跳过
    固定字节复核。
    """

    payload = source_analysis.verify_saved(
        project_id=project_id, snapshot_id=source_snapshot_id, revision=record_revision
    )
    return _technical_identity(payload)


def _pinned_file_digests(pinned: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    """技术固定清单的逐文件身份（`verify_pinned` 已重新核对 blob 后才交回）。"""

    raw = pinned.get("files")
    if not isinstance(raw, (list, tuple)) or not raw:
        raise SourceBindingUnverified("the pinned source has no frozen file manifest")
    entries: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise SourceBindingUnverified("a pinned manifest entry is unreadable")
        name, digest, size = item.get("relative_path"), item.get("sha256"), item.get("size")
        if not isinstance(name, str) or not name.strip() or type(size) is not int:
            raise SourceBindingUnverified("a pinned manifest entry lacks its exact identity")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise SourceBindingUnverified("a pinned manifest entry digest is invalid")
        entries.append(
            {
                "relative_path": name,
                "size": size,
                "content_digest": "sha256:" + digest,
            }
        )
    return tuple(entries)


def _observed_content_identity(saved: Mapping[str, object], pinned: Mapping[str, object]) -> str:
    """用**重新核对过的固定字节**重算业务内容身份（不回声冻结值）。

    只核摘要或只比"记录里写的身份"都是空转：这里把技术清单的实际逐文件摘要放回 B 冻结的
    形态身份（git 基准提交/差异摘要或 plain 清单摘要）与范围声明，按 `source_content_identity`
    唯一口径重算。重算值与冻结 `content_identity` 不符即 `source_digest_mismatch`。
    """

    try:
        manifest = source_manifest_from_payload(saved)
    except ValueError as error:
        raise SourceBindingUnverified(f"saved source manifest is unreadable: {error}") from error
    files = _pinned_file_digests(pinned)
    try:
        observed = replace(
            manifest,
            files=tuple(
                SourceFileDigest(
                    relative_path=str(item["relative_path"]),
                    size=cast(int, item["size"]),
                    content_digest=str(item["content_digest"]),
                )
                for item in files
            ),
        )
    except ValueError as error:
        raise SourceBindingUnverified(f"pinned file identity is invalid: {error}") from error
    return source_content_identity(observed)


def _module_expectations(
    reader: RecordReader,
    project_id: str,
    project_revision: object,
    workdir: str,
    pinned: Mapping[str, object],
) -> tuple[tuple[str, ...], dict[str, str]]:
    """登记模块 → 冻结期望摘要（只对冻结清单里**确有文件型来源**的模块给期望）。

    `_module_names_from_project` 已保证推导出的名字都存在于物化根内；这里再把每个名字映射到
    冻结清单中的实际文件（常规包取 `__init__.py`）。命名空间包在清单里没有文件，交由
    `evaluate_source_binding` 之外的观测事实按"无文件且无错误"处理。
    """

    names = _module_names_from_project(reader, project_id, project_revision, workdir)
    digests = {
        str(item["relative_path"]): str(item["content_digest"]).removeprefix("sha256:")
        for item in _pinned_file_digests(pinned)
    }
    expected: dict[str, str] = {}
    for name in names:
        relative = name.replace(".", "/")
        digest = digests.get(f"{relative}/__init__.py")
        if digest is not None:
            expected[name] = digest
    return names, expected


def _probe_output_reference(
    objects: FileObjectStore | None, project_id: str, fact: object
) -> StoredObjectRef | None:
    """永久保存安全探针捕获流，返回包含摘要、大小和路径的完整对象引用。

    关闭条件要求"永久原始输出"：逐模块/语法结论必须能回到原始事实，而不是只有汇总状态。
    捕获流含协议包裹和模块 stdout，使用 text/plain；对象不可变且可按摘要回读。
    """

    raw = getattr(fact, "raw_output", b"")
    if objects is None or not isinstance(raw, bytes) or not raw:
        return None
    stored = objects.publish_bytes(project_id, raw, media_type="text/plain; charset=utf-8")
    return stored


def _source_verification_provider(
    snapshots: SourceSnapshotPort,
    reader: RecordReader,
    source_analysis: SourceAnalysisService,
    objects: FileObjectStore | None = None,
    *,
    instance_id: str | None = None,
) -> Callable[[str, Mapping[str, object], Mapping[str, object]], ExecutionSourceVerification]:
    """装配层适配：合成「来源核验事实」（架构01 第 12 节 / P1-FR08 / P1-AC13）。

    这是**一期**要求：需求 P1-FR08 明确 L1 覆盖「Python 语法、适用构建、加载或最小启动」，
    P1-AC13 要求 start 准入核对实际来源。

    **本轮实现 `SYNTAX` + `LOAD` 两类，并说明仍未覆盖的一点**：

    1. `MINIMAL_START`／`APPLICABLE_BUILD` 需要显式构建配置与最小启动脚本来源，合同尚未冻结
       该字段来源，故仍不实现；这是**已知的范围缺口**，在登记里如实记录，不得当成完整 L1 通过。
    2. 有登记模块时用 `LOAD`（真实导入 + 逐模块摘要），无登记模块时用 `SYNTAX`（实际编译
       物化范围内全部 `.py` 文件）；两类都**实际执行**，不再只核"文件存在/摘要非空"。

    **非空转**：
    - 业务 `SnapshotRef` → 技术 `pinned_snapshot_id` 走 `SourceAnalysisService.verify_saved`
      （按项目与准确修订读回并逐 blob 复核），不再把业务 ID 当技术 ID（C-15）；
    - `observed_source_digest` 由重新核对过的固定字节按唯一内容身份口径**重算**，不回声冻结值；
    - 语法在**登记解释器**下逐个 `.py` 实际 `compile`；每个登记模块的实际加载文件/摘要/失败
      全部进入观测事实，由 `_derive_state` 逐项判定（C-16）；
    - 入口路径取自物化的期望→实际映射（`paths[*].actual_path`，由 `resolve` 保证落在固定
      workdir 内）。映射摘要一致性**已由** `StartSourceBindingResolver.resolve` 重算比对，
      此处不重复声称。
    """

    service = SourceVerificationService()

    def provide(
        workdir: str,
        admitted: Mapping[str, object],
        prepared: Mapping[str, object],
    ) -> ExecutionSourceVerification:
        project_id = _required_text(prepared.get("project_id"), "project_id")
        snapshot = prepared.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise SourceBindingUnverified("frozen preparation has no snapshot to verify")
        business_id = _required_text(
            snapshot.get("source_snapshot_id"), "snapshot.source_snapshot_id"
        )
        record_revision = _positive_int(
            snapshot.get("record_revision", 1), "snapshot.record_revision"
        )
        expected_identity = _required_text(
            snapshot.get("content_identity"), "snapshot.content_identity"
        )
        # 一次权威读取：既给出技术身份，也用同一份业务记录重算内容身份。
        saved = source_analysis.verify_saved(
            project_id=project_id, snapshot_id=business_id, revision=record_revision
        )
        technical_id = _technical_identity(saved)
        pinned = snapshots.verify_pinned(technical_id)
        observed_identity = _observed_content_identity(saved, pinned)
        environment = prepared.get("environment")
        if not isinstance(environment, Mapping):
            raise SourceBindingUnverified("frozen preparation has no environment")
        resolution = environment.get("resolution")
        if not isinstance(resolution, Mapping):
            raise SourceBindingUnverified("frozen environment has no resolved carrier")
        interpreter = _required_text(
            resolution.get("executable_path"), "environment.resolution.executable_path"
        )
        entry_path = _entry_actual_path(admitted.get("paths"), workdir, prepared)
        plan_revision = prepared.get("plan_revision")
        if not isinstance(plan_revision, Mapping):
            raise SourceBindingUnverified("frozen preparation has no plan revision")
        # 登记模块的实际加载来源：以探针自己的 sys.path（物化根）推导可导入名并真的导入。
        # 推导不出任何模块时不伪造结论，退回只覆盖「语法」一类的 SYNTAX。
        module_names, expected_modules = _module_expectations(
            reader, project_id, prepared.get("project_revision"), workdir, pinned
        )
        try:
            fact = PythonLoadSourceProbe(executable=interpreter).probe(
                materialized_root=workdir, modules=module_names
            )
        except PythonSourceProbeError as error:
            # 探针起不来（登记解释器不可执行/超时/协议被破坏）是**来源取证缺口**，
            # 不是可以逃逸的内部错误：按 DEC-012 归为不可重试的来源未核实阻塞。
            fact = LoadSourceFact(
                requested_executable=interpreter,
                executable=interpreter,
                version="",
                prefix="",
                pythonpath_env=None,
                sys_path=(),
                project_scope_entries=(),
                modules=(),
                probe_error=str(error),
            )
        raw_object = _probe_output_reference(objects, project_id, fact)
        raw_reference = raw_object.relative_path if raw_object is not None else None
        observed_modules: list[SourceModuleFact] = []
        modules_by_name = {item.module: item for item in fact.modules}
        for name in module_names:
            item = modules_by_name.get(name)
            observed_modules.append(
                SourceModuleFact(
                    module=name,
                    expected_digest=expected_modules.get(name),
                    loaded_file=item.file if item is not None else None,
                    observed_digest=item.sha256 if item is not None else None,
                    load_error=(
                        item.load_error
                        if item is not None
                        else "the probe reported no fact for this registered module"
                    ),
                )
            )
        loaded_files = [
            item.file
            for item in fact.modules
            if item.file is not None and item.load_error is None and item.module in expected_modules
        ]
        # `LOAD` 只在**确有文件型登记的模块**时使用；纯命名空间包没有文件型加载来源，
        # 退回 `SYNTAX`（仍然实际编译范围内全部 .py），不伪造一个 load 引用。
        check_type = SourceCheckType.LOAD if expected_modules else SourceCheckType.SYNTAX
        import_ref = loaded_files[0] if loaded_files else None
        observation = SourceProbeObservation(
            observed_source_digest=observed_identity,
            observed_entry_ref=entry_path,
            observed_import_ref=import_ref,
            # 实际解释器取探针自报的 realpath，**不是**冻结值回声；冻结值作为期望侧比对。
            observed_interpreter_ref=fact.executable,
            failure_class=(
                FailureClass.ENVIRONMENT_UNREACHABLE
                if fact.probe_error is not None
                else (FailureClass.SOURCE_ERROR if fact.syntax_errors else None)
            ),
            raw_output_evidence_ref=raw_reference,
            observed_python_file_count=len(getattr(fact, "python_files", ())),
            observed_syntax_errors=tuple(
                SourceSyntaxErrorFact(relative_path=item.relative_path, reason=item.reason)
                for item in fact.syntax_errors
            ),
            observed_modules=tuple(observed_modules),
            probe_error=fact.probe_error,
        )
        request = SourceCheckRequest(
            verification_id="source-verification:" + technical_id,
            check_result_id="source-check:" + technical_id,
            project_id=project_id,
            attempt_id="pending-attempt",
            plan_revision_ref=PlanRevisionRef(
                revision_id=_required_text(plan_revision.get("revision_id"), "plan.revision_id"),
                revision_no=_positive_int(plan_revision.get("revision_no"), "plan.revision_no"),
                digest=_required_text(plan_revision.get("digest"), "plan.digest"),
            ),
            expected_source_content_digest=expected_identity,
            materialized_snapshot_ref=workdir,
            check_type=check_type,
            scope=workdir,
            source_snapshot_ref=technical_id,
            environment_ref=_environment_ref(environment),
            rules_revision=_rules_revision(prepared),
            adapter_version=_frozen_adapter_version(prepared),
            observed_source_digest=observed_identity,
            observed_entry_ref=entry_path,
            observed_import_ref=import_ref,
            expected_interpreter_ref=interpreter,
            raw_output_evidence_ref=raw_reference,
        )
        verification = service.verify(request, observation)
        if raw_object is not None and raw_object.digest != (
            "sha256:" + hashlib.sha256(fact.raw_output).hexdigest()
        ):
            verification = replace(
                verification, state=SourceVerificationState.UNVERIFIED,
                gap_ids=(*verification.gap_ids, "source_probe_material_redacted"),
            )
        return replace(
            verification, raw_output_object=raw_object, source_instance_id=instance_id,
        )

    return provide


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceBindingUnverified(f"{name} must be nonempty text")
    return value


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise SourceBindingUnverified(f"{name} must be a positive integer")
    return value


def _entry_actual_path(
    paths: object, workdir: str, prepared: Mapping[str, object] | None = None
) -> str:
    """取物化的期望→实际映射里的入口实际路径（必须落在固定 workdir 内）。

    优先取**冻结入口参数**指向的文件（`execution_source.entry_arguments` 里的相对路径），
    而不是清单里的第一个文件：入口身份必须来自冻结事实，取任意文件只是"恰好存在"。
    冻结参数没有落入物化清单时回落到清单中的第一个文件（仍必须落在固定 workdir 内）。
    """

    if not isinstance(paths, (list, tuple)) or not paths:
        raise SourceBindingUnverified("materialization has no expected→actual path mapping")
    root = Path(workdir).resolve()
    wanted: set[str] = set()
    if isinstance(prepared, Mapping):
        source = prepared.get("execution_source")
        arguments = source.get("entry_arguments") if isinstance(source, Mapping) else None
        if isinstance(arguments, (list, tuple)):
            wanted = {
                item.replace("\\", "/").lstrip("./")
                for item in arguments
                if isinstance(item, str) and item.strip()
            }

    def _inside(actual: str) -> str | None:
        candidate = Path(actual)
        if candidate.is_absolute() and candidate.is_relative_to(root) and candidate.is_file():
            return str(candidate)
        return None

    fallback: str | None = None
    for item in paths:
        if not isinstance(item, Mapping):
            continue
        actual = item.get("actual_path")
        relative = item.get("relative_path")
        if not isinstance(actual, str) or not actual.strip():
            continue
        resolved = _inside(actual)
        if resolved is None:
            continue
        if fallback is None:
            fallback = resolved
        if isinstance(relative, str) and relative.replace("\\", "/") in wanted:
            return resolved
    if fallback is not None:
        return fallback
    raise SourceBindingUnverified("no entry path inside the fixed workdir to verify")


def _environment_ref(environment: Mapping[str, object]) -> str:
    environment_id = environment.get("environment_id")
    revision = environment.get("revision")
    if not isinstance(environment_id, str) or not environment_id.strip():
        raise SourceBindingUnverified("frozen environment has no identity")
    if type(revision) is not int or revision < 1:
        raise SourceBindingUnverified("frozen environment has no exact revision")
    return f"{environment_id}@{revision}"


def _frozen_adapter_version(prepared: Mapping[str, object]) -> str:
    """冻结的适配器版本：取 `execution_source.adapter_versions` 的唯一取值。

    与 `_adapter_kind` 同源（唯一键）。
    """

    source = prepared.get("execution_source")
    if not isinstance(source, Mapping):
        raise SourceBindingUnverified("frozen preparation has no execution source binding")
    versions = source.get("adapter_versions")
    if not isinstance(versions, Mapping) or not versions:
        raise SourceBindingUnverified("frozen binding has no adapter versions")
    unique = {str(value) for value in versions.values()}
    if len(unique) != 1:
        raise SourceBindingUnverified("frozen binding has ambiguous adapter versions")
    return next(iter(unique))


def _rules_revision(prepared: Mapping[str, object]) -> str:
    versions = prepared.get("rule_versions")
    if not isinstance(versions, (list, tuple)):
        raise SourceBindingUnverified("frozen preparation has no rule versions")
    payload = sorted(
        json.dumps(dict(item), sort_keys=True, ensure_ascii=False) for item in versions
    )
    return "rules:" + hashlib.sha256("\n".join(payload).encode("utf-8")).hexdigest()


def assemble_workspace_core(
    workspace_root: Path,
    *,
    instance_id: str,
    workspace_id: str | None = None,
    extra_handlers: Mapping[str, Handler] | None = None,
    connection_endpoint: str | None = None,
    model_endpoint: str | None = None,
    model_secret_reference: tuple[str, str] | None = None,
    secret_manager: SecretManager | None = None,
    extra_action_dependencies: Mapping[str, tuple[str, ...]] | None = None,
    environment_resolver: EnvironmentResolver | None = None,
    execution_action_resolver: ExecutionActionResolver | None = None,
    execution_port: ExecutionPort | None = None,
    business_verification_resolver: BusinessVerificationResolver | None = None,
    business_verification_port: BusinessVerificationCapturePort | None = None,
    migration_backup_root: Path | None = None,
    verify_start_interpreter: bool = True,
    verify_start_source: bool = True,
    execution_enabled: bool = True,
) -> CoreAssembly:
    """装配唯一核心：启动恢复 → 文件底座 → B 用例自动接线 → 注册表叠加。

    A-02：此前 ``CoreBootstrap.create`` 只把 :class:`FileUnitOfWork` 交给
    ``LocalAPI(transaction_port=...)``，注册的 handler 拿不到工作单元与只读
    仓储；跨进程的 ``core_worker`` 子进程更是全新进程，注册表为空且不派发
    Command。本函数是 A 所有的**唯一装配路径**：

    - 服务前先跑 :class:`RecoveryOrchestrator`，闭合上一核心崩溃残留的活动
      标记与落后投影；``blocked`` 时抛 :class:`CoreAssemblyBlocked`，不带病服务；
    - B 的用例经 ``substrate_adapter`` 窄转接头闭包**本工作空间**的
      ``FileUnitOfWork`` / ``FileRecordRepository``（提交序号取 A-01 冻结的
      正式访问器），无需调用方手工注入；
    - C/D 用例继续走 :class:`UseCaseRegistry`，经 ``extra_handlers`` 叠加，
      与默认动作同名时拒绝（动作归属唯一）。
    """
    FileCommitStore.reject_links(workspace_root)
    root = workspace_root.resolve()
    workspace = Workspace(root)
    if workspace_id is not None:
        workspace.validate(workspace_id)
    # 全生命周期唯一写入者（A-02）：先取得排他写锁再跑恢复与服务装配，
    # 同进程/跨进程的同根第二核心在此被拒绝；恢复 blocked 时释放锁退出。
    lifetime_lock = workspace.admit_lifetime()
    try:
        try:
            journal = FileEventJournal(root, instance_id=instance_id)
            recovery = RecoveryOrchestrator(root, instance_id=instance_id).run(startup=True)
        except (OSError, ValueError) as error:
            raise CoreAssemblyBlocked("提交与恢复材料无法核实，核心拒绝启动") from error
        if recovery.state == "blocked":
            raise CoreAssemblyBlocked(f"工作空间恢复 blocked，核心拒绝启动: {recovery.actions}")

        # 格式变化仍在生命周期写锁内，且必须经过可校验备份与活动守卫。
        try:
            if migration_backup_root is None:
                configured_root = os.environ.get("AITEST_MIGRATION_BACKUP_ROOT")
                if configured_root is not None:
                    migration_backup_root = Path(configured_root)
            migrations = FileMigrationManager(root, backup_root=migration_backup_root)
            migration_plan = migrations.plan(
                (
                    "0003-sharded-record-authority",
                    "0004-bounded-query-directory",
                    "0005-complete-commit-closure",
                    "0006-canonical-current-publication",
                    "0007-canonical-manifest-and-business-changes",
                )
            )
            migration_report = migrations.apply(migration_plan.plan_id)
        except (OSError, ValueError, MigrationError) as error:
            raise CoreAssemblyBlocked("提交材料迁移无法核实，核心拒绝启动") from error
        if migration_report.state == "blocked":
            raise CoreAssemblyBlocked("权威分片迁移被未核实活动阻塞，核心拒绝启动")

        from .infrastructure.file_store.publication_backend import FilePublicationBackend

        try:
            FilePublicationBackend(root).confirm_current()
        except OSError as error:
            raise CoreAssemblyBlocked("发布后端保存能力无法核实，核心拒绝新写入") from error

        unit_of_work = FileUnitOfWork(
            root, journal=journal, expected_writer_epoch=workspace.identity["writer_epoch"]
        )
        ports_unit_of_work = PortsUnitOfWork(
            unit_of_work,
            repository=unit_of_work.repo,
            sequence=unit_of_work,
        )
        reader = PortsRecordReader(unit_of_work.repo)
        # A-10：默认装配真实凭据/来源能力与动作级能力门。能力门在
        # LocalAPI 构造前建立，连接水合结论与各能力条件随装配确定。
        gate = CapabilityGate(
            clock=lambda: datetime.now(UTC).isoformat(),
            state_store=FileCapabilityConditionStore(root, workspace_id=workspace.workspace_id),
        )
        secret_manager = secret_manager if secret_manager is not None else SecretManager.default()
        gate.configure(SECRET)
        snapshot_store = FileSourceSnapshotStore(root)
        source_probe = PythonLoadSourceProbe()
        gate.configure(SOURCE)

        connector: LocalAPIConnectionBridge | None = None
        if connection_endpoint is not None:
            # 真实目标接线（A-10）：探测事实持久化到工作空间，核心换实例后
            # test_connection 的历史结论仍可恢复；未配置目标时不挂连接器，
            # doctor/业务用例等不依赖外网的动作不受影响（动作级降级）。
            from aitest.interfaces.local.pipe import current_user_sid

            endpoint_config = EndpointConfig.from_address(connection_endpoint)
            bridge = LocalAPIConnectionBridge(
                endpoint_config,
                ConnectionFactStore(root),
                source_session=current_user_sid() or "",
            )
            connector = bridge
            # 换核心/重连验收：新实例装配即按台账最近事实确定连接条件，
            # 上次未恢复的故障分类不被重置，最近成功则视为就绪。
            try:
                recovered_state = bridge.load()
            except (ValueError, OSError):
                gate.report(
                    CONNECTION,
                    healthy=False,
                    reason="连接事实无法核实",
                    classification="storage",
                )
            else:
                if recovered_state is None:
                    gate.configure(CONNECTION)
                elif recovered_state.get("connected") is True:
                    # Probe records are the source; the automatic gate condition
                    # is a projection which can predate the latest saved probe.
                    gate.report(CONNECTION, healthy=True)
                else:
                    gate.report(
                        CONNECTION,
                        healthy=False,
                        reason=str(recovered_state.get("last_error") or "目标未就绪"),
                        classification=str(recovered_state.get("last_error_kind") or "transport"),
                    )

        model_provider: HttpModelProvider | None = None
        model_credentials: ModelCredentialResolver | None = None
        if model_endpoint is not None:
            # 模型能力必须显式装配：端点与已解析凭据同时具备才构造真实
            # provider；凭据解析失败只降级模型能力，不阻断核心启动。
            resolved_secret = None
            if model_secret_reference is not None:
                purpose, reference = model_secret_reference
                try:
                    resolved_secret = secret_manager.resolve(reference, purpose=purpose)
                except Exception as error:
                    gate.report(
                        MODEL,
                        healthy=False,
                        reason=f"模型凭据不可解析: {error}",
                        classification="auth",
                    )
            if resolved_secret is not None and resolved_secret.purpose == "model":
                model_provider = HttpModelProvider(
                    model_endpoint,
                    secret=resolved_secret,
                    on_result=lambda result: gate.report(
                        MODEL,
                        healthy=result.status.value == "ok",
                        reason=result.error_detail,
                        classification=result.error_kind,
                    ),
                )
                model_credentials = ModelCredentialResolver(resolved_secret)
                gate.configure(MODEL)
            # 凭据解析失败已在 except 中写入自动 degraded（auth）事实，
            # 后续成功事实可自动恢复；未提供凭据引用则保持 not_configured
            # （缺配置）。两者都不是操作者意图，不得落人工降级——人工
            # 降级只能显式 restore，会把临时凭据故障永久钉死。

        gate.require_if(
            "generate_draft", parameter="generation_mode", equals="model", keys=(MODEL, SECRET)
        )
        # AB-001 第 8.16.3 节第 4 条：动作级能力声明。`request_model_draft` 依赖真实模型
        # 调用与凭据解析（两者未配齐时按“未配置”降级，由能力门与 handler 双重把关）；
        # `revise_pending_steps` / `narrow_driver` 是纯规则（执行事实随命令带来），
        # 无外部能力依赖，因此不声明。
        gate.require("request_model_draft", MODEL, SECRET)
        actors = CoreActorContext()

        class ApprovalIdentity:
            def create(self) -> str:
                return "approval-challenge-" + uuid4().hex

        policy_resolver = SavedModelPolicyApprovalResolver(
            unit_of_work.repo,
            workspace.workspace_id,
            "model:"
            + (
                model_secret_reference[1]
                if model_secret_reference is not None and model_secret_reference[0] == "model"
                else "not_configured"
            ),
        )
        write_resolver = SavedControlledWriteResolver(unit_of_work.repo, workspace.workspace_id)
        execution_resolver = SavedExecutionAuthorizationResolver(
            cast(RecordRepository, unit_of_work.repo), workspace.workspace_id
        )
        human_resolver = SavedHumanActionResolver(
            SavedBasisApprovalResolver(unit_of_work.repo, workspace.workspace_id),
            policy_resolver,
            write_resolver,
            execution_resolver,
        )
        approvals = ApprovalService(
            unit=unit_of_work,
            records=unit_of_work.repo,
            actors=actors,
            resolver=human_resolver,
            identities=ApprovalIdentity(),
            clock=SystemClock(),
            workspace_id=workspace.workspace_id,
        )
        policy_confirmations = ModelPolicyConfirmationService(
            unit_of_work, approvals, policy_resolver
        )
        controlled_writes = ControlledWriteService(unit_of_work, approvals, write_resolver)
        write_resolver.proof = controlled_writes
        environment_resolution = EnvironmentResolutionService(
            reader=reader,
            resolver=environment_resolver or HostConfiguredEnvironmentResolver(),
        )
        dependencies = BUseCaseDependencies(
            unit_of_work=ports_unit_of_work,
            reader=reader,
            clock=SystemClock(),
            model_provider=model_provider,
            model_credentials=model_credentials,
            material_projector=SafeMaterialProjector(),
            workspace_id=workspace.workspace_id,
            record_protector=lambda value: cast(Mapping[str, object], guard_value(value)[0]),
            source_analysis=SourceAnalysisService(
                reader=reader,
                unit_of_work=unit_of_work,
                snapshots=snapshot_store,
                source_control=GitSourceControl(),
                source_available=lambda: gate.condition(SOURCE).state.value == "ready",
                controlled_writes=controlled_writes,
            ),
            basis_confirmations=BasisConfirmationService(
                reader=reader, unit=unit_of_work, approvals=approvals
            ),
            basis_confirmation_proof=approvals,
            model_policy_confirmations=policy_confirmations,
            model_policy_proof=policy_confirmations,
            controlled_writes=controlled_writes,
            controlled_write_proof=controlled_writes,
            environment_resolution=environment_resolution,
            model_responses=FileModelResponseStore(
                root, writer_epoch=workspace.identity["writer_epoch"]
            ),
        )
        assert dependencies.source_analysis is not None
        write_resolver.sources = dependencies.source_analysis
        initial_run_registration = InitialRunRegistration(
            unit=unit_of_work,
            reader=reader,
            records=cast(RecordRepository, unit_of_work.repo),
            workspace_id=workspace.workspace_id,
            source_analysis=dependencies.source_analysis,
            approvals=approvals,
            controlled_writes=controlled_writes,
            environment_resolution=environment_resolution,
        )
        handlers: dict[str, Handler] = dict(b_registration_for(dependencies))
        assert dependencies.source_analysis is not None
        execution_authorizations = ExecutionAuthorizationService(
            unit=unit_of_work,
            records=cast(RecordRepository, unit_of_work.repo),
            reader=reader,
            workspace_id=workspace.workspace_id,
            resolver=execution_resolver,
            approvals=approvals,
            source=dependencies.source_analysis,
            environment=environment_resolution,
            action_resolver=(
                execution_action_resolver
                if execution_action_resolver is not None
                else build_saved_action_resolver(
                    snapshots=snapshot_store,
                    workspace_root=root,
                    read_facts=lambda project, run: (
                        execution_coordinator.read_runtime_revision_facts(
                            project_id=project, run_id=run
                        )
                    ),
                    pinned_snapshot_ref=lambda project, snapshot, revision: (
                        _pinned_snapshot_identity(
                            _required_source_analysis(dependencies), project, snapshot, revision
                        )
                    ),
                    read_step_timeout=lambda project, environment, revision: _frozen_step_timeout(
                        reader, project, environment, revision
                    ),
                    interpreter_verifier=(
                        _interpreter_verifier() if verify_start_interpreter else None
                    ),
                    source_verification_provider=(
                        _source_verification_provider(
                            snapshot_store,
                            reader,
                            dependencies.source_analysis,
                            FileObjectStore(root),
                            instance_id=instance_id,
                        )
                        if verify_start_source
                        else None
                    ),
                )
            ),
            controlled_writes=controlled_writes,
        )
        execution_coordinator = ExecutionCommitCoordinator(
            unit_of_work,
            records=cast(RecordRepository, unit_of_work.repo),
            approvals=approvals,
            controlled_writes=controlled_writes,
            execution_authorizations=execution_authorizations,
            serial_execution=True,
            evidence_collector=SavedExecutionEvidence(
                cast(RecordRepository, unit_of_work.repo),
                reader,
                FileSpoolStore(root),
                FileObjectStore(root),
                workspace_id=workspace.workspace_id,
                instance_id=instance_id,
            ),
            source_objects=FileObjectStore(root),
        )
        step_execution = SavedStepExecution(
            execution_authorizations,
            execution_coordinator,
            FileSpoolStore(root),
            # 默认执行端口（契约：端口绑定固定快照、登记入口、参数数组与环境；命令始终是
            # argv 数组、`shell=False`）。`ExecutionRequest` 恰好携带这些且已经过准入链核对
            # （实际入口、实际解释器、来源核验、固定 workdir），故由
            # `RegisteredEntryExecutionAdapter` 在首次 `start` 时从请求派生登记命令。
            #
            # **显式关闭仍然可用**：`execution_enabled=False`（且未传 `execution_port`）时端口为
            # `None`，装配会如实报「未配置可信执行适配器」——这是刻意保留的合法配置，
            # 不因为"默认有端口"就变得不可达。
            execution_port
            if execution_port is not None
            else (RegisteredEntryExecutionAdapter(CommandAdapter(spool_store=FileSpoolStore(root)))
                  if execution_enabled else None),
        )
        execution_commands = ExecutionCommands(
            execution_authorizations, initial_run_registration, step_execution
        )
        reuse_inspection = CaseReuseInspection(
            execution_coordinator,
            cast(RecordRepository, unit_of_work.repo),
            reader,
            FileObjectStore(root),
            FileSpoolStore(root),
            basis_proof=approvals,
        )
        # 复用选择（裁定 DEC-014 / CORE-001 1.82）：只登记引用关系，不授予资格；
        # 同一核对器派生来源身份，报告侧据此把候选交给消费守卫。
        reuse_selection = SavedCaseReuseSelection(
            unit=unit_of_work,
            records=cast(RecordRepository, unit_of_work.repo),
            actors=actors,
            inspection=reuse_inspection,
        )
        run_control = SavedRunControl(execution_coordinator, step_execution, workspace.workspace_id)
        run_schedule = SavedRunSchedule(
            execution_coordinator, step_execution, workspace.workspace_id, unit_of_work.repo
        )
        if business_verification_resolver is None and business_verification_port is None:
            configured_queries = HostConfiguredBusinessQueries.from_environment(secret_manager)
            business_verification_resolver = SavedBusinessVerificationResolver(
                execution_authorizations, cast(RecordRepository, unit_of_work.repo),
                configured_queries.validate if configured_queries is not None else None,
                FileObjectStore(root),
            )
            business_verification_port = configured_queries
        business_verification = SavedBusinessVerification(
            execution_coordinator,
            execution_authorizations,
            FileObjectStore(root),
            workspace_id=workspace.workspace_id,
            instance_id=instance_id,
            resolver=business_verification_resolver,
            verifier=business_verification_port,
            protector=lambda value: cast(Mapping[str, object], guard_value(value)[0]),
        )
        external_imports = SavedExternalResultImport(
            execution_coordinator,
            FileObjectStore(root),
            workspace.workspace_id,
            lambda value: cast(Mapping[str, object], guard_value(value)[0]),
        )
        exit_guard = FileCoreExitGuard(
            root,
            lambda project, attempt: execution_coordinator.read_checkpoint(
                project_id=project, attempt_id=attempt
            ),
            lambda attempt: require_saved_output_material(
                FileSpoolStore(root), attempt, attempt.output_block_refs
            ),
        )
        runtime_actions = SavedRuntimeRevisionActions(execution_coordinator, approvals)
        human_resolver.runtime = runtime_actions
        execution_authorizations.runtime_origins = runtime_actions
        # 受控人工补证（P1-FR13/§4）：登记人工实际材料为永久证据，不判业务通过。
        manual_capture = SavedManualEvidenceCapture(
            coordinator=execution_coordinator,
            unit=unit_of_work,
            reader=reader,
            objects=FileObjectStore(root),
            workspace_id=workspace.workspace_id,
            protector=lambda value: guard_value(value)[0],
        )
        handlers.update(
            register_run=execution_commands.register,
            inspect_case_reuse=reuse_inspection.inspect,
            select_case_reuse=reuse_selection.apply,
            prepare_execution=execution_commands.prepare,
            authorize_step=execution_commands.grant,
            execute_step=execution_commands.execute,
            retry_step=execution_commands.retry,
            return_stage=execution_commands.return_stage,
            start_run=run_schedule.apply,
            verify_pending=business_verification.apply,
            import_external_result=external_imports.apply,
            attach_evidence=manual_capture.apply,
            pause_run=run_control.apply,
            resume_run=run_control.apply,
            cancel_run=run_control.apply,
            revise_pending_steps=runtime_actions.apply,
            narrow_driver=runtime_actions.apply,
        )

        def prepare_approval(command: Command) -> Mapping[str, object]:
            values = command.parameters
            raw = values.get("parameters")
            if (
                command.project_id is None
                or command.intent_id is None
                or not isinstance(raw, Mapping)
                or not all(
                    isinstance(values.get(key), str) and values[key]
                    for key in ("action_intent_id", "action", "target")
                )
            ):
                raise ValueError("approval preparation requires an exact action/intent/target")
            challenge = approvals.prepare(
                project_id=command.project_id,
                preparation_intent_id=command.intent_id,
                request_id=command.request_id,
                action_intent_id=cast(str, values["action_intent_id"]),
                action=cast(str, values["action"]),
                target=cast(str, values["target"]),
                parameters=cast(Mapping[str, object], raw),
            )
            from pydantic import TypeAdapter

            return cast(
                Mapping[str, object],
                TypeAdapter(type(challenge)).dump_python(challenge, mode="json"),
            )

        def revoke_approval(command: Command) -> Mapping[str, object]:
            identity = command.parameters.get("challenge_id")
            if (
                command.project_id is None
                or command.intent_id is None
                or not isinstance(identity, str)
                or not identity
            ):
                raise ValueError("revocation requires its exact project, intent and challenge")
            challenge = approvals.revoke(
                project_id=command.project_id,
                challenge_id=identity,
                request_id=command.request_id,
                intent_id=command.intent_id,
            )
            return {"challenge_id": challenge.challenge_id, "state": challenge.state.value}

        handlers.update(prepare_approval=prepare_approval, revoke_approval=revoke_approval)

        # 架构 05 第 3 节「查询/维护」组里的两个**只读**动作：实现早已存在
        # （FileMaintenanceService / FileEventJournal），此前只是没有注册，
        # 于是默认装配里 doctor 之外没有可用的查询/维护面。
        def storage_usage(command: Command) -> Mapping[str, object]:
            """只读空间诊断；工作空间级动作，不接受业务参数。"""
            del command
            return cast(Mapping[str, object], _json_value(FileMaintenanceService(root).diagnose()))

        def read_events(command: Command) -> Mapping[str, object]:
            """只读事件续读：游标非法由日志返回结构化状态，不猜测、不补造。"""
            values = command.parameters
            raw_limit = values.get("limit")
            if raw_limit is None:
                limit = 100
            elif (
                isinstance(raw_limit, bool)
                or not isinstance(raw_limit, int)
                or not 1 <= raw_limit <= 500
            ):
                raise BUseCaseError("B_INVALID_PARAMETER", "limit must be an integer in [1, 500]")
            else:
                limit = raw_limit
            raw_cursor = values.get("cursor")
            if raw_cursor is None:
                cursor = None
            elif isinstance(raw_cursor, str) and raw_cursor.strip():
                cursor = raw_cursor
            else:
                raise BUseCaseError("B_INVALID_PARAMETER", "cursor must be a non-empty string")
            return cast(Mapping[str, object], _json_value(journal.read(cursor=cursor, limit=limit)))

        handlers.update(storage_usage=storage_usage, events=read_events)

        # 受控备份入口（架构 04 §6/§9/§10，P1-FR16）：底层 FileBackupStore 早已实现冻结
        # 闭包、清单最后写与双向校验，此前缺的是"受控写身份 + 产品入口"。
        # 只做在线备份：维护模式的完整停机备份要求"停止接纳新写入"，没有维护模式
        # 状态可依赖时明确拒绝，而不是假装做到了。
        backup_entry = SavedBackupEntry(
            root=root,
            actors=actors,
            store=FileBackupStore(root),
            maintenance=FileMaintenanceService(root),
        )
        handlers.update(backup=backup_entry.apply)

        # 受控结构迁移入口（架构 04 §6/§9/§11，P1-FR16）：FileMigrationManager 已实现
        # inspect/plan/apply/resume/rollback 与"apply 前必须有可校验备份""活动未核实
        # 不得迁移"，此前缺的是受控入口与维护窗口。维护窗口期间入口适配器拒绝新的
        # 写动作（§142「进入维护状态暂停新写入」）。
        maintenance_mode = MaintenanceMode()
        migration_runner = FileMigrationManager(
            root, maintenance=FileMaintenanceService(root), backup_root=migration_backup_root
        )
        migration_entry = SavedMigrationEntry(
            runner=migration_runner,
            actors=actors,
            mode=maintenance_mode,
        )
        handlers.update(migrate=migration_entry.apply)

        # D 的问题生命周期（P1-FR15）：权威读取已提交执行事实 + 受控会话身份，
        # 经同一工作单元持久化问题记录与 issues.list 索引入口。
        # 位置必须在 `execution_coordinator` 之后：关闭依据要从它读同一快照事实。
        defect_management = SavedDefectManagement(
            unit=unit_of_work,
            reader=reader,
            records=cast(RecordRepository, unit_of_work.repo),
            clock=SystemClock(),
            actors=actors,
            runtime=execution_coordinator,
            workspace_id=workspace.workspace_id,
            objects=FileObjectStore(root),
            basis_proof=approvals,
        )
        # D 的报告/复核（P1-FR14）：判定由同一快照事实与冻结准备/范围派生，
        # 人工复核绑定准确报告修订；**不**由调用方声明结论或等级。
        report_management = SavedReportService(
            unit=unit_of_work,
            reader=reader,
            records=cast(RecordRepository, unit_of_work.repo),
            clock=SystemClock(),
            actors=actors,
            runtime=execution_coordinator,
            workspace_id=workspace.workspace_id,
            basis_proof=approvals,
            # 复用候选来源：已保存的复用选择（CORE-001 1.82）；资格仍由消费守卫派生。
            reuse_references=reuse_selection.references_for_run,
            reuse_basis=reuse_inspection.consumption_basis,
        )
        # 宿主只读视图（架构 06 §1/§54）：面板读 DTO，判定复用 D 侧 live_decision，
        # 不新增协议动作、不写任何记录。
        write_resolver.delivery_verification = DeliveryVerificationReader(
            report_management, cast(RecordRepository, unit_of_work.repo), FileObjectStore(root),
        )
        view_queries = CommittedViewReader(
            decisions=report_management,
            reader=reader,
            records=cast(RecordRepository, unit_of_work.repo),
            delivery_verification=write_resolver.delivery_verification,
        )
        handlers["read_view"] = view_queries.dispatch
        # D 的导出（P1-FR14 §5/§8）：正文与附件都要脱敏，制品先写事务临时位置、
        # 核对摘要后发布，发布成功**再**在工作单元登记导出记录。
        export_management = SavedExportService(
            unit=unit_of_work,
            reader=reader,
            records=cast(RecordRepository, unit_of_work.repo),
            clock=SystemClock(),
            actors=actors,
            runtime=execution_coordinator,
            workspace_id=workspace.workspace_id,
            artifacts=FileReportArtifactPort(root, instance_id=instance_id),
            projector=SafeMaterialProjector(),
            objects=FileObjectStore(root),
            instance_id=instance_id,
        )
        # 只读的复测简报（P1-FR14 §5 末段）：只投影文本、不写记录、不含证据正文。
        repair_briefs = SavedRepairBrief(
            records=cast(RecordRepository, unit_of_work.repo),
            runtime=execution_coordinator,
            projector=SafeMaterialProjector(),
        )
        handlers.update(
            d_registration_for(
                defect_management,
                reports=report_management,
                exports=export_management,
                briefs=repair_briefs,
            )
        )

        if extra_handlers:
            conflicts = sorted(handlers.keys() & extra_handlers.keys())
            if conflicts:
                raise ValueError(
                    f"registered use case conflicts with built-in actions: {conflicts}"
                )
            handlers.update(extra_handlers)

        if extra_action_dependencies:
            for action, keys in extra_action_dependencies.items():
                gate.require(action, *keys)

        def close_approval_session(session: Session) -> None:
            # Challenges can only be prepared on controlled interactive channels.
            # A same-named noninteractive channel must not even read their hints.
            if not session.interactive:
                return
            for project, challenge_id in approvals.pending_for_session(
                session.session_id, session.entry_kind
            ):
                actor = TrustedActor(
                    workspace.workspace_id,
                    project,
                    session.session_id,
                    session.entry_kind,
                    session.interactive,
                )
                with actors.bind(actor):
                    approvals.revoke(
                        project_id=project,
                        challenge_id=challenge_id,
                        request_id="close-challenge-" + uuid4().hex,
                        intent_id="close-session-" + challenge_id,
                    )

        api = LocalAPI(
            instance_id=instance_id,
            workspace_id=workspace.workspace_id,
            handlers=handlers,
            transaction_port=unit_of_work,
            connector=connector,
            connection_persistence=connector,
            capability_gate=gate,
            credential_projector=lambda value: cast(Mapping[str, object], guard_value(value)[0]),
            actors=actors,
            session_finalizer=close_approval_session,
            intent_contracts={action: RECORD_WRITE_SCHEMA for action in ORDINARY_WRITE_ACTIONS},
            maintenance=lambda: (
                maintenance_mode.reason if maintenance_mode.active
                else migration_runner.maintenance_reason()
            ),
        )
    except BaseException:
        lifetime_lock.release()
        raise
    return CoreAssembly(
        api=api,
        workspace=workspace,
        unit_of_work=unit_of_work,
        recovery=recovery,
        lifetime_lock=lifetime_lock,
        seal_inflight=lambda: seal_inflight_outputs(root),
        shutdown_blocker=exit_guard.blocker,
        gate=gate,
        secret_manager=secret_manager,
        model_provider=model_provider,
        snapshot_store=snapshot_store,
        source_probe=source_probe,
        initial_run_registration=initial_run_registration,
        environment_resolution=environment_resolution,
        execution_authorizations=execution_authorizations,
        execution_coordinator=execution_coordinator,
        step_execution=step_execution,
        run_schedule=run_schedule,
        continue_work=lambda: run_schedule.tick(run_control),
        business_verification=business_verification,
        external_imports=external_imports,
        run_control=run_control,
        runtime_actions=runtime_actions,
        model_policy_proof=policy_confirmations,
        maintenance_mode=maintenance_mode,
        view_queries=view_queries,
    )


class UseCaseRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._handlers: dict[str, Handler] = {}
        self._owners: dict[str, str] = {}

    def register(
        self,
        package: str,
        handlers: Mapping[str, Handler],
        *,
        closed: bool = False,
    ) -> None:
        if package not in {"B", "C", "D"}:
            raise ValueError("package must be B, C or D")
        if closed:
            raise RuntimeError("use case registration is closed after workspace assembly")
        with self._lock:
            for action, handler in handlers.items():
                if not action or not callable(handler):
                    raise ValueError("use case action must map to a callable")
                if action in self._handlers:
                    raise ValueError(f"use case already registered: {action}")
            self._handlers.update(handlers)
            self._owners.update({action: package for action in handlers})

    def snapshot(self) -> Mapping[str, Handler]:
        with self._lock:
            return MappingProxyType(dict(self._handlers))

    def owners(self) -> Mapping[str, str]:
        with self._lock:
            return MappingProxyType(dict(self._owners))


class CoreBootstrap:
    def __init__(self) -> None:
        self._lock = RLock()
        self._registry = UseCaseRegistry()
        self._instances: dict[Path, CoreInstance] = {}
        self._registration_closed = False

    @property
    def registry(self) -> UseCaseRegistry:
        return self._registry

    def register_use_cases(self, package: str, handlers: Mapping[str, Handler]) -> None:
        self._registry.register(package, handlers, closed=self._registration_closed)

    def create(self, workspace_root: Path) -> CoreInstance:
        FileCommitStore.reject_links(workspace_root)
        root = workspace_root.resolve()
        with self._lock:
            if root in self._instances:
                return self._instances[root]
            self._registration_closed = True
            assembly = assemble_workspace_core(
                root,
                instance_id=str(uuid4()),
                extra_handlers=dict(self._registry.snapshot()),
            )
            instance = CoreInstance(
                assembly.api.instance_id,
                assembly.workspace.workspace_id,
                assembly.api,
                assembly.lifetime_lock,
            )
            self._instances[root] = instance
            return instance

    def release(self, workspace_root: Path) -> None:
        """关闭指定工作空间的进程内核心并释放全生命周期写锁（A-02）。"""
        FileCommitStore.reject_links(workspace_root)
        root = workspace_root.resolve()
        with self._lock:
            instance = self._instances.pop(root, None)
        if instance is not None and instance.lifetime_lock is not None:
            instance.lifetime_lock.release()


def registered_use_cases() -> Mapping[str, Handler] | None:
    """全局注册表当前快照（C/D 用例）；无注册时返回 None。

    跨进程 worker 与进程内装配都只能经这条只读快照取得 C/D 动作，
    不能由连接帧自报入口类型或绕注册表注入 handler（A-02）。
    """
    snapshot = _GLOBAL_BOOTSTRAP.registry.snapshot()
    return dict(snapshot) if snapshot else None


class SystemProcessLauncher:
    """Serialize startup claims, verify process birth, then publish discovery.

    Repeated hosts wait for the same living child. Uncertain creation or process
    identity blocks another launch; only verified exit permits a new claim.
    The OS lifetime writer lock remains the business single-writer authority.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        python_executable: str | None = None,
        worker_module: str = _DEFAULT_WORKER_MODULE,
        instance_id_file: str = _INSTANCE_ID_FILE,
        env: Mapping[str, str] | None = None,
        process_probe: Callable[[int], ProcessFact] | None = None,
    ) -> None:
        FileCoreLaunchStore.reject_links(workspace_root)
        FileCommitStore.reject_links(workspace_root)
        self._root = workspace_root.resolve()
        if not valid_instance_filename(instance_id_file):
            raise WorkspaceInUse("core discovery pointer must be a workspace filename")
        self._python = python_executable or sys.executable
        self._module = worker_module
        self._id_path = self._root / instance_id_file
        self._env: dict[str, str] | None = dict(env) if env is not None else None
        self._launches = FileCoreLaunchStore(self._root)
        self._probe = process_probe or probe_process
        self._children: dict[str, subprocess.Popen[bytes]] = {}

    def start(self, workspace_id: str) -> str:
        """Return the verified live instance, or create exactly one new child."""
        from aitest.interfaces.local.pipe import PipeUnavailable, validate_workspace_id

        try:
            validate_workspace_id(workspace_id)
            if len(workspace_id) > 128:
                raise PipeUnavailable("oversized core workspace identity")
        except PipeUnavailable as error:
            raise WorkspaceInUse("invalid core endpoint workspace identity") from error
        with self._launches.locked():
            prior = self._launches.read()
            pointer = self._launches.read_instance_id(self._id_path)
            if prior is None and pointer is not None:
                raise WorkspaceInUse(
                    "legacy core process identity is unknown; verify exit before recovery"
                )
            if prior is not None:
                if prior["state"] in {"creating", "publication_uncertain"}:
                    raise WorkspaceInUse("prior core creation is uncertain; preserve startup facts")
                if prior["state"] in {"created", "stopping"}:
                    fact = self._process_fact(prior)
                    if prior["state"] == "stopping":
                        deadline = time.monotonic() + 2.0
                        while (
                            fact.alive is True
                            and fact.identity == prior["process_identity"]
                            and time.monotonic() < deadline
                        ):
                            time.sleep(_DEFAULT_POLL_INTERVAL_SECONDS)
                            fact = self._process_fact(prior)
                    if fact.identity is not None and fact.identity != prior["process_identity"]:
                        self._record_exit(prior, ProcessFact(False), "pid_reused")
                    elif fact.alive is False:
                        self._record_exit(prior, fact)
                    elif fact.alive is True and fact.identity == prior["process_identity"]:
                        if prior["state"] == "stopping":
                            raise WorkspaceInUse(
                                "core shutdown has not completed; preserve the writer"
                            )
                        if prior["endpoint_workspace_id"] != workspace_id:
                            raise WorkspaceInUse(
                                "living core belongs to a different endpoint identity"
                            )
                        if pointer != prior["instance_id"]:
                            self._launches.publish_instance_id(self._id_path, prior["instance_id"])
                        return str(prior["instance_id"])
                    else:
                        raise WorkspaceInUse("prior core process identity cannot be verified")
            return self._create_child(workspace_id)

    def _create_child(self, workspace_id: str) -> str:
        program, venv_launcher = python_launch_program(self._python)
        # Missing discovery data does not prove absence of an existing writer.
        # Probe the real lifetime lock before publishing any new launch fact.
        FileCoreLaunchStore.reject_links(self._root / "writer.lock")
        admission = LifetimeWriterLock(self._root / "writer.lock")
        admission.acquire()
        admission.release()
        instance_id = f"core-{uuid4().hex[:12]}"
        value: dict[str, object] = {
            "schema": FileCoreLaunchStore.SCHEMA,
            "endpoint_workspace_id": workspace_id,
            "instance_id": instance_id,
            "state": "creating",
            "process_id": None,
            "process_identity": None,
            "exit_code": None,
            "reason": None,
        }
        self._launches.save(value)
        cmd = [
            program,
            "-m",
            self._module,
            "--workspace-root",
            str(self._root),
            "--workspace-id",
            workspace_id,
            "--instance-id",
            instance_id,
            # Child cannot assemble or recover business files before the claim
            # and discovery pointer have both been published by its parent.
            "--launch-claim",
            "--instance-id-file",
            self._id_path.name,
            # 父进程消亡后子核心在连接边界自行退出，避免孤儿核心长期占管。
            "--parent-pid",
            str(os.getpid()),
        ]
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        env = dict(self._env if self._env is not None else os.environ)
        env.pop("__PYVENV_LAUNCHER__", None)
        if venv_launcher is not None:
            env["__PYVENV_LAUNCHER__"] = venv_launcher
        # 推导 src 目录：本文件位于 src/aitest/bootstrap.py
        src_dir = Path(__file__).resolve().parent.parent
        python_path_parts = [str(src_dir)]
        existing_pp = env.get("PYTHONPATH")
        if existing_pp:
            python_path_parts.append(existing_pp)
        env["PYTHONPATH"] = os.pathsep.join(python_path_parts)
        try:
            child = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
                env=env,
            )
        except OSError as error:
            value.update(state="create_failed", reason="create_failed")
            self._launches.save(value)
            raise WorkspaceInUse(
                "core process creation failed; discovery was not published"
            ) from error
        self._children[instance_id] = child
        try:
            fact = self._probe(child.pid)
            if fact.alive is None or fact.identity is None:
                raise WorkspaceInUse("created core process birth cannot be verified")
            value.update(state="created", process_id=child.pid, process_identity=fact.identity)
            if fact.alive is False:
                self._record_exit(value, fact)
                raise WorkspaceInUse("core process exited before discovery publication")
            self._launches.save(value)
            self._launches.publish_instance_id(self._id_path, instance_id)
        except Exception as error:
            # Only the captured Popen handle can be stopped. Never terminate a
            # PID read from disk (it can refer to an unrelated reused process).
            code = child.poll()
            if code is None:
                with suppress(OSError, subprocess.TimeoutExpired):
                    child.terminate()
                    code = child.wait(timeout=1.0)
            failed = dict(value)
            if code is not None and failed["process_identity"] is not None:
                failed.update(state="exited", exit_code=code, reason="publication_failed")
            else:
                failed.update(
                    state="publication_uncertain",
                    process_id=None,
                    process_identity=None,
                    exit_code=None,
                    reason="publication_failed",
                )
            with suppress(OSError, ValueError, WorkspaceInUse):
                self._launches.save(failed)
            raise WorkspaceInUse(
                "core startup publication failed; process facts retained"
            ) from error
        return instance_id

    def _process_fact(self, value: dict[str, object]) -> ProcessFact:
        instance_id = str(value["instance_id"])
        child = self._children.get(instance_id)
        if child is not None and child.pid == value["process_id"]:
            # Popen retains the actual process handle, including its exit code.
            code = child.poll()
            return ProcessFact(code is None, str(value["process_identity"]), code)
        return self._probe(cast(int, value["process_id"]))

    def _record_exit(
        self,
        value: dict[str, object],
        fact: ProcessFact,
        reason: str = "process_exited",
    ) -> None:
        exited = dict(value)
        exited.update(state="exited", exit_code=fact.exit_code, reason=reason)
        self._launches.save(exited)
        self._children.pop(str(value["instance_id"]), None)

    def observe_start(self, instance_id: str) -> CoreStartupObservation:
        """Finite safe facts; raw startup exception text never enters discovery."""
        try:
            value = self._launches.read()
            if value is None or value["instance_id"] != instance_id:
                return CoreStartupObservation("unknown")
            if value["state"] == "exited":
                return CoreStartupObservation("exited", value["exit_code"], value["reason"])
            if value["state"] not in {"created", "stopping"}:
                return CoreStartupObservation("unknown")
            fact = self._process_fact(value)
            reused = fact.identity is not None and fact.identity != value["process_identity"]
            if reused or fact.alive is False:
                reason = "pid_reused" if reused else "process_exited"
                observed = ProcessFact(False) if reused else fact
                # A concurrent launch may already have advanced the current
                # fact. Never overwrite another instance with this old exit.
                with self._launches.locked():
                    if self._launches.read() == value:
                        self._record_exit(value, observed, reason)
                return CoreStartupObservation("exited", observed.exit_code, reason)
            if fact.alive is True and fact.identity == value["process_identity"]:
                return CoreStartupObservation("starting")
        except (OSError, WorkspaceInUse):
            pass
        return CoreStartupObservation("unknown")

    @property
    def instance_id_path(self) -> Path:
        return self._id_path


def await_core_launch_claim(
    workspace_root: Path,
    endpoint_workspace_id: str,
    instance_id: str,
    *,
    timeout_seconds: float = 5.0,
    instance_id_file: str = _INSTANCE_ID_FILE,
) -> bool:
    """Child gate: no business assembly before its exact creation claim is visible."""
    if not valid_instance_filename(instance_id_file):
        return False
    store = FileCoreLaunchStore(workspace_root)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        value = store.read()
        if value is not None and (
            value["instance_id"] != instance_id
            or value["endpoint_workspace_id"] != endpoint_workspace_id
        ):
            return False
        if value is not None and value["state"] == "created":
            if value["process_id"] != os.getpid():
                return False
            own = probe_process(os.getpid())
            if own.alive is not True or own.identity != value["process_identity"]:
                return False
            if store.read_instance_id(workspace_root / instance_id_file) == instance_id:
                return True
        elif value is not None and value["state"] != "creating":
            return False
        time.sleep(0.01)
    return False


def make_pipe_connector(
    workspace_root: Path,
    *,
    connect_timeout_ms: int = _DEFAULT_CONNECT_TIMEOUT_MS,
    ledger: CoreConnectionLedger | None = None,
) -> Connector:
    """构造基于 :class:`NamedPipeClient` 的连接器，供 :class:`EditorHost` 使用。

    连接器按工作空间 ``.core-instance-id`` 文件确定目标实例：

    - 文件缺失 → 返回 ``None``（无活动核心）；
    - 管道尚未就绪或对端身份不可信 → 返回 ``None``（让 host 轮询/启动）；
    - 连接成功 → 返回 ``(NamedPipeClient, instance_id)``，由调用方持有；
      若提供 ``ledger``，连接事实（含来源会话/用户 SID）在返回前已落盘，
      使连接可跨重启核对（A-10）。
    """
    from aitest.interfaces.local.pipe import (
        NamedPipeClient,
        PipeUnavailable,
        validate_workspace_id,
    )

    FileCoreLaunchStore.reject_links(workspace_root)
    FileCommitStore.reject_links(workspace_root)
    root = workspace_root.resolve()
    id_path = root / _INSTANCE_ID_FILE
    launches = FileCoreLaunchStore(root)

    def connect(workspace_id: str) -> tuple[object, str] | None:
        try:
            instance_id = launches.read_instance_id(id_path)
            launch = launches.read()
        except (OSError, WorkspaceInUse):
            return None
        if instance_id is None:
            return None
        if launch is not None and (
            launch["state"] != "created"
            or launch["instance_id"] != instance_id
            or launch["endpoint_workspace_id"] != workspace_id
        ):
            return None
        try:
            validate_workspace_id(workspace_id)
        except PipeUnavailable:
            return None
        try:
            client = NamedPipeClient(workspace_id, instance_id=instance_id)
            client.connect(timeout_ms=connect_timeout_ms)
            if launch is not None:
                actual = probe_process(client.peer_process_id or 0)
                if (
                    client.peer_process_id != launch["process_id"]
                    or actual.alive is not True
                    or actual.identity != launch["process_identity"]
                ):
                    client.close()
                    return None
        except PipeUnavailable:
            return None
        if ledger is not None:
            # 只在对端身份核对通过（管道连接成功即服务端同会话同用户
            # 校验已接受）后落事实，失败不阻断连接本身。
            with suppress(OSError):
                ledger.record(
                    "connected",
                    pipe_workspace_id=workspace_id,
                    instance_id=instance_id,
                )
        return (client, instance_id)

    return connect


def make_editor_host(
    workspace_root: Path,
    *,
    launcher: CoreLauncher | None = None,
    wait_timeout_seconds: float = _DEFAULT_WAIT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    ledger: CoreConnectionLedger | None = None,
) -> EditorHost:
    """装配 :class:`EditorHost`：注入管道连接器与系统进程启动器。"""
    return EditorHost(
        connector=make_pipe_connector(workspace_root, ledger=ledger),
        launcher=launcher or SystemProcessLauncher(workspace_root),
        wait_timeout_seconds=wait_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def acquire_endpoint(
    workspace_root: Path,
    *,
    workspace_id: str | None = None,
    launcher: CoreLauncher | None = None,
    wait_timeout_seconds: float = _DEFAULT_WAIT_TIMEOUT_SECONDS,
) -> CoreEndpoint:
    """发现或启动唯一核心并返回已核对身份的连接端点。

    - ``workspace_id`` 缺省时从工作空间 identity 读取；
    - ``launcher`` 缺省时使用 :class:`SystemProcessLauncher`；
    - 启动后核心在限定时间内不可连接，或探测到的实例身份与启动事实不一致，
      抛 :class:`aitest.interfaces.local.editor_host.WorkspaceInUse`；
    - 每次核对成功的连接都写入工作空间连接台账（A-10），跨重启可核对。
    """
    FileCoreLaunchStore.reject_links(workspace_root)
    FileCommitStore.reject_links(workspace_root)
    root = workspace_root.resolve()
    if workspace_id is None:
        workspace_id = Workspace(root).workspace_id
    host = make_editor_host(
        root,
        launcher=launcher,
        wait_timeout_seconds=wait_timeout_seconds,
        ledger=CoreConnectionLedger(root),
    )
    return host.acquire(workspace_id)


def acquire_existing_endpoint(workspace_root: Path) -> CoreEndpoint:
    """CLI/relay read an existing identity; missing paths must never become workspaces."""
    identity = Workspace(workspace_root, create=False)
    return acquire_endpoint(identity.root, workspace_id=identity.workspace_id)


def shutdown_endpoint(
    workspace_root: Path,
    *,
    workspace_id: str | None = None,
    connect_timeout_ms: int = _DEFAULT_CONNECT_TIMEOUT_MS,
    wait_timeout_seconds: float = 2.0,
) -> bool:
    """请求活动核心优雅退出（工作空间完整生命周期的关闭端）。

    通过**同一条已核对管道**发送一帧停机控制消息（不是业务 Command，
    不进 ``contracts.commands``）；无活动核心返回 ``False``。核心在当前
    命令边界排空后退出，随后 :func:`acquire_endpoint` 会启动新实例。

    客户端刚断开时核心可能正在关闭旧管道、用同一实例名重建监听管道，
    因此在 ``wait_timeout_seconds`` 窗口内重试连接，避免把重建间隙误判为
    无活动核心。
    """
    # 延迟导入：core_worker 在函数内反向导入本模块，避免循环导入。
    import time

    from aitest.interfaces.local.core_worker import shutdown_frame
    from aitest.interfaces.local.pipe import NamedPipeClient, PipeUnavailable

    FileCoreLaunchStore.reject_links(workspace_root)
    FileCommitStore.reject_links(workspace_root)
    root = workspace_root.resolve()
    if workspace_id is None:
        workspace_id = Workspace(root).workspace_id
    ledger = CoreConnectionLedger(root)
    connector = make_pipe_connector(root, connect_timeout_ms=connect_timeout_ms, ledger=ledger)
    deadline = time.monotonic() + max(0.0, wait_timeout_seconds)
    while True:
        connected = connector(workspace_id)
        if connected is not None:
            break
        if time.monotonic() >= deadline:
            return False
        time.sleep(_DEFAULT_POLL_INTERVAL_SECONDS)
    client = cast(NamedPipeClient, connected[0])
    instance_id = connected[1]
    try:
        client.write_message(shutdown_frame())
        from aitest.contracts.responses import Response
        from aitest.domain.json_material import decode_json

        raw_acknowledgement = client.read_message(
            timeout_ms=max(1, int((deadline - time.monotonic()) * 1000))
        )
        acknowledgement = Response.model_validate(decode_json(raw_acknowledgement.decode("utf-8")))
        if (
            acknowledgement.error is not None
            or acknowledgement.request_id != "core-shutdown"
            or acknowledgement.instance_id != instance_id
            or acknowledgement.workspace_id != Workspace(root).workspace_id
            or acknowledgement.result != {"status": "shutting_down"}
        ):
            return False
    except (PipeUnavailable, ValueError, OSError):
        return False
    finally:
        client.close()
    with suppress(OSError):
        ledger.record(
            "shutdown_requested",
            pipe_workspace_id=workspace_id,
            instance_id=instance_id,
        )
    # A shutdown request is distinct from actual exit. A following acquisition
    # waits only for a verified stopping process; it cannot mistake a vanished
    # pipe for permission to launch another writer.
    launches = FileCoreLaunchStore(root)
    with launches.locked():
        launch = launches.read()
        if launch is not None and (
            launch["state"] == "created"
            and launch["instance_id"] == instance_id
            and launch["endpoint_workspace_id"] == workspace_id
        ):
            launch.update(state="stopping", reason="shutdown_requested")
            launches.save(launch)
    return True


_GLOBAL_BOOTSTRAP = CoreBootstrap()


def register_use_cases(package: str, handlers: Mapping[str, Handler]) -> None:
    _GLOBAL_BOOTSTRAP.register_use_cases(package, handlers)


def create_api(
    workspace_root: Path | None = None,
    handlers: Mapping[str, Handler] | None = None,
) -> LocalAPI:
    """创建本地协议 API 实例。

    .. deprecated::
        ``handlers`` 参数为**过时兼容接口**，仅用于旧代码一次性本地 API 创建。
        B/C/D 上层包**禁止使用 handlers 参数**，必须通过 :func:`register_use_cases`
        注册用例，再由 ``create_api(workspace_root)`` 装配唯一核心实例。
        直接传入 handlers 会绕过统一注册窗口和所有者校验，属于后门风险。

    .. note::
        本入口仅作进程内装配；跨进程唯一核心请用 :func:`acquire_endpoint`。
    """
    if handlers is not None:
        return LocalAPI(instance_id=str(uuid4()), handlers=dict(handlers))
    if workspace_root is None:
        return LocalAPI(
            instance_id=str(uuid4()),
            handlers=dict(_GLOBAL_BOOTSTRAP.registry.snapshot()),
        )
    return _GLOBAL_BOOTSTRAP.create(workspace_root).api


__all__ = [
    "CoreAssembly",
    "CoreAssemblyBlocked",
    "CoreConnectionLedger",
    "CoreBootstrap",
    "CoreInstance",
    "EditorHost",
    "SystemProcessLauncher",
    "UseCaseRegistry",
    "acquire_endpoint",
    "acquire_existing_endpoint",
    "assemble_workspace_core",
    "create_api",
    "make_editor_host",
    "make_pipe_connector",
    "register_use_cases",
    "registered_use_cases",
    "shutdown_endpoint",
]
