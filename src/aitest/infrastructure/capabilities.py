"""动作级能力门（A-10）：按动作的真实依赖降级，不扩散为整体不可用。

某一外部能力（连接、模型、凭据、来源核对）故障或未配置时，只拦截**实际
依赖该能力**的动作；不依赖它的本地动作（保存、查询、恢复、doctor 等）
必须继续可用。判定是机械事实，三个状态：

- ``ready``：能力已配置且最近事实正常；
- ``degraded``：能力曾配置但最近事实故障（含归一失败分类）或被人工降级；
- ``not_configured``：默认装配未获得该能力的显式配置（缺端点/缺凭据）。

门**不探测、不重连**：探测事实由连接/模型/来源适配器产生后经
:meth:`CapabilityGate.report` / :meth:`degrade` / :meth:`restore` 写入；
人工暂停与自动事实分开记录；新的成功事实只恢复自动故障，人工暂停
须显式 :meth:`restore` 清除。换核心不丢暂停，也不按时间静默自愈。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Protocol

from aitest.infrastructure.file_store.atomic import write_json
from aitest.infrastructure.security import guard_value

#: 接口层以字符串引用这些键（interfaces 不得导入 infrastructure），
#: 修改取值属于协议变更，需同步 LocalAPI 与合同测试。
CONNECTION = "connection"
MODEL = "model"
SECRET = "secret"
SOURCE = "source"

_DEPENDENCY_KEYS: frozenset[str] = frozenset({CONNECTION, MODEL, SECRET, SOURCE})

#: 归一失败分类；``None`` 表示当前 ready。
FailureClassification = str  # "transport" | "auth" | "rate_limit" | "manual" | ...


class CapabilityState(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True, slots=True)
class DependencyCondition:
    """一项外部能力的当前条件（doctor 投影与门禁判定的同一事实）。"""

    key: str
    state: CapabilityState
    reason: str | None = None
    classification: str | None = None
    manual: bool = False
    updated_at: str | None = None

    def to_mapping(self) -> dict[str, object]:
        return {
            "key": self.key,
            "state": self.state.value,
            "reason": self.reason,
            "classification": self.classification,
            "manual": self.manual,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class GateDecision:
    allowed: bool
    denied_by: tuple[DependencyCondition, ...] = field(default_factory=tuple)

    @property
    def reason(self) -> str | None:
        if self.allowed or not self.denied_by:
            return None
        parts = [
            f"{condition.key} {condition.state.value}"
            + (f"({condition.reason})" if condition.reason else "")
            for condition in self.denied_by
        ]
        return "依赖能力不可用: " + ", ".join(parts)


class CapabilityConditionStore(Protocol):
    def load(self) -> tuple[dict[str, DependencyCondition], dict[str, DependencyCondition]]: ...

    def save(
        self,
        automatic: Mapping[str, DependencyCondition],
        manual: Mapping[str, DependencyCondition],
    ) -> None: ...


class FileCapabilityConditionStore:
    """Four finite current facts, protected by the core lifetime writer lock."""

    def __init__(self, root: Path, *, workspace_id: str) -> None:
        self._path = root / "core" / "capability-state.json"
        self._workspace_id = workspace_id

    def load(self) -> tuple[dict[str, DependencyCondition], dict[str, DependencyCondition]]:
        self._check_path()
        if not self._path.exists():
            return {}, {}
        with self._path.open("rb") as handle:
            data = handle.read(64 * 1024 + 1)
        if len(data) > 64 * 1024:
            raise ValueError("capability state exceeds its finite record limit")
        payload = json.loads(data)
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "aitest.capability-state/1.0"
            or payload.get("workspace_id") != self._workspace_id
        ):
            raise ValueError("capability state workspace/schema cannot be verified")
        automatic = self._decode(payload.get("automatic"), manual=False)
        manual = self._decode(payload.get("manual"), manual=True)
        if set(automatic) != _DEPENDENCY_KEYS:
            raise ValueError("capability state lacks its finite automatic facts")
        return automatic, manual

    @staticmethod
    def _decode(value: object, *, manual: bool) -> dict[str, DependencyCondition]:
        if not isinstance(value, list):
            raise ValueError("capability facts must be finite lists")
        result = {}
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("unknown capability fact")
            key = item.get("key")
            if not isinstance(key, str) or key not in _DEPENDENCY_KEYS or key in result:
                raise ValueError("unknown capability fact")
            if item.get("key") != key or item.get("manual") is not manual:
                raise ValueError("capability fact identity cannot be verified")
            raw_state = item.get("state")
            if not isinstance(raw_state, str):
                raise ValueError("capability state must be a known string")
            state = CapabilityState(raw_state)
            if manual and state is not CapabilityState.DEGRADED:
                raise ValueError("manual capability control must describe a pause")
            for field_name in ("reason", "classification", "updated_at"):
                if item.get(field_name) is not None and not isinstance(item[field_name], str):
                    raise ValueError("capability fact field cannot be verified")
            result[key] = DependencyCondition(
                key=key,
                state=state,
                reason=item.get("reason"),
                classification=item.get("classification"),
                manual=manual,
                updated_at=item.get("updated_at"),
            )
        return result

    def save(
        self,
        automatic: Mapping[str, DependencyCondition],
        manual: Mapping[str, DependencyCondition],
    ) -> None:
        self._check_path()
        value, _changed = guard_value(
            {
                "schema": "aitest.capability-state/1.0",
                "workspace_id": self._workspace_id,
                "automatic": [item.to_mapping() for _key, item in sorted(automatic.items())],
                "manual": [item.to_mapping() for _key, item in sorted(manual.items())],
            }
        )
        if (
            value["schema"] != "aitest.capability-state/1.0"
            or value["workspace_id"] != self._workspace_id
        ):
            raise ValueError("capability state identity cannot be safely persisted")
        encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        if len(encoded) > 64 * 1024:
            raise ValueError("capability state exceeds its finite record limit")
        write_json(self._path, value)

    def _check_path(self) -> None:
        if any(
            path.is_symlink() or path.is_junction() for path in (self._path, *self._path.parents)
        ):
            raise ValueError("capability state cannot traverse a link")


class CapabilityGate:
    """动作 → 依赖键 登记表与运行时条件。线程安全。"""

    def __init__(
        self,
        *,
        action_dependencies: Mapping[str, frozenset[str]] | None = None,
        clock: Callable[[], str] | None = None,
        state_store: CapabilityConditionStore | None = None,
    ) -> None:
        self._lock = RLock()
        self._dependencies: dict[str, frozenset[str]] = {}
        self._conditional: dict[str, list[tuple[str, str, frozenset[str]]]] = {}
        self._conditions: dict[str, DependencyCondition] = {
            key: DependencyCondition(key=key, state=CapabilityState.NOT_CONFIGURED)
            for key in _DEPENDENCY_KEYS
        }
        self._clock = clock
        self._state_store = state_store
        self._automatic = dict(self._conditions)
        self._manual: dict[str, DependencyCondition] = {}
        if state_store is not None:
            automatic, manual = state_store.load()
            for key, condition in automatic.items():
                # Historical success cannot supply this core's actual configuration.
                self._automatic[key] = (
                    DependencyCondition(key=key, state=CapabilityState.NOT_CONFIGURED)
                    if condition.state is CapabilityState.READY
                    else condition
                )
            self._manual = manual
            self._conditions = {
                key: manual.get(key, value) for key, value in self._automatic.items()
            }
        if action_dependencies:
            for action, keys in action_dependencies.items():
                self.require(action, *keys)

    # ----- 登记表 -----------------------------------------------------

    def require(self, action: str, *keys: str) -> None:
        """声明某动作实际依赖的能力；重复声明取并集。"""
        if not action:
            raise ValueError("action 不能为空")
        for key in keys:
            if key not in _DEPENDENCY_KEYS:
                raise ValueError(f"未知能力键: {key}")
        with self._lock:
            existing = self._dependencies.get(action, frozenset())
            self._dependencies[action] = existing | frozenset(keys)

    def dependencies_of(self, action: str) -> frozenset[str]:
        with self._lock:
            return self._dependencies.get(action, frozenset())

    def require_if(
        self, action: str, *, parameter: str, equals: str, keys: tuple[str, ...]
    ) -> None:
        """同一动作的不同模式按真实依赖准入；条件也可投影到 doctor。"""
        if not action or not parameter:
            raise ValueError("action/parameter 不能为空")
        if any(key not in _DEPENDENCY_KEYS for key in keys):
            raise ValueError("未知能力键")
        condition = (parameter, equals, frozenset(keys))
        with self._lock:
            conditions = self._conditional.setdefault(action, [])
            if condition not in conditions:
                conditions.append(condition)

    def affected_actions(self, key: str) -> tuple[str, ...]:
        with self._lock:
            actions = {action for action, keys in self._dependencies.items() if key in keys}
            actions.update(
                action
                for action, conditions in self._conditional.items()
                if any(key in keys for _, _, keys in conditions)
            )
            return tuple(sorted(actions))

    # ----- 条件写入 ---------------------------------------------------

    def configure(self, key: str) -> None:
        """本核心获得真实配置；已有故障仍需新的实际成功事实。"""
        if key not in _DEPENDENCY_KEYS:
            raise ValueError(f"未知能力键: {key}")
        with self._lock:
            if self._automatic[key].state is CapabilityState.NOT_CONFIGURED:
                self._set(DependencyCondition(key=key, state=CapabilityState.READY))

    def report(
        self,
        key: str,
        *,
        healthy: bool,
        reason: str | None = None,
        classification: str | None = None,
    ) -> None:
        """写入一次适配器事实；成功事实恢复自动降级，不清人工降级。"""
        if key not in _DEPENDENCY_KEYS:
            raise ValueError(f"未知能力键: {key}")
        with self._lock:
            if healthy:
                self._set(DependencyCondition(key=key, state=CapabilityState.READY))
            else:
                self._set(
                    DependencyCondition(
                        key=key,
                        state=CapabilityState.DEGRADED,
                        reason=reason,
                        classification=classification or "transport",
                        manual=False,
                    )
                )

    def degrade(self, key: str, reason: str, *, classification: str = "manual") -> None:
        """人工降级：记录操作者意图，探测成功也不自动清除。"""
        self._set(
            DependencyCondition(
                key=key,
                state=CapabilityState.DEGRADED,
                reason=reason,
                classification=classification,
                manual=True,
            )
        )

    def restore(self, key: str) -> None:
        """显式解除人工暂停；仍保留最近自动故障或缺配置。"""
        if key not in _DEPENDENCY_KEYS:
            raise ValueError(f"未知能力键: {key}")
        with self._lock:
            manual = dict(self._manual)
            manual.pop(key, None)
            self._publish(dict(self._automatic), manual, key)

    def condition(self, key: str) -> DependencyCondition:
        with self._lock:
            return self._conditions[key]

    def conditions(self) -> tuple[DependencyCondition, ...]:
        with self._lock:
            return tuple(self._conditions[key] for key in sorted(self._conditions))

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "dependencies": [
                    self._conditions[key].to_mapping() for key in sorted(self._conditions)
                ],
                "automatic_conditions": [
                    value.to_mapping() for key, value in sorted(self._automatic.items())
                ],
                "manual_controls": [
                    value.to_mapping() for key, value in sorted(self._manual.items())
                ],
                "action_dependencies": {
                    action: sorted(keys) for action, keys in sorted(self._dependencies.items())
                },
                "conditional_action_dependencies": {
                    action: [
                        {"parameter": field, "equals": value, "keys": sorted(keys)}
                        for field, value, keys in conditions
                    ]
                    for action, conditions in sorted(self._conditional.items())
                },
            }

    # ----- 门禁 -------------------------------------------------------

    def check(self, action: str) -> GateDecision:
        """未登记依赖的动作默认放行；依赖项非 ready 即拒绝。"""
        return self.check_command(action, {})

    def check_command(self, action: str, parameters: Mapping[str, object]) -> GateDecision:
        """按命令的实际模式增加条件依赖，与普通动作共用同一状态事实。"""
        with self._lock:
            keys = self._dependencies.get(action, frozenset())
            for field, value, conditional_keys in self._conditional.get(action, []):
                if parameters.get(field) == value:
                    keys = keys | conditional_keys
            denied = tuple(
                self._conditions[key]
                for key in sorted(keys)
                if self._conditions[key].state != CapabilityState.READY
            )
        return GateDecision(allowed=not denied, denied_by=denied)

    # ----- 内部 -------------------------------------------------------

    def _set(self, condition: DependencyCondition) -> None:
        if condition.key not in _DEPENDENCY_KEYS:
            raise ValueError(f"未知能力键: {condition.key}")
        stamped = condition
        if self._clock is not None and condition.updated_at is None:
            stamped = DependencyCondition(
                key=condition.key,
                state=condition.state,
                reason=condition.reason,
                classification=condition.classification,
                manual=condition.manual,
                updated_at=self._clock(),
            )
        with self._lock:
            automatic, manual = dict(self._automatic), dict(self._manual)
            (manual if stamped.manual else automatic)[stamped.key] = stamped
            self._publish(automatic, manual, stamped.key)

    def _publish(
        self,
        automatic: dict[str, DependencyCondition],
        manual: dict[str, DependencyCondition],
        key: str,
    ) -> None:
        if self._state_store is not None:
            try:
                self._state_store.save(automatic, manual)
            except BaseException:
                # Preserve the requested control in memory while reporting the
                # uncertain save. A later full save may confirm it; an unrelated
                # successful fact must not implicitly discard a pending pause.
                self._automatic, self._manual = automatic, manual
                self._conditions[key] = DependencyCondition(
                    key=key,
                    state=CapabilityState.DEGRADED,
                    reason="capability_state_persistence_uncertain",
                    classification="storage",
                )
                raise
        self._automatic, self._manual = automatic, manual
        self._conditions = {key: manual.get(key, value) for key, value in automatic.items()}


__all__ = [
    "CONNECTION",
    "MODEL",
    "SECRET",
    "SOURCE",
    "CapabilityGate",
    "FileCapabilityConditionStore",
    "CapabilityState",
    "DependencyCondition",
    "GateDecision",
]
