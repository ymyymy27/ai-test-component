"""唯一 A 包装配入口。B/C/D 只能提交用例注册表。"""

import warnings
from uuid import uuid4
from collections.abc import Mapping
from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from pathlib import Path

from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.file_store.workspace import Workspace
from aitest.interfaces.local.api import Handler, LocalAPI

@dataclass(frozen=True, slots=True)
class CoreInstance:
    instance_id: str
    workspace_id: str
    api: LocalAPI

class UseCaseRegistry:
    def __init__(self) -> None:
        self._lock=RLock(); self._handlers: dict[str, Handler]={}; self._owners: dict[str,str]={}
    def register(self, package: str, handlers: Mapping[str, Handler], *, closed: bool = False) -> None:
        if package not in {"B", "C", "D"}: raise ValueError("package must be B, C or D")
        if closed: raise RuntimeError("use case registration is closed after workspace assembly")
        with self._lock:
            for action, handler in handlers.items():
                if not action or not callable(handler): raise ValueError("use case action must map to a callable")
                if action in self._handlers: raise ValueError(f"use case already registered: {action}")
            self._handlers.update(handlers); self._owners.update({action: package for action in handlers})
    def snapshot(self) -> Mapping[str, Handler]:
        with self._lock: return MappingProxyType(dict(self._handlers))
    def owners(self) -> Mapping[str, str]:
        with self._lock: return MappingProxyType(dict(self._owners))

class CoreBootstrap:
    def __init__(self) -> None:
        self._lock=RLock(); self._registry=UseCaseRegistry(); self._instances: dict[Path,CoreInstance]={}; self._registration_closed=False
    @property
    def registry(self) -> UseCaseRegistry: return self._registry
    def register_use_cases(self, package: str, handlers: Mapping[str, Handler]) -> None: self._registry.register(package, handlers, closed=self._registration_closed)
    def create(self, workspace_root: Path) -> CoreInstance:
        root=workspace_root.resolve()
        with self._lock:
            if root in self._instances: return self._instances[root]
            self._registration_closed=True
            workspace=Workspace(root); uow=FileUnitOfWork(root)
            api=LocalAPI(instance_id=str(uuid4()), workspace_id=workspace.workspace_id, handlers=dict(self._registry.snapshot()), transaction_port=uow)
            instance=CoreInstance(api.instance_id, workspace.workspace_id, api); self._instances[root]=instance; return instance

_GLOBAL_BOOTSTRAP=CoreBootstrap()
def register_use_cases(package: str, handlers: Mapping[str, Handler]) -> None: _GLOBAL_BOOTSTRAP.register_use_cases(package, handlers)


def create_api(workspace_root: Path | None = None, handlers: Mapping[str, Handler] | None = None) -> LocalAPI:
    """创建本地协议 API 实例。

    .. deprecated::
        ``handlers`` 参数为**过时兼容接口**，仅用于旧代码一次性本地 API 创建。
        B/C/D 上层包**禁止使用 handlers 参数**，必须通过 :func:`register_use_cases`
        注册用例，再由 ``create_api(workspace_root)`` 装配唯一核心实例。
        直接传入 handlers 会绕过统一注册窗口和所有者校验，属于后门风险。
    """
    if handlers is not None:
        warnings.warn(
            "create_api(handlers=...) 是过时兼容接口：B/C/D 上层包禁止使用 handlers 参数，"
            "必须走 register_use_cases 注册。该参数仅用于旧兼容，不用于 B/C/D 正常接入。",
            DeprecationWarning,
            stacklevel=2,
        )
        return LocalAPI(instance_id=str(uuid4()), handlers=dict(handlers))
    if workspace_root is None: return LocalAPI(instance_id=str(uuid4()), handlers=dict(_GLOBAL_BOOTSTRAP.registry.snapshot()))
    return _GLOBAL_BOOTSTRAP.create(workspace_root).api

__all__=["CoreBootstrap","CoreInstance","UseCaseRegistry","create_api","register_use_cases"]
