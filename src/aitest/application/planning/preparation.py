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
from typing import Final, Literal

#: 准备记录的聚合类别；取值与 `substrate.AggregateKind` 一致。
PREPARATION_AGGREGATE_KIND: Final[Literal["preparation_record"]] = "preparation_record"

#: 身份键的分隔符。用不可打印字符，避免业务取值里出现同名字符造成歧义拼接。
_IDENTITY_SEPARATOR: Final[str] = "\u001f"
_INTENT_PREFIX: Final[str] = "intent-"
_RECORD_PREFIX: Final[str] = "prep-"


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


# ------------------------------------------------------------------ 业务身份


def preparation_identity_digest(
    *, project_id: str, client_id: str, prepare_request_id: str
) -> str:
    """三个业务身份键的稳定摘要。

    准备记录标识与业务意图标识**都由它派生**，因此两者同域、定长、无歧义：

    - 多项目/多客户端下同一个 `prepare_request_id` 不会互相覆盖
      （存储层的修订计数是**全局按 `record_id`** 计的，不含项目维度）；
    - 记录标识长度固定，不受业务取值长度影响
      （有限查询的 `record_id` 有长度上限）；
    - 可以从意图标识反推记录标识——跨入口按 `intent_id` 恢复需要这一步。
    """
    for value, name in (
        (project_id, "project_id"),
        (client_id, "client_id"),
        (prepare_request_id, "prepare_request_id"),
    ):
        _require_text(value, name)
    raw = _IDENTITY_SEPARATOR.join((project_id, client_id, prepare_request_id))
    return sha256(raw.encode("utf-8")).hexdigest()[:40]


def preparation_record_id(
    *, project_id: str, client_id: str, prepare_request_id: str
) -> str:
    """`preparation_record` 的稳定记录标识。"""
    return _RECORD_PREFIX + preparation_identity_digest(
        project_id=project_id,
        client_id=client_id,
        prepare_request_id=prepare_request_id,
    )


def preparation_intent_id(
    *, project_id: str, client_id: str, prepare_request_id: str
) -> str:
    """业务意图标识；由应用派生，**不是**传输层的 `prepare_request_id`。"""
    return _INTENT_PREFIX + preparation_identity_digest(
        project_id=project_id,
        client_id=client_id,
        prepare_request_id=prepare_request_id,
    )


def record_id_for_intent_id(intent_id: str) -> str:
    """由意图标识反推准备记录的记录标识。

    跨入口恢复只有 `intent_id` 时走这条路：**不新增按任意 payload 字段的查询**，
    也不做全表扫描（存储与恢复合同第 13 节）。
    """
    _require_text(intent_id, "intent_id")
    if not intent_id.startswith(_INTENT_PREFIX):
        raise ValueError("intent_id was not derived by preparation_intent_id()")
    return _RECORD_PREFIX + intent_id[len(_INTENT_PREFIX) :]


#: 参与摘要的**请求侧**业务输入字段。
#:
#: **不含"实际观察到的来源事实"**（`InputRevisions` 的八项，以及计划/用例/规则/模板的
#: 修订号与快照字节身份）：来源或依据变了要报"依据需重新准备"，而不是"同键异输入冲突"
#: （架构文档第 11 节）——判定次序是"冲突 > 需重新准备"，把观察结果混进摘要会把后者盖住。
#: 来源修订由 `decide_preparation()` 单独比对。
#: **也不含任何传输层参数**（`request_id`、重试次数、接收时间）。
#:
#: 本元组与 `prepare_run.preparation_payload()` 的键集合**必须逐字一致**，
#: `tests/unit/test_prepare_run.py` 有对照测试。
PAYLOAD_FIELDS: tuple[str, ...] = (
    "binding_form",
    "selected_paths",
    "exclusion_rules",
    "refetch_dependencies",
    "run_tier",
    "driver",
    "case_revision_ids",
    "rule_version_ids",
    "template_version_ids",
    "selected_case_ids",
    "skipped_scope",
    "applicability_exclusions",
    "source_snippets_enabled",
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


def preparation_record_payload(record: PreparationRecord) -> dict[str, object]:
    """准备记录的落盘形状——**由本模块唯一决定**。

    必须**自足**：只靠这份 payload 就要能把 `PreparationRecord` 完整重建出来，
    否则"重启后按 `(project_id, client_id, prepare_request_id)` 查回原意图"
    会静默失效（进程内还看得见，重启后就查不回来）。

    因此这里落的是**全部身份与摘要字段**，不是只落一个 `intent_id`。
    """
    return {
        "project_id": record.request.project_id,
        "client_id": record.request.client_id,
        "prepare_request_id": record.request.prepare_request_id,
        "payload_hash": record.request.payload_hash,
        "intent_id": record.intent_id,
        "created_at_commit": record.created_at_commit,
        "cancelled": record.cancelled,
        "input_revisions": {
            name: getattr(record.request.input_revisions, name)
            for name in _REVISION_FIELDS
        },
    }


def _payload_text(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"preparation payload is missing a usable {name}")
    return value


def preparation_record_from_payload(payload: Mapping[str, object]) -> PreparationRecord:
    """按 `preparation_record_payload()` 的落盘形状重建准备记录。

    缺字段、类型不符一律**抛错**，不填默认值：把读不懂的记录当成"没有记录"
    会让同键异输入被误判成可以新建，从而覆盖掉既有意图。
    """
    raw_revisions = payload.get("input_revisions")
    if not isinstance(raw_revisions, Mapping):
        raise ValueError("preparation payload is missing input_revisions")
    revisions: dict[str, int] = {}
    for name in _REVISION_FIELDS:
        value = raw_revisions.get(name)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"preparation payload is missing a usable {name}")
        revisions[name] = value

    cancelled = payload.get("cancelled", False)
    if not isinstance(cancelled, bool):
        raise ValueError("preparation payload has a non-boolean cancelled flag")

    return PreparationRecord(
        request=PreparationRequest(
            project_id=_payload_text(payload, "project_id"),
            client_id=_payload_text(payload, "client_id"),
            prepare_request_id=_payload_text(payload, "prepare_request_id"),
            payload_hash=_payload_text(payload, "payload_hash"),
            input_revisions=InputRevisions(**revisions),
        ),
        intent_id=_payload_text(payload, "intent_id"),
        created_at_commit=_payload_text(payload, "created_at_commit"),
        cancelled=cancelled,
    )



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
    "PREPARATION_AGGREGATE_KIND",
    "InputRevisions",
    "PreparationDecision",
    "PreparationLookup",
    "PreparationRecord",
    "PreparationRequest",
    "changed_inputs",
    "decide_preparation",
    "payload_hash",
    "preparation_identity_digest",
    "preparation_intent_id",
    "preparation_record_from_payload",
    "preparation_record_id",
    "preparation_record_payload",
    "record_id_for_intent_id",
]
