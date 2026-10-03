"""计划域材料的落盘编排：把 `Case` / `AcceptanceScope` / `ConfirmationRecord` 存成不可变记录。

分工与 `application/project/persistence.py` 一致：
**序列化在 `serialization.py`**，本模块只负责**事务边界与记录标识**。

记录标识（与 `rule_version` 用 `rule_id` 同一惯例）：

| 对象 | `aggregate_kind` | `record_id` |
| --- | --- | --- |
| `Case` | `case` | `case_id` |
| `AcceptanceScope` | `acceptance_scope` | `scope_id` |
| `ConfirmationRecord` | `case_link` | `confirmation_id` |

三条与项目上下文相同的硬约束：

1. **不自动覆盖**：`expected_revision` 是调用方**看到过**的修订；
   `None` 表示"认定这是新建"，此时已有记录就抛 `ConcurrentEditError` 并带上当前修订；
2. **一次短事务**：每个 `save_*` 自带 `open` / `commit`，调用时不得已有打开的事务；
3. **只经端口**：只用 B 的 `UnitOfWork` / `RecordReader` 两个窄协议，
   不直接读写任何业务文件（端口实现归 A）。

另外两条本模块特有的约定：

- **确认记录只追加**：`ConfirmationRecord` 一旦登记就不再改写，
  因此 `save_confirmation()` **不接受** `expected_revision`，恒以"新建"登记；
  需要更正时登记一条新确认，而不是覆盖旧的那条。
- **用例与范围以修订递增追加**：改内容要新建修订，历史修订仍按原样读得到
  （需求 P1-FR06 要求"依据变化不覆盖冻结记录"）。
"""

from __future__ import annotations

from aitest.application.planning.portable import rule_draft_to_payload
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
    acceptance_scope_to_payload,
    case_from_payload,
    case_to_payload,
    confirmation_from_payload,
    confirmation_to_payload,
)
from aitest.application.planning.substrate import (
    AggregateKind,
    ConcurrentEditError,
    RecordReader,
    StagedRevision,
    UnitOfWork,
    transaction,
)
from aitest.domain.planning.plans import (
    AcceptanceScope,
    Case,
    ConfirmationRecord,
)
from aitest.domain.planning.rules import RuleDraft

#: 三类记录的类别取值；与 `substrate.AggregateKind` 逐字一致。
CASE_AGGREGATE: AggregateKind = "case"
ACCEPTANCE_SCOPE_AGGREGATE: AggregateKind = "acceptance_scope"
CONFIRMATION_AGGREGATE: AggregateKind = "case_link"
RULE_DRAFT_AGGREGATE: AggregateKind = "rule_draft"


def _stage_and_commit(
    *,
    project_id: str,
    aggregate_kind: AggregateKind,
    record_id: str,
    expected_revision: int | None,
    payload: dict[str, object],
    unit_of_work: UnitOfWork,
    revision_carrying_key: str | None = None,
) -> StagedRevision:
    """一次短事务：在**事务上下文**里暂存并提交。

    用 `transaction()` 而不是裸 `open()`：真实底座的 `open()` 会取工作空间级
    排他写锁，收尾一旦靠调用方的记性，抛异常或提前返回就会把锁留在这个进程里；
    上下文对象负责收尾（见 `substrate.Transaction`）。

    `revision_carrying_key` 给出**正文里带修订号的键**（`case` 用 `"revision"`）；
    给出时会在暂存前核对"正文修订 == 这次要分配的仓储修订"（检查项 B-11）。
    核对放在事务**内部**、`stage_record` **之前**：这样底座的并发校验先生效，
    本核对只处理"修订号本身的错配"。
    """
    with transaction(unit_of_work, project_id) as tx:
        if revision_carrying_key is not None:
            _require_revision_matches_assignment(
                aggregate_kind=aggregate_kind,
                record_id=record_id,
                expected_revision=expected_revision,
                payload=payload,
                key=revision_carrying_key,
            )
        staged = tx.stage_record(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            expected_revision=expected_revision,
            payload=payload,
        )
        tx.commit()
    return staged


def _require_revision_matches_assignment(
    *,
    aggregate_kind: AggregateKind,
    record_id: str,
    expected_revision: int | None,
    payload: dict[str, object],
    key: str,
) -> None:
    """正文修订必须等于**这次实际会分配的仓储修订**（检查项 B-11）。

    底座的语义是：`expected_revision` 是"调用方看到的当前仓储修订"，
    `None` / `0` 表示"我认定这是新建"，成功时分配 `expected_revision + 1`。
    而带修订号的正文对象（`Case` / `AcceptanceScope`）会把自己的 `revision`
    一起写进 payload——两者一旦分叉，就会出现检查文档记录的现象：
    **记录 `@1` 里躺着正文 `@9`**，按 `@1` 读回得到 `@9`、按 `@9` 又读不到东西。

    因此这里要求 `正文修订 == (expected_revision or 0) + 1`，否则报
    `ConcurrentEditError` 并带上"正文写的修订"与"将要分配的修订"两个数——
    调用方据此要么按顺序递增，要么先读回当前修订再重试。
    """
    declared = payload.get(key)
    if not isinstance(declared, int) or isinstance(declared, bool):
        raise ValueError(f"{key} must be an integer in the {aggregate_kind} payload")
    assigned = (expected_revision or 0) + 1
    if declared != assigned:
        raise ConcurrentEditError(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            expected_revision=expected_revision,
            current_revision=assigned,
        )


