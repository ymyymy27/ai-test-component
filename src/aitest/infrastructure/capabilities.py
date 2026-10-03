"""动作级能力门（A-10）：按动作的真实依赖降级，不扩散为整体不可用。

某一外部能力（连接、模型、凭据、来源核对）故障或未配置时，只拦截**实际
依赖该能力**的动作；不依赖它的本地动作（保存、查询、恢复、doctor 等）
必须继续可用。判定是机械事实，三个状态：

- ``ready``：能力已配置且最近事实正常；
- ``degraded``：能力曾配置但最近事实故障（含归一失败分类）或被人工降级；
- ``not_configured``：默认装配未获得该能力的显式配置（缺端点/缺凭据）。

门**不探测、不重连**：探测事实由连接/模型/来源适配器产生后经
:meth:`CapabilityGate.report` / :meth:`degrade` / :meth:`restore` 写入；
人工降级与自动事实分开记录，恢复必须由一次新的成功事实或显式
:meth:`restore` 清除——不按时间静默自愈。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock

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


class CapabilityGate:
    """动作 → 依赖键 登记表与运行时条件。线程安全。"""

    def __init__(
        self,
        *,
        action_dependencies: Mapping[str, frozenset[str]] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._lock = RLock()
        self._dependencies: dict[str, frozenset[str]] = {}
        self._conditions: dict[str, DependencyCondition] = {
            key: DependencyCondition(key=key, state=CapabilityState.NOT_CONFIGURED)
            for key in _DEPENDENCY_KEYS
        }
        self._clock = clock
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

    def affected_actions(self, key: str) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                sorted(action for action, keys in self._dependencies.items() if key in keys)
            )

    # ----- 条件写入 ---------------------------------------------------

    def configure(self, key: str) -> None:
        """能力获得显式真实配置（未证实可用前视为 ready）。"""
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
        with self._lock:
            current = self._conditions[key]
            if healthy:
                if current.manual:
                    # 人工降级必须显式恢复，探测成功不擅自解除。
                    return
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
        """显式恢复（人工或换核心后重连成功的装配点调用）。"""
        self._set(DependencyCondition(key=key, state=CapabilityState.READY))

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
                "action_dependencies": {
                    action: sorted(keys)
                    for action, keys in sorted(self._dependencies.items())
                },
            }

    # ----- 门禁 -------------------------------------------------------

    def check(self, action: str) -> GateDecision:
        """未登记依赖的动作默认放行；依赖项非 ready 即拒绝。"""
        with self._lock:
            keys = self._dependencies.get(action, frozenset())
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
            self._conditions[condition.key] = stamped


__all__ = [
    "CONNECTION",
    "MODEL",
    "SECRET",
    "SOURCE",
    "CapabilityGate",
    "CapabilityState",
    "DependencyCondition",
    "GateDecision",
]
