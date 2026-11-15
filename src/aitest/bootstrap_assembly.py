"""Composition root: assemble a workspace core (moved from bootstrap)."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import cast
from uuid import uuid4

from aitest.application.approval_service import ApprovalService
from aitest.application.controlled_write import ControlledWriteService, SavedControlledWriteResolver
from aitest.application.evidence.agent_evidence import SavedAgentEvidence
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
from aitest.application.execution.http_workflow_execution import (
    SavedHttpWorkflowExecution,
)
from aitest.application.execution.output_material import require_saved_output_material
from aitest.application.execution.plan_grant import PlanExecutionGrantService
from aitest.application.execution.registration import InitialRunRegistration
from aitest.application.execution.reuse_inspection import CaseReuseInspection
from aitest.application.execution.run_schedule import SavedRunSchedule
from aitest.application.execution.runtime_actions import SavedRuntimeRevisionActions
from aitest.application.execution.saved_control import SavedRunControl
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
from aitest.application.planning.plan_confirmation import PlanConfirmationService
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
from aitest.application.project.delivery_verification import DeliveryVerificationReader
from aitest.application.project.environment_resolution import EnvironmentResolutionService
from aitest.application.project.source_analysis import SourceAnalysisService
from aitest.application.project.test_discovery import TestDiscoveryService
from aitest.application.project.understanding import ProjectUnderstandingService
from aitest.application.record_write import ORDINARY_WRITE_ACTIONS
from aitest.application.record_write import SCHEMA as RECORD_WRITE_SCHEMA
from aitest.application.review.repair_brief import SavedRepairBrief
from aitest.application.review.saved_defects import SavedDefectManagement
from aitest.application.review.saved_exports import SavedExportService
from aitest.application.review.saved_reports import SavedReportService
from aitest.application.review.saved_reuse import SavedCaseReuseSelection
from aitest.application.usecase_registry import BUseCaseDependencies, BUseCaseError
from aitest.contracts.commands import Command
from aitest.core_endpoint import (
    CoreConnectionLedger as CoreConnectionLedger,
)
from aitest.core_endpoint import (
    read_core_startup_diagnostic as read_core_startup_diagnostic,
)
from aitest.core_endpoint import (
    rebuild_query_index as rebuild_query_index,
)
from aitest.core_endpoint import (
    record_core_startup_failure as record_core_startup_failure,
)
from aitest.core_endpoint import (
    reject_path_links as reject_path_links,
)
from aitest.core_endpoint import (
    save_purpose_credential as save_purpose_credential,
)
from aitest.core_endpoint import (
    verify_purpose_credential as verify_purpose_credential,
)
from aitest.domain.approvals import TrustedActor
from aitest.infrastructure.adapters.execution.command import CommandAdapter
from aitest.infrastructure.adapters.execution.http_workflow import HttpWorkflowAdapter
from aitest.infrastructure.adapters.execution.python_checks import (
    PythonLoadSourceProbe,
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
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.execution_handles import FileExecutionHandleStore
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
from aitest.infrastructure.host_lifetime import cli_host_pid as cli_host_pid
from aitest.infrastructure.model_configuration import host_model_configuration
from aitest.infrastructure.model_configuration import (
    validate_model_configuration as validate_model_configuration,
)
from aitest.infrastructure.projections import SafeMaterialProjector
from aitest.infrastructure.security import guard_value
from aitest.interfaces.local.actor_context import CoreActorContext
from aitest.interfaces.local.api import Handler, LocalAPI, Session
from aitest.interfaces.local.b_registration import b_registration_for
from aitest.interfaces.local.d_registration import d_registration_for
from aitest.interfaces.local.view_queries import CommittedViewReader
from aitest.source_wiring import (
    _entry_actual_path as _entry_actual_path,
)
from aitest.source_wiring import (
    _environment_ref as _environment_ref,
)
from aitest.source_wiring import (
    _frozen_adapter_version as _frozen_adapter_version,
)
from aitest.source_wiring import (
    _frozen_step_timeout as _frozen_step_timeout,
)
from aitest.source_wiring import (
    _interpreter_verifier as _interpreter_verifier,
)
from aitest.source_wiring import (
    _module_expectations as _module_expectations,
)
from aitest.source_wiring import (
    _module_names_from_project as _module_names_from_project,
)
from aitest.source_wiring import (
    _observed_content_identity as _observed_content_identity,
)
from aitest.source_wiring import (
    _pattern_relative_dir as _pattern_relative_dir,
)
from aitest.source_wiring import (
    _pinned_file_digests as _pinned_file_digests,
)
from aitest.source_wiring import (
    _pinned_snapshot_identity as _pinned_snapshot_identity,
)
from aitest.source_wiring import (
    _positive_int as _positive_int,
)
from aitest.source_wiring import (
    _probe_output_reference as _probe_output_reference,
)
from aitest.source_wiring import (
    _registered_source_check_statuses as _registered_source_check_statuses,
)
from aitest.source_wiring import (
    _required_source_analysis as _required_source_analysis,
)
from aitest.source_wiring import (
    _required_text as _required_text,
)
from aitest.source_wiring import (
    _rules_revision as _rules_revision,
)
from aitest.source_wiring import (
    _source_verification_provider as _source_verification_provider,
)
from aitest.source_wiring import (
    _technical_identity as _technical_identity,
)

_INSTANCE_ID_FILE = ".core-instance-id"
_DEFAULT_WORKER_MODULE = "aitest.interfaces.local.core_worker"
_DEFAULT_CONNECT_TIMEOUT_MS = 200
_DEFAULT_WAIT_TIMEOUT_SECONDS = 5.0
_DEFAULT_POLL_INTERVAL_SECONDS = 0.05



@dataclass(frozen=True, slots=True)
class CoreInstance:
    instance_id: str
    workspace_id: str
    api: LocalAPI
    lifetime_lock: LifetimeWriterLock | None = None



class CoreAssemblyBlocked(RuntimeError):
    """工作空间完整性无法通过启动恢复，核心不得对外服务。"""






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
    legacy = [
        name
        for name in ("records.json", "commit.json", "events.json", "transactions.json")
        if (root / name).exists()
    ]
    if legacy and not (root / "current.json").exists():
        raise CoreAssemblyBlocked(
            "工作空间格式过旧（legacy 提交/事件文件），本版本不支持；请新建工作空间"
        )
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
        if model_endpoint is None and model_secret_reference is None:
            try:
                host_model = host_model_configuration()
                if host_model is not None:
                    model_endpoint, model_secret_reference = host_model
            except ValueError:
                gate.report(
                    MODEL, healthy=False,
                    reason="宿主模型配置无效；核对端点与凭据引用后受控重启核心",
                    classification="configuration",
                )
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
        basis_confirmations = BasisConfirmationService(
            reader=reader, unit=unit_of_work, approvals=approvals
        )
        plan_grants = PlanExecutionGrantService(
            unit=unit_of_work,
            records=cast(RecordRepository, unit_of_work.repo),
            workspace_id=workspace.workspace_id,
            actors=actors,
            clock=approvals.clock,
        )
        plan_confirmations = PlanConfirmationService(
            reader=reader,
            unit=unit_of_work,
            basis=basis_confirmations,
            grants=plan_grants,
        )
        source_analysis = SourceAnalysisService(
            reader=reader,
            unit_of_work=unit_of_work,
            snapshots=snapshot_store,
            source_control=GitSourceControl(),
            source_available=lambda: gate.condition(SOURCE).state.value == "ready",
            controlled_writes=controlled_writes,
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
            source_analysis=source_analysis,
            test_discovery=TestDiscoveryService(
                source_analysis=source_analysis,
                environments=environment_resolution,
                snapshots=snapshot_store,
            ),
            project_understanding=ProjectUnderstandingService(
                reader=reader,
                unit_of_work=ports_unit_of_work,
                snapshots=snapshot_store,
                workspace_id=workspace.workspace_id,
            ),
            basis_confirmations=basis_confirmations,
            plan_confirmations=plan_confirmations,
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
            plan_grants=plan_grants,
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
            else (RegisteredEntryExecutionAdapter(CommandAdapter(
                spool_store=FileSpoolStore(root),
                handle_store=FileExecutionHandleStore(root),
                artifact_sources=(("pytest.xml", "application/xml; profile=junit"),),
            ))
                  if execution_enabled else None),
            http_workflow=SavedHttpWorkflowExecution(
                execution_authorizations,
                execution_coordinator,
                FileObjectStore(root),
                workspace_id=workspace.workspace_id,
                instance_id=instance_id,
                adapter=HttpWorkflowAdapter(secrets=secret_manager),
            ),
        )
        execution_commands = ExecutionCommands(
            execution_authorizations, initial_run_registration, step_execution, plan_grants
        )
        reuse_inspection = CaseReuseInspection(
            execution_coordinator,
            cast(RecordRepository, unit_of_work.repo),
            reader,
            FileObjectStore(root),
            FileSpoolStore(root),
            basis_proof=approvals,
            environment=environment_resolution,
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
        agent_evidence = SavedAgentEvidence(
            execution_coordinator,
            workspace_id=workspace.workspace_id,
            instance_id=instance_id,
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
            create_plan_grant=execution_commands.create_plan_grant,
            revoke_plan_grant=execution_commands.revoke_plan_grant,
            execute_step=execution_commands.execute,
            retry_step=execution_commands.retry,
            return_stage=execution_commands.return_stage,
            start_run=run_schedule.apply,
            verify_pending=business_verification.apply,
            import_external_result=external_imports.apply,
            record_agent_evidence=agent_evidence.apply,
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
            store=FileBackupStore(root, backup_root=migration_backup_root),
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
            objects=FileObjectStore(root),
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
            objects=FileObjectStore(root),
            # 证据正文显示态投影需要工作空间根前缀（CORE-001 1.95）。
            workspace_root=root,
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


