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

import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from uuid import uuid4

from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.file_store.workspace import Workspace
from aitest.interfaces.local.api import Handler, LocalAPI
from aitest.interfaces.local.editor_host import (
    Connector,
    CoreEndpoint,
    CoreLauncher,
    EditorHost,
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
            workspace = Workspace(root)
            uow = FileUnitOfWork(root)
            api = LocalAPI(
                instance_id=str(uuid4()),
                workspace_id=workspace.workspace_id,
                handlers=dict(self._registry.snapshot()),
                transaction_port=uow,
            )
            instance = CoreInstance(api.instance_id, workspace.workspace_id, api)
            self._instances[root] = instance
            return instance


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
) -> Connector:
    """构造基于 :class:`NamedPipeClient` 的连接器，供 :class:`EditorHost` 使用。

    连接器按工作空间 ``.core-instance-id`` 文件确定目标实例：

    - 文件缺失 → 返回 ``None``（无活动核心）；
    - 管道尚未就绪或对端身份不可信 → 返回 ``None``（让 host 轮询/启动）；
    - 连接成功 → 返回 ``(NamedPipeClient, instance_id)``，由调用方持有。
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
        return (client, instance_id)

    return connect


def make_editor_host(
    workspace_root: Path,
    *,
    launcher: CoreLauncher | None = None,
    wait_timeout_seconds: float = _DEFAULT_WAIT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
) -> EditorHost:
    """装配 :class:`EditorHost`：注入管道连接器与系统进程启动器。"""
    return EditorHost(
        connector=make_pipe_connector(workspace_root),
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
      抛 :class:`aitest.interfaces.local.editor_host.WorkspaceInUse`。
    """
    root = workspace_root.resolve()
    if workspace_id is None:
        workspace_id = Workspace(root).workspace_id
    host = make_editor_host(
        root,
        launcher=launcher,
        wait_timeout_seconds=wait_timeout_seconds,
    )
    return host.acquire(workspace_id)


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
    "CoreBootstrap",
    "CoreInstance",
    "EditorHost",
    "SystemProcessLauncher",
    "UseCaseRegistry",
    "acquire_endpoint",
    "create_api",
    "make_editor_host",
    "make_pipe_connector",
    "register_use_cases",
]
