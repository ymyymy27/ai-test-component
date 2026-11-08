"""项目理解、业务功能目录、测试意图与覆盖承诺的纯领域对象。

合同来源：

- 设计文档《03-AI主导与非开发者体验设计草案》4.1—4.8、8.2—8.3；
- 架构文档《01-项目与计划》第 3 节“业务功能目录与覆盖投影（实现补充）”；
- 架构文档《03-报告与缺陷》第 2 节。

本模块只承载不可变记录、字段校验与枚举词汇，不做 I/O、不调用模型，也不依赖
application / infrastructure / contracts。覆盖事实由
``aitest.domain.review.function_coverage.FunctionCoverage`` 表达，**不写回**
业务功能目录；`TestIntent` 是现有 `AcceptanceItem` 的业务视图，不新增第二套
验收对象。

设计文档对“来源”使用了三套词汇：项目信号是
``deterministic / ai_suggested / user_corrected``，业务功能是
``deterministic / ai_suggested / user_confirmed``，测试意图是
``user / ai_suggested``。这里按记录分别建模，避免一个枚举把非法组合也放进来。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_confidence(value: float, name: str) -> None:
    if math.isnan(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")


def _require_items(values: Iterable[str], name: str) -> None:
    for value in values:
        if not value.strip():
            raise ValueError(f"{name} must not contain empty values")


def _require_unique(values: Iterable[str], name: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ValueError(f"{name} must be unique")


def _require_dimensions(values: tuple[str, ...]) -> None:
    if not values:
        raise ValueError("required_dimensions must not be empty")
    _require_items(values, "required_dimensions")
    _require_unique(values, "required_dimensions")


class BusinessFunctionKind(StrEnum):
    """业务功能层级：业务域 → capability → flow → operation → rule。"""

    CAPABILITY = "capability"
    FLOW = "flow"
    OPERATION = "operation"
    RULE = "rule"


class FunctionTrigger(StrEnum):
    """业务功能被触发的入口类型（设计文档 4.4）。"""

    HTTP = "http"
    PAGE = "page"
    CLI = "cli"
    AGENT = "agent"
    EVENT = "event"
    SCHEDULED_TASK = "scheduled_task"
    STATE_TRANSITION = "state_transition"


class BusinessFunctionOrigin(StrEnum):
    """业务功能条目的来源；用户确认与用户纠正是不同事实。"""

    DETERMINISTIC = "deterministic"
    AI_SUGGESTED = "ai_suggested"
    USER_CONFIRMED = "user_confirmed"


class ReviewState(StrEnum):
    """业务功能的确认状态；未经确认的条目不能当作完整清单。"""

    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    NEEDS_REVIEW = "needs_review"


class ProjectSignalOrigin(StrEnum):
    """项目信号的来源（设计文档 4.2）。"""

    DETERMINISTIC = "deterministic"
    AI_SUGGESTED = "ai_suggested"
    USER_CORRECTED = "user_corrected"


class TestIntentOrigin(StrEnum):
    """测试意图的来源（设计文档 4.4）。"""

    # pytest 会按 Test* 前缀尝试收集本类；它是领域词汇，不是测试。
    __test__ = False

    USER = "user"
    AI_SUGGESTED = "ai_suggested"


class CoverageClaimStatus(StrEnum):
    """覆盖承诺状态；只有四态，`unknown` 属于清单/维度状态，不属于承诺。"""

    PROVEN = "proven"
    PARTIAL = "partial"
    NOT_PROVEN = "not_proven"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class BusinessFunction:
    """稳定目录中的业务功能，只描述“项目能做什么”。

    覆盖状态不保存在这里；目录本身不记录覆盖事实。`required_dimensions`
    必须非空且唯一：空集合会把功能伪装成无需验证，重复项会把一个维度
    重复计数。`aliases` 允许为空（多数功能没有别名），但每个别名必须非空
    且不得重复；`canonical_function_id` 非空时不能是空串，并须在目录内解析。
    """

    function_id: str
    name: str
    parent_function_id: str | None
    kind: BusinessFunctionKind
    trigger: FunctionTrigger
    business_object: str
    expected_outcome: str
    conditions: tuple[str, ...]
    required_dimensions: tuple[str, ...]
    source_refs: tuple[str, ...]
    origin: BusinessFunctionOrigin
    confidence: float
    review_state: ReviewState
    understanding_revision: int
    canonical_function_id: str | None
    aliases: tuple[str, ...]
    source_snapshot_id: str

    def __post_init__(self) -> None:
        _require_text(self.function_id, "function_id")
        if self.parent_function_id is not None:
            _require_text(self.parent_function_id, "parent_function_id")
        if self.canonical_function_id is not None:
            _require_text(self.canonical_function_id, "canonical_function_id")
        _require_text(self.source_snapshot_id, "source_snapshot_id")
        _require_items(self.conditions, "conditions")
        _require_items(self.source_refs, "source_refs")
        _require_items(self.aliases, "aliases")
        _require_unique(self.aliases, "aliases")
        _require_dimensions(self.required_dimensions)
        _require_confidence(self.confidence, "confidence")
        if self.understanding_revision < 1:
            raise ValueError("understanding_revision must be >= 1")
        object.__setattr__(self, "kind", BusinessFunctionKind(str(self.kind)))
        object.__setattr__(self, "trigger", FunctionTrigger(str(self.trigger)))
        object.__setattr__(
            self, "origin", BusinessFunctionOrigin(str(self.origin))
        )
        object.__setattr__(self, "review_state", ReviewState(str(self.review_state)))


@dataclass(frozen=True, slots=True)
class BusinessFunctionCatalog:
    """随 understanding 版本化的稳定目录。

    `function_id` 必须唯一；`parent_function_id` 允许为空（根功能），非空时
    必须能在同一目录中解析。父链成环不是合法层级，因此一并拒绝。
    `canonical_function_id` 同样必须解析到同一目录内的其他功能，不能指向自己。
    """

    understanding_id: str
    revision: int
    functions: tuple[BusinessFunction, ...]

    def __post_init__(self) -> None:
        _require_text(self.understanding_id, "understanding_id")
        if self.revision < 1:
            raise ValueError("catalog revision must be >= 1")
        by_id = {function.function_id: function for function in self.functions}
        if len(by_id) != len(self.functions):
            raise ValueError("function_id must be unique within a catalog")
        for function in self.functions:
            parent_id = function.parent_function_id
            if parent_id is not None and parent_id not in by_id:
                raise ValueError(f"unresolved parent function: {parent_id}")
        for function in self.functions:
            canonical_id = function.canonical_function_id
            if canonical_id is None:
                continue
            if canonical_id == function.function_id:
                raise ValueError(
                    f"canonical function must not reference itself: {canonical_id}"
                )
            if canonical_id not in by_id:
                raise ValueError(f"unresolved canonical function: {canonical_id}")
        for function in self.functions:
            seen = {function.function_id}
            canonical_id = function.canonical_function_id
            while canonical_id is not None:
                if canonical_id in seen:
                    raise ValueError(
                        f"business function canonical cycle at: {canonical_id}"
                    )
                seen.add(canonical_id)
                canonical_id = by_id[canonical_id].canonical_function_id
        for function in self.functions:
            seen = {function.function_id}
            parent_id = function.parent_function_id
            while parent_id is not None:
                if parent_id in seen:
                    raise ValueError(
                        f"business function parent cycle at: {parent_id}"
                    )
                seen.add(parent_id)
                parent_id = by_id[parent_id].parent_function_id


@dataclass(frozen=True, slots=True)
class ProjectSignals:
    """项目信号；`confidence` 按信号分别记录，不做一个总置信度。"""

    detected_stack: tuple[str, ...]
    entry_candidates: tuple[str, ...]
    capabilities: tuple[str, ...]
    risk_signals: tuple[str, ...]
    confidence: Mapping[str, float]
    evidence_refs: tuple[str, ...]
    origin: ProjectSignalOrigin

    def __post_init__(self) -> None:
        for name in (
            "detected_stack",
            "entry_candidates",
            "capabilities",
            "risk_signals",
            "evidence_refs",
        ):
            _require_items(getattr(self, name), name)
        for signal, value in self.confidence.items():
            _require_text(signal, "signal confidence key")
            _require_confidence(value, f"confidence for {signal}")
        object.__setattr__(
            self, "confidence", MappingProxyType(dict(self.confidence))
        )
        object.__setattr__(self, "origin", ProjectSignalOrigin(str(self.origin)))


@dataclass(frozen=True, slots=True)
class CapabilityGap:
    """插件/适配器能力缺口，不是项目属性。

    缺口由适配器能力修订产生；能力升级后按 `capability_revision` 重算，
    不必重新扫描项目。`blocks_full` 表示该缺口是否阻止 `full`。
    """

    gap_id: str
    scope: str
    missing_capability: str
    capability_revision: str
    reason: str
    workaround: str | None
    blocks_full: bool
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "gap_id",
            "scope",
            "missing_capability",
            "capability_revision",
            "reason",
        ):
            _require_text(getattr(self, name), name)
        if self.workaround is not None:
            _require_text(self.workaround, "workaround")
        _require_items(self.source_refs, "source_refs")


@dataclass(frozen=True, slots=True)
class TestIntent:
    """把“为什么测”固定下来；至少关联一个业务功能和一个必要维度。"""

    # pytest 会按 Test* 前缀尝试收集本类；它是领域记录，不是测试。
    __test__ = False

    intent_id: str
    statement: str
    risk: str
    function_ids: frozenset[str]
    required_dimensions: tuple[str, ...]
    acceptance_condition: str
    rationale: str
    origin: TestIntentOrigin
    confidence: float
    understanding_revision: int

    def __post_init__(self) -> None:
        _require_text(self.intent_id, "intent_id")
        if not self.function_ids:
            raise ValueError("function_ids must not be empty")
        _require_items(self.function_ids, "function_ids")
        _require_dimensions(self.required_dimensions)
        _require_confidence(self.confidence, "confidence")
        if self.understanding_revision < 1:
            raise ValueError("understanding_revision must be >= 1")
        object.__setattr__(self, "origin", TestIntentOrigin(str(self.origin)))


@dataclass(frozen=True, slots=True)
class CoverageClaim:
    """intent × function × dimension 的连接记录（设计文档 4.4、4.6）。

    “每个 `CoverageClaim` 至少映射一条用例/证据”，所以 `case_ids` 非空。
    `plan_revision` 指向本次计划修订；claim 不是目录字段。
    """

    claim_id: str
    intent_id: str
    function_id: str
    dimension: str
    plan_revision: int
    status: CoverageClaimStatus
    case_ids: frozenset[str]

    def __post_init__(self) -> None:
        for name in ("claim_id", "intent_id", "function_id", "dimension"):
            _require_text(getattr(self, name), name)
        if self.plan_revision < 1:
            raise ValueError("plan_revision must be >= 1")
        if not self.case_ids:
            raise ValueError("a coverage claim must map at least one case")
        _require_items(self.case_ids, "case_ids")
        object.__setattr__(self, "status", CoverageClaimStatus(str(self.status)))
