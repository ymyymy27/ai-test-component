"""受控结构迁移入口（架构 04 §6/§9/§11、P1-FR16）。

报告点名的缺口是「backup/migrate 虽有底层组件，工作空间级受控写身份与产品入口
尚未完成」。底层 `FileMigrationManager` 已实现 `inspect`／`plan`／`apply`／
`resume`／`rollback`，并且**自己**强制了合同里的硬规则：

- `apply` 首个真实步骤前必须存在**已校验**备份，复用既有备份前重新核对；
- 存在未核实活动（活动事务/非空事件暂存/未封口 spool/未核实执行句柄）时返回
  `blocked` 而不是迁移；
- 计划必须按迁移 id 升序、唯一、已注册；
- 不可逆迁移存在时 `rollback` 整体拒绝。

因此本模块只补**受控入口**与**维护窗口**：

1. `inspect` 是只读动作，任何受控会话可用（诊断需要）；
2. `plan`／`apply`／`resume`／`rollback` 是结构性写入，必须**受控交互会话**，
   并在**维护窗口**内执行——窗口期间入口适配器拒绝新的写动作
   （架构 04 §142「进入维护状态暂停新写入」）；
3. 迁移失败按 `MIGRATION_FAILED` 结构化拒绝，不把底层异常当内部错误；
4. 不在本入口重造备份/活动门禁：那是存储组件已实现的合同规则，重复实现会分叉。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any, Final, Protocol

from aitest.application.maintenance.mode import MaintenanceMode, MaintenanceModeActive
from aitest.application.ports import MigrationRunnerPort

MIGRATE_MODES: Final[tuple[str, ...]] = ("inspect", "plan", "apply", "resume", "rollback")
#: 需要受控交互会话 + 维护窗口的模式。
WRITE_MODES: Final[frozenset[str]] = frozenset({"plan", "apply", "resume", "rollback"})


class MigrationBlocked(ValueError):
    """迁移被拒绝；错误码取已登记集合（`MIGRATION_FAILED` / `MAINTENANCE_REQUIRED`）。"""

    def __init__(self, message: str, *, code: str = "MIGRATION_FAILED") -> None:
        super().__init__(message)
        self.code = code


class _Actors(Protocol):
    def current(self) -> Any: ...


class SavedMigrationEntry:
    """`migrate` 动作入口：五个模式 + 受控会话 + 维护窗口。"""

    def __init__(
        self,
        *,
        runner: MigrationRunnerPort,
        actors: _Actors,
        mode: MaintenanceMode,
    ) -> None:
        if runner is None or mode is None:
            raise MigrationBlocked("migration entry requires its runner and the maintenance mode")
        self.runner = runner
        self.actors = actors
        self.mode = mode

    def apply(self, command: Any) -> Mapping[str, object]:
        if getattr(command, "action", None) != "migrate":
            raise MigrationBlocked("migration entry only serves the migrate action")
        mode, parameters = self._parameters(getattr(command, "parameters", None))
        if mode in WRITE_MODES:
            self._controlled_identity()
            try:
                with self.mode.window(f"migrate:{mode}"):
                    return self._dispatch(mode, parameters)
            except MaintenanceModeActive as error:
                raise MigrationBlocked(str(error), code="MAINTENANCE_REQUIRED") from error
        return self._dispatch(mode, parameters)

    # ------------------------------------------------------------ 守卫

    @staticmethod
    def _parameters(raw: object) -> tuple[str, dict[str, object]]:
        if not isinstance(raw, Mapping):
            raise MigrationBlocked("migrate requires explicit parameters")
        unknown = set(raw) - {"mode", "migration_ids", "plan_id"}
        if unknown:
            raise MigrationBlocked(f"migrate received unknown parameters: {sorted(unknown)}")
        mode = raw.get("mode")
        if not isinstance(mode, str) or mode not in MIGRATE_MODES:
            raise MigrationBlocked(
                f"migrate mode must be one of {sorted(MIGRATE_MODES)}, got {mode!r}"
            )
        parameters = {key: value for key, value in raw.items() if key != "mode"}
        if mode == "plan":
            ids = parameters.get("migration_ids")
            if not isinstance(ids, (list, tuple)) or not ids or any(
                not isinstance(item, str) or not item.strip() for item in ids
            ):
                raise MigrationBlocked("migrate plan requires an explicit ordered id list")
            if set(parameters) != {"migration_ids"}:
                raise MigrationBlocked("migrate plan accepts only migration_ids")
        if mode in {"apply", "resume", "rollback"}:
            plan_id = parameters.get("plan_id")
            if not isinstance(plan_id, str) or not plan_id.strip():
                raise MigrationBlocked(f"migrate {mode} requires an explicit plan_id")
            if set(parameters) != {"plan_id"}:
                raise MigrationBlocked(f"migrate {mode} accepts only plan_id")
        if mode == "inspect" and parameters:
            raise MigrationBlocked("migrate inspect accepts no parameters")
        return mode, parameters

    def _controlled_identity(self) -> None:
        actor = self.actors.current()
        if actor is None or not getattr(actor, "interactive", False):
            raise MigrationBlocked(
                "structure migration requires a controlled interactive session",
                code="AWAITING_USER_CONFIRMATION",
            )

    # ------------------------------------------------------------ 执行

    def _dispatch(self, mode: str, parameters: Mapping[str, object]) -> Mapping[str, object]:
        try:
            if mode == "inspect":
                return {"mode": mode, "report": dict(self.runner.inspect())}
            if mode == "plan":
                ids = parameters["migration_ids"]
                assert isinstance(ids, (list, tuple))
                result = self.runner.plan([str(item) for item in ids])
                return {"mode": mode, **_plain(result)}
            plan_id = str(parameters["plan_id"])
            if mode == "apply":
                return {"mode": mode, **_plain(self.runner.apply(plan_id))}
            if mode == "resume":
                return {"mode": mode, **_plain(self.runner.resume(plan_id))}
            return {"mode": mode, **_plain(self.runner.rollback(plan_id))}
        except MigrationBlocked:
            raise
        except (OSError, ValueError, RuntimeError) as error:
            raise MigrationBlocked(str(error)) from error


def _plain(value: object) -> dict[str, object]:
    """把 `PlanResult`／`ApplyReport`／`RollbackReport` 变成可序列化映射。"""

    if is_dataclass(value) and not isinstance(value, type):
        payload = asdict(value)
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise MigrationBlocked("migration result shape cannot be serialized")
    return {
        key: _jsonable(item) for key, item in payload.items()
    }


def _jsonable(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    from pathlib import Path

    if isinstance(value, Path):
        return str(value)
    return str(value)


__all__ = [
    "MIGRATE_MODES",
    "WRITE_MODES",
    "MigrationBlocked",
    "SavedMigrationEntry",
]


def _sequence(value: Sequence[str]) -> Sequence[str]:  # pragma: no cover - typing helper
    return value
