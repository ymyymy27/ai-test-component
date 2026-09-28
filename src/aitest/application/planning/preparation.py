"""准备意图与幂等规则：身份、输入摘要与四态判定。

对应一期架构文档《01-项目与计划》第 11 节「准备请求与业务身份合同」与
需求 P1-FR07「同意图去重与明确重跑」。

本模块只提供**纯规则**：值对象、规范摘要与判定函数。记录的登记与落盘经
`WorkspaceUnitOfWork` / `RecordRepository`（A 的端口）完成，**不在本模块内实现**。

三条容易写错的规则，集中在此说明：

1. `payload_hash` **不得纳入传输层参数**（`request_id`、重试次数等）。否则"同号重传"
   会被判成"输入不同"，与"重传不变"直接矛盾。
2. 判定次序是**摘要冲突 > 来源变化 > 幂等复用**。搞反会把冲突误报成可重试。
3. 业务顺序按**提交序号**（`created_at_commit`）判断，不使用系统时间。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from hashlib import sha256


class PreparationDecision(StrEnum):
    """同一准备请求再次到达时的判定结果（架构文档第 11 节）。"""

    NEW = "new"
    REUSED = "reused"
    CONFLICTED = "conflicted"
    NEEDS_REPREPARE = "needs_reprepare"


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_revision(value: int, name: str) -> None:
    if value < 1:
        raise ValueError(f"{name} must be >= 1")


@dataclass(frozen=True, slots=True)
class InputRevisions:
    """一次准备请求解析到的各来源修订。

    架构文档第 11 节："提交时校验项目、绑定、计划、规则、模板、环境修订及准备请求是否仍有效。"
    需求 P1-FR07："准备运行生成持久 intent_id，绑定项目、源码、环境、计划、规则、模板和动作。"

    每一项都必须 `>= 1`：分册要求校验"是否仍有效"，而既有约定是"修订从 1 起"，
    用 `0` 表示"无"会与既有约定冲突。
    """

    project_revision: int
    binding_revision: int
    snapshot_revision: int
    environment_revision: int
    plan_revision: int
    rules_revision: int
    template_revision: int
    scope_revision: int

    def __post_init__(self) -> None:
        for field_name in _REVISION_FIELDS:
            _require_revision(getattr(self, field_name), field_name)


_REVISION_FIELDS: tuple[str, ...] = tuple(
    field.name for field in fields(InputRevisions)
)

#: 参与摘要的业务输入字段；**不含**项目/客户端/请求号与任何传输层参数。
PAYLOAD_FIELDS: tuple[str, ...] = _REVISION_FIELDS + (
    "binding_form",
    "selected_paths",
    "exclusion_rules",
    "run_tier",
    "driver",
    "case_revision_ids",
    "rule_version_ids",
    "template_version_ids",
)


def _canonical_item(value: object) -> object:
    if isinstance(value, (list, tuple, set, frozenset)):
        return sorted(str(_canonical_item(item)) for item in value)
    return value


def payload_hash(payload: Mapping[str, object]) -> str:
    """对**业务输入**计算规范摘要；键排序、紧凑分隔符，与字典次序无关。

    只应传入业务输入。把 `request_id`、重试次数、接收时间一类传输层参数放进来，
    会让"同号重传"被误判为"输入不同"。
    """
    canonical = {
        key: _canonical_item(payload[key]) for key in sorted(payload)
    }
    encoded = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


# ------------------------------------------------------------------ 请求与记录


@dataclass(frozen=True, slots=True)
class PreparationRequest:
    """一次准备请求的业务身份与输入。

    三个身份键的组合次序固定为 `(project_id, client_id, prepare_request_id)`
    （架构文档第 11 节）。
    """

    project_id: str
    client_id: str
    prepare_request_id: str
    payload_hash: str
    input_revisions: InputRevisions

    def __post_init__(self) -> None:
        _require_text(self.project_id, "project_id")
        _require_text(self.client_id, "client_id")
        _require_text(self.prepare_request_id, "prepare_request_id")
        _require_text(self.payload_hash, "payload_hash")

    @property
    def identity_key(self) -> tuple[str, str, str]:
        """幂等键；三个分量缺一不可。"""
        return (self.project_id, self.client_id, self.prepare_request_id)


@dataclass(frozen=True, slots=True)
class PreparationRecord:
    """已登记的准备记录。

    `intent_id` **与准备记录同一次提交**（架构文档第 11 节），因此它随记录一起冻结；
    跨入口恢复通过 `intent_id` 或准备查询取得已有意图。

    取消只记录取消，**不自动创建新意图**（需求 P1-FR07），因此 `cancelled` 只是事实位，
    不参与幂等判定。
    """

    request: PreparationRequest
    intent_id: str
    created_at_commit: str
    cancelled: bool = False

    def __post_init__(self) -> None:
        _require_text(self.intent_id, "intent_id")
        _require_text(self.created_at_commit, "created_at_commit")
        # 传输层的 request_id 不能代替业务身份；本值对象不接受 request_id，
        # 这里再挡住"把请求号当意图号"这一种实际会发生的写法。
        if self.intent_id == self.request.prepare_request_id:
            raise ValueError(
                "intent_id must not be the transport-level prepare_request_id"
            )

    def matches_identity(self, request: PreparationRequest) -> bool:
        return self.request.identity_key == request.identity_key


@dataclass(frozen=True, slots=True)
class PreparationLookup:
    """判定结果；携带调用方给出提示所需的最少信息。"""

    decision: PreparationDecision
    intent_id: str | None = None
    changed_inputs: tuple[str, ...] = ()


def changed_inputs(
    previous: InputRevisions, current: InputRevisions
) -> tuple[str, ...]:
    """列出发生变化的输入项名；顺序固定为字段声明顺序，便于稳定提示与测试。"""
    return tuple(
        name
        for name in _REVISION_FIELDS
        if getattr(previous, name) != getattr(current, name)
    )


def decide_preparation(
    request: PreparationRequest,
    existing: PreparationRecord | None,
) -> PreparationLookup:
    """判定同一准备请求再次到达时该怎么做。

    判定次序（设计说明第 3.5 节，**次序是硬性的**）：

    1. 无记录 → `NEW`；
    2. 摘要不同 → `CONFLICTED`（"同键异输入摘要返回冲突，不覆盖"）；
    3. 摘要相同但来源修订不同 → `NEEDS_REPREPARE`
       （"同请求期间源码已变则返回原意图及'依据需重新准备'"）；
    4. 摘要与修订都相同 → `REUSED`。

    第 2 条先于第 3 条：摘要不同是**冲突**，同一批输入而来源修订变化才是"需重新准备"。
    """
    if existing is None:
        return PreparationLookup(decision=PreparationDecision.NEW)

    if existing.request.payload_hash != request.payload_hash:
        return PreparationLookup(decision=PreparationDecision.CONFLICTED)

    changed = changed_inputs(existing.request.input_revisions, request.input_revisions)
    if changed:
        return PreparationLookup(
            decision=PreparationDecision.NEEDS_REPREPARE,
            intent_id=existing.intent_id,
            changed_inputs=changed,
        )

    return PreparationLookup(
        decision=PreparationDecision.REUSED,
        intent_id=existing.intent_id,
    )


__all__ = [
    "PAYLOAD_FIELDS",
    "InputRevisions",
    "PreparationDecision",
    "PreparationLookup",
    "PreparationRecord",
    "PreparationRequest",
    "changed_inputs",
    "decide_preparation",
    "payload_hash",
]
