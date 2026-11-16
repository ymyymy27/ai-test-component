"""把 B 的用例注册表交给统一入口。

为什么单独一层
--------------

`aitest.application.usecase_registry` **不 import** `bootstrap` / `interfaces`
（`application` 不得依赖入口层，见 `tests/architecture/test_boundaries.py`）。
`bootstrap.Handler` 的形参类型是 `contracts.Command`，而 B 的 handler 只按结构化字段
读命令（`action` / `project_id` / `parameters` / …），因此两边对接时在这里做一次
**显式 cast**：形状一致由 `Command` 的字段与 `build_b_use_case_registry()` 的读取方式共同保证，
`tests/contracts/test_b_use_case_registration.py` 用真实 `Command` 驱动来锁死这件事。

用法（装配点或测试）：

```python
from aitest.application.usecase_registry import BUseCaseDependencies, build_b_use_case_registry
from aitest.interfaces.local.api import LocalAPI
from aitest.interfaces.local.b_registration import register_b_use_cases

api = LocalAPI(instance_id=..., workspace_id=..., handlers={})
register_b_use_cases(api, BUseCaseDependencies(unit_of_work=uow, reader=reader, clock=clock))
```

`api` 只要求有可写的 `handlers` 字典；本模块不构造也不替换 `LocalAPI`。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from aitest.application.usecase_registry import (
    BUseCaseDependencies,
    build_b_use_case_registry,
)
from aitest.contracts.commands import Command
from aitest.interfaces.local.api import Handler

__all__ = ["b_registration_for", "register_b_use_cases"]


def b_registration_for(deps: BUseCaseDependencies) -> dict[str, Handler]:
    """构造可直接并入 `LocalAPI(handlers=...)` 的 B 动作表。"""
    registry = build_b_use_case_registry(deps)

    def to_entry_handler(handler: Callable[[object], Mapping[str, object]]) -> Handler:
        def entry(command: Command) -> Mapping[str, object]:
            return handler(cast("object", command))

        return entry

    return {
        action: to_entry_handler(handler)
        for action, handler in registry.as_registration().items()
    }


def register_b_use_cases(api: object, deps: BUseCaseDependencies) -> tuple[str, ...]:
    """把 B 的动作并进一个已有 `LocalAPI` 的 handler 表。

    返回注册的动作名（排序后），便于调用点与 `doctor` 的 `supported_actions` 核对。
    同名动作**已存在时拒绝**：B 的动作名归 `contracts/commands.py` 的一期动作表，
    静默覆盖会让"谁拥有这个动作"在运行期失去意义。
    """
    handlers = getattr(api, "handlers", None)
    if not isinstance(handlers, dict):
        raise TypeError("api must expose a mutable 'handlers' dict")
    registration = b_registration_for(deps)
    conflicts = sorted(set(registration) & set(handlers))
    if conflicts:
        raise ValueError(f"B use case already registered: {conflicts}")
    handlers.update(registration)
    return tuple(sorted(registration))