def _load_payload(
    reader: RecordReader,
    *,
    project_id: str,
    aggregate_kind: AggregateKind,
    record_id: str,
    revision: int,
) -> dict[str, object]:
    record = reader.read(
        aggregate_kind=aggregate_kind, record_id=record_id, revision=revision
    )
    payload = dict(record.payload)
    # 项目校验：跨项目的记录不得被当成同一条读到。
    stored_project = payload.get("project_id")
    if stored_project is not None and stored_project != project_id:
        raise ValueError(f"{aggregate_kind} {record_id} belongs to another project")
    return payload


# ------------------------------------------------------------------ 用例


def save_case(
    case: Case,
    *,
    project_id: str,
    unit_of_work: UnitOfWork,
    expected_revision: int | None = None,
) -> StagedRevision:
    """保存一条用例的某个修订。

    `case.revision` 必须等于**这次会分配的仓储修订**（`expected_revision + 1`，新建时为 1）：
    正文里的修订号与记录修订分叉就会出现"记录 `@1`、正文 `@9`"（检查项 B-11）。
    """
    return _stage_and_commit(
        project_id=project_id,
        aggregate_kind=CASE_AGGREGATE,
        record_id=case.case_id,
        expected_revision=expected_revision,
        payload=case_to_payload(case, project_id=project_id),
        unit_of_work=unit_of_work,
        revision_carrying_key="revision",
    )


def load_case(
    reader: RecordReader, *, project_id: str, case_id: str, revision: int
) -> Case:
    """按**准确修订**读回用例；没有"读最新"入口。"""
    payload = _load_payload(
        reader,
        project_id=project_id,
        aggregate_kind=CASE_AGGREGATE,
        record_id=case_id,
        revision=revision,
    )
    return case_from_payload(payload)


# ------------------------------------------------------------------ 验收范围


def save_acceptance_scope(
    scope: AcceptanceScope,
    *,
    project_id: str,
    unit_of_work: UnitOfWork,
    expected_revision: int | None = None,
) -> StagedRevision:
    """保存一个验收范围的某个修订。

    与 `save_case()` 同一不变量：`scope.revision` 必须等于这次会分配的仓储修订
    （检查项 B-11 的"Scope 同类路径"）。
    """
    return _stage_and_commit(
        project_id=project_id,
        aggregate_kind=ACCEPTANCE_SCOPE_AGGREGATE,
        record_id=scope.scope_id,
        expected_revision=expected_revision,
        payload=acceptance_scope_to_payload(scope, project_id=project_id),
        unit_of_work=unit_of_work,
        revision_carrying_key="revision",
    )


def load_acceptance_scope(
    reader: RecordReader, *, project_id: str, scope_id: str, revision: int
) -> AcceptanceScope:
    payload = _load_payload(
        reader,
        project_id=project_id,
        aggregate_kind=ACCEPTANCE_SCOPE_AGGREGATE,
        record_id=scope_id,
        revision=revision,
    )
    return acceptance_scope_from_payload(payload)


def save_rule_draft(
    draft: RuleDraft,
    *,
    project_id: str,
    unit_of_work: UnitOfWork,
    expected_revision: int | None = None,
) -> StagedRevision:
    """保存一份规则**草稿**的某个修订。

    落盘形状由 `portable.rule_draft_to_payload()` 决定：它与导入导出的可携带形状
    **同一套**，因此"导入的规则"与"本地编辑的规则"在记录里长得一样，不会出现两套形状。
    草稿的确认与启用状态**不落盘**——它们是动作产生的事实，草稿记录只承载内容
    （发布才产生 `rule_version`）。
    """
    return _stage_and_commit(
        project_id=project_id,
        aggregate_kind=RULE_DRAFT_AGGREGATE,
        record_id=draft.rule_id,
        expected_revision=expected_revision,
        payload=rule_draft_to_payload(draft, project_id=project_id),
        unit_of_work=unit_of_work,
    )


# ------------------------------------------------------------------ 依据确认


def save_confirmation(
    confirmation: ConfirmationRecord,
    *,
    project_id: str,
    unit_of_work: UnitOfWork,
) -> StagedRevision:
    """登记一条依据确认；**只追加，不接受覆盖**。

    确认绑定准确的 `(case_id, basis_revision, basis_text_digest)`；
    需要更正时登记新确认，旧确认仍按原样读得到——因此这里没有
    `expected_revision` 参数：给它一个"我看到的修订"就等于允许覆盖历史确认。
    """
    return _stage_and_commit(
        project_id=project_id,
        aggregate_kind=CONFIRMATION_AGGREGATE,
        record_id=confirmation.confirmation_id,
        expected_revision=None,
        payload=confirmation_to_payload(confirmation, project_id=project_id),
        unit_of_work=unit_of_work,
    )


def load_confirmation(
    reader: RecordReader, *, project_id: str, confirmation_id: str
) -> ConfirmationRecord:
    """读回一条确认（恒为修订 1：确认只追加，不改写）。"""
    payload = _load_payload(
        reader,
        project_id=project_id,
        aggregate_kind=CONFIRMATION_AGGREGATE,
        record_id=confirmation_id,
        revision=1,
    )
    return confirmation_from_payload(payload)


__all__ = [
    "ACCEPTANCE_SCOPE_AGGREGATE",
    "CASE_AGGREGATE",
    "CONFIRMATION_AGGREGATE",
    "RULE_DRAFT_AGGREGATE",
    "load_acceptance_scope",
    "load_case",
    "load_confirmation",
    "save_acceptance_scope",
    "save_case",
    "save_confirmation",
    "save_rule_draft",
]
