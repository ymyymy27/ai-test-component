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

import json
import os
import subprocess as subprocess
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
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
from aitest.application.evidence.saved_verification import SavedBusinessVerification
from aitest.application.execution.authorization import (
    ExecutionAuthorizationService,
    SavedExecutionAuthorizationResolver,
)
from aitest.application.execution.commands import ExecutionCommands
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.output_material import require_saved_output_material
from aitest.application.execution.registration import InitialRunRegistration
from aitest.application.execution.run_schedule import SavedRunSchedule
from aitest.application.execution.runtime_actions import SavedRuntimeRevisionActions
from aitest.application.execution.saved_control import SavedRunControl
from aitest.application.execution.step_execution import SavedStepExecution
from aitest.application.planning.basis_approval import SavedBasisApprovalResolver
from aitest.application.planning.basis_confirmation import BasisConfirmationService
from aitest.application.planning.model_policy_confirmation import (
    ModelPolicyConfirmationService,
    SavedHumanActionResolver,
    SavedModelPolicyApprovalResolver,
)
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
)
from aitest.application.project.environment_resolution import EnvironmentResolutionService
from aitest.application.project.source_analysis import SourceAnalysisService
from aitest.application.record_write import ORDINARY_WRITE_ACTIONS
from aitest.application.record_write import SCHEMA as RECORD_WRITE_SCHEMA
from aitest.application.usecase_registry import BUseCaseDependencies
from aitest.contracts.commands import Command
from aitest.domain.approvals import TrustedActor
from aitest.infrastructure.adapters.execution.python_checks import (
    PythonLoadSourceProbe,
)
from aitest.infrastructure.adapters.model import HttpModelProvider, ModelCredentialResolver
from aitest.infrastructure.adapters.source_control import GitSourceControl
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore
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
from aitest.infrastructure.file_store.migrations import FileMigrationManager
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
from aitest.infrastructure.python_environment import RegisteredPythonEnvironmentResolver
from aitest.infrastructure.security import guard_value
from aitest.interfaces.local.actor_context import CoreActorContext
from aitest.interfaces.local.api import Handler, LocalAPI, Session
from aitest.interfaces.local.b_registration import b_registration_for
from aitest.interfaces.local.editor_host import (
    Connector,
    CoreEndpoint,
    CoreLauncher,
    CoreStartupObservation,
    EditorHost,
)

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
        migrations = FileMigrationManager(root)
        migration_plan = migrations.plan(
            (
                "0003-sharded-record-authority",
                "0004-bounded-query-directory",
                "0005-complete-commit-closure",
                "0006-canonical-current-publication",
                "0007-canonical-manifest-and-business-changes",
            )
        )
        try:
            migration_report = migrations.apply(migration_plan.plan_id)
        except (OSError, ValueError) as error:
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
            resolver=environment_resolver or RegisteredPythonEnvironmentResolver(),
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
            action_resolver=execution_action_resolver,
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
                cast(RecordRepository, unit_of_work.repo), reader, FileSpoolStore(root),
                FileObjectStore(root), workspace_id=workspace.workspace_id, instance_id=instance_id,
            ),
        )
        step_execution = SavedStepExecution(
            execution_authorizations, execution_coordinator, FileSpoolStore(root), execution_port
        )
        execution_commands = ExecutionCommands(
            execution_authorizations, initial_run_registration, step_execution
        )
        run_control = SavedRunControl(execution_coordinator, step_execution, workspace.workspace_id)
        run_schedule = SavedRunSchedule(
            execution_coordinator, step_execution, workspace.workspace_id, unit_of_work.repo
        )
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
        handlers.update(
            register_run=execution_commands.register,
            prepare_execution=execution_commands.prepare,
            authorize_step=execution_commands.grant,
            execute_step=execution_commands.execute,
            start_run=run_schedule.apply,
            verify_pending=business_verification.apply,
            import_external_result=external_imports.apply,
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
        acknowledgement = Response.model_validate(
            decode_json(raw_acknowledgement.decode("utf-8"))
        )
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
