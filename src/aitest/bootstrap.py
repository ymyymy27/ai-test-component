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
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import cast
from uuid import uuid4

from aitest.application.planning.substrate_adapter import (
    PortsRecordReader,
    PortsUnitOfWork,
)
from aitest.application.usecase_registry import BUseCaseDependencies
from aitest.infrastructure.adapters.execution.python_checks import (
    PythonLoadSourceProbe,
)
from aitest.infrastructure.adapters.model import HttpModelProvider
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore
from aitest.infrastructure.capabilities import (
    CONNECTION,
    MODEL,
    SECRET,
    SOURCE,
    CapabilityGate,
)
from aitest.infrastructure.clock import SystemClock
from aitest.infrastructure.connections import (
    ConnectionFactStore,
    EndpointConfig,
    LocalAPIConnectionBridge,
)
from aitest.infrastructure.credential_resolver import PurposeBoundCredentialResolver
from aitest.infrastructure.credentials import SecretManager
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.locking import LifetimeWriterLock
from aitest.infrastructure.file_store.recovery import (
    RecoveryOrchestrator,
    RecoveryState,
    seal_inflight_outputs,
)
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.file_store.workspace import Workspace
from aitest.infrastructure.projections import SafeMaterialProjector
from aitest.interfaces.local.api import Handler, LocalAPI
from aitest.interfaces.local.b_registration import b_registration_for
from aitest.interfaces.local.editor_host import (
    Connector,
    CoreEndpoint,
    CoreLauncher,
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
        line = (
            json.dumps(fact, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
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


def assemble_workspace_core(
    workspace_root: Path,
    *,
    instance_id: str,
    workspace_id: str | None = None,
    extra_handlers: Mapping[str, Handler] | None = None,
    connection_endpoint: str | None = None,
    model_endpoint: str | None = None,
    model_secret_reference: tuple[str, str] | None = None,
    extra_action_dependencies: Mapping[str, tuple[str, ...]] | None = None,
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
    root = workspace_root.resolve()
    workspace = Workspace(root)
    if workspace_id is not None:
        workspace.validate(workspace_id)
    # 全生命周期唯一写入者（A-02）：先取得排他写锁再跑恢复与服务装配，
    # 同进程/跨进程的同根第二核心在此被拒绝；恢复 blocked 时释放锁退出。
    lifetime_lock = workspace.admit_lifetime()
    try:
        journal = FileEventJournal(root, instance_id=workspace.workspace_id)
        recovery = RecoveryOrchestrator(
            root, instance_id=workspace.workspace_id
        ).run()
        if recovery.state == "blocked":
            raise CoreAssemblyBlocked(
                f"工作空间恢复 blocked，核心拒绝启动: {recovery.actions}"
            )

        unit_of_work = FileUnitOfWork(root, journal=journal)
        ports_unit_of_work = PortsUnitOfWork(
            unit_of_work,
            repository=unit_of_work.repo,
            sequence=unit_of_work,
        )
        reader = PortsRecordReader(unit_of_work.repo)
        clock = SystemClock()

        # A-10：默认装配真实凭据/来源能力与动作级能力门。能力门在
        # LocalAPI 构造前建立，连接水合结论与各能力条件随装配确定。
        gate = CapabilityGate()
        secret_manager = SecretManager.default()
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
            recovered_state = bridge.load()
            if recovered_state is None or recovered_state.get("connected"):
                gate.configure(CONNECTION)
            else:
                gate.report(
                    CONNECTION,
                    healthy=False,
                    reason=str(recovered_state.get("last_error") or "目标未就绪"),
                    classification=str(
                        recovered_state.get("last_error_kind") or "transport"
                    ),
                )

        model_provider: HttpModelProvider | None = None
        if model_endpoint is not None:
            # 模型能力必须显式装配：端点与已解析凭据同时具备才构造真实
            # provider；凭据解析失败只降级模型能力，不阻断核心启动。
            resolved_secret = None
            if model_secret_reference is not None:
                purpose, reference = model_secret_reference
                try:
                    resolved_secret = secret_manager.resolve(
                        reference, purpose=purpose
                    )
                except Exception as error:
                    gate.report(
                        MODEL,
                        healthy=False,
                        reason=f"模型凭据不可解析: {error}",
                        classification="auth",
                    )
            if resolved_secret is not None:
                model_provider = HttpModelProvider(
                    model_endpoint, secret=resolved_secret
                )
                gate.configure(MODEL)
            # 凭据解析失败已在 except 中写入自动 degraded（auth）事实，
            # 后续成功事实可自动恢复；未提供凭据引用则保持 not_configured
            # （缺配置）。两者都不是操作者意图，不得落人工降级——人工
            # 降级只能显式 restore，会把临时凭据故障永久钉死。

        # AB-001 §8.16.3：默认装配把模型三端口注入 B 的用例依赖。
        # - 投影器是本地纯计算，任何配置下都可用；
        # - 模型调用端点与凭据引用未配齐时 `caller` 为 None，模型动作按
        #   "未配置"降级（能力门 + handler 双重把关），不影响其余动作；
        # - 凭据解析走窄适配器（候选甲）：引用由装配方按用途绑定，B 侧
        #   只能按用途取状态，拿不到、也换不了凭据正文。
        credential_references: dict[str, str] = {}
        if model_secret_reference is not None:
            secret_purpose, secret_reference = model_secret_reference
            credential_references[secret_purpose] = secret_reference
        dependencies = BUseCaseDependencies(
            unit_of_work=ports_unit_of_work,
            reader=reader,
            clock=clock,
            projector=SafeMaterialProjector(),
            caller=model_provider,
            credentials=PurposeBoundCredentialResolver(
                secret_manager, credential_references
            ),
        )
        handlers: dict[str, Handler] = dict(b_registration_for(dependencies))
        if extra_handlers:
            conflicts = sorted(handlers.keys() & extra_handlers.keys())
            if conflicts:
                raise ValueError(
                    f"registered use case conflicts with built-in actions: {conflicts}"
                )
            handlers.update(extra_handlers)

        # AB-001 §8.16.3 第 4 条：动作级能力声明。模型出站依赖真实模型调用
        # 与凭据解析；`revise_pending_steps` / `narrow_driver` 是纯规则
        # （执行事实随命令带来），无外部能力依赖，不声明。
        gate.require("request_model_draft", MODEL, SECRET)

        if extra_action_dependencies:
            for action, keys in extra_action_dependencies.items():
                gate.require(action, *keys)

        api = LocalAPI(
            instance_id=instance_id,
            workspace_id=workspace.workspace_id,
            handlers=handlers,
            transaction_port=unit_of_work,
            connector=connector,
            connection_persistence=connector,
            capability_gate=gate,
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
        gate=gate,
        secret_manager=secret_manager,
        model_provider=model_provider,
        snapshot_store=snapshot_store,
        source_probe=source_probe,
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

    def register_use_cases(
        self, package: str, handlers: Mapping[str, Handler]
    ) -> None:
        self._registry.register(package, handlers, closed=self._registration_closed)

    def create(self, workspace_root: Path) -> CoreInstance:
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
    """通过 :mod:`subprocess` 启动长生命周期子进程作为唯一核心。

    启动后立即返回子进程的 ``instance_id``，不等待子进程进入就绪状态——
    调用方（通常是 :class:`EditorHost`）后续轮询管道连接确认子进程已就绪。
    子进程通过工作空间 ``.core-instance-id`` 文件与父进程共享实例标识；
    connector 读到该文件即知目标管道命名，管道尚未就绪则探测返回 ``None``。
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        python_executable: str | None = None,
        worker_module: str = _DEFAULT_WORKER_MODULE,
        instance_id_file: str = _INSTANCE_ID_FILE,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._root = workspace_root.resolve()
        self._python = python_executable or sys.executable
        self._module = worker_module
        self._id_path = self._root / instance_id_file
        self._env: dict[str, str] | None = dict(env) if env is not None else None

    def start(self, workspace_id: str) -> str:
        """启动子进程并返回新分配的 ``instance_id``。

        - 写入 ``instance_id`` 文件供 connector 探测（原子替换）；
        - 用 ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` 启动脱离父进程
          的子进程（仅 Windows；其他平台无 ``creationflags`` 语义）；
        - 子进程的 stdin/stdout/stderr 全部丢弃，避免占用管道；
        - 显式把 ``src/`` 注入子进程 ``PYTHONPATH``，让 ``python -m
          aitest.interfaces.local.core_worker`` 在未 pip 装包环境也能解析。
        """
        instance_id = f"core-{uuid4().hex[:12]}"
        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._id_path.with_name(
            f".{self._id_path.name}.{os.getpid()}.tmp"
        )
        tmp.write_text(instance_id, encoding="utf-8")
        os.replace(tmp, self._id_path)
        cmd = [
            self._python,
            "-m",
            self._module,
            "--workspace-root",
            str(self._root),
            "--workspace-id",
            workspace_id,
            "--instance-id",
            instance_id,
            # 父进程消亡后子核心在连接边界自行退出，避免孤儿核心长期占管。
            "--parent-pid",
            str(os.getpid()),
        ]
        creationflags = 0
        if sys.platform == "win32":
            creationflags = (
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        env = dict(self._env if self._env is not None else os.environ)
        # 推导 src 目录：本文件位于 src/aitest/bootstrap.py
        src_dir = Path(__file__).resolve().parent.parent
        python_path_parts = [str(src_dir)]
        existing_pp = env.get("PYTHONPATH")
        if existing_pp:
            python_path_parts.append(existing_pp)
        env["PYTHONPATH"] = os.pathsep.join(python_path_parts)
        subprocess.Popen(  # noqa: S603 - 受控的子进程入口，命令由本类构造
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
            env=env,
        )
        return instance_id

    @property
    def instance_id_path(self) -> Path:
        return self._id_path


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

    root = workspace_root.resolve()
    id_path = root / _INSTANCE_ID_FILE

    def connect(workspace_id: str) -> tuple[object, str] | None:
        if not id_path.exists():
            return None
        try:
            raw = id_path.read_text(encoding="utf-8")
        except OSError:
            return None
        instance_id = raw.strip()
        if not instance_id:
            return None
        try:
            validate_workspace_id(workspace_id)
        except PipeUnavailable:
            return None
        try:
            client = NamedPipeClient(workspace_id, instance_id=instance_id)
            client.connect(timeout_ms=connect_timeout_ms)
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

    root = workspace_root.resolve()
    if workspace_id is None:
        workspace_id = Workspace(root).workspace_id
    ledger = CoreConnectionLedger(root)
    connector = make_pipe_connector(
        root, connect_timeout_ms=connect_timeout_ms, ledger=ledger
    )
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
    except PipeUnavailable:
        return False
    finally:
        client.close()
    with suppress(OSError):
        ledger.record(
            "shutdown_requested",
            pipe_workspace_id=workspace_id,
            instance_id=instance_id,
        )
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
    "assemble_workspace_core",
    "create_api",
    "make_editor_host",
    "make_pipe_connector",
    "register_use_cases",
    "registered_use_cases",
    "shutdown_endpoint",
]
