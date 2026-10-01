"""B 侧薄底座协议：提交、按修订读、幂等查询。

**这不是架构文档中 A 的 `application/ports.py`。** 该文件是 A 唯一所有，B 至今未修改，
所需签名以 `docs/接口对接/进行中/AB-001-端口与保存/contract.md` 第 8 节提出，
A 的评审意见见同目录 `review-A.md`。

本模块是 B 侧的**临时窄底座**：在 A 的端口签名落地前，让 `prepare_run` 等编排能够
真实执行并验证。A 的签名一旦可用，由 `substrate_adapter.py` 写一层薄转接头对接，
**用例代码与测试不需要改动**。

设计依据：一期架构文档《01-项目与计划》第 8 节末、第 11 节；实施方案第 3 节。
逐条不变量见 `docs/文档-feix-a/B包/11-薄底座与prepare_run编排设计说明.md` 第 3.4 节。

三条容易违背的约定，集中在此说明：

1. **所有读操作必须显式给修订号**，本协议**不提供"读最新"的方法**。
2. **业务顺序按提交序号**（`commit_seq`），不使用系统时间。
3. **记录不可变**：同一 `(aggregate_kind, record_id, revision)` 不允许被改写。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from aitest.application.planning.preparation import PreparationRecord

#: 参与提交与查询的记录类别。新增时同步更新文档与内存实现。
AggregateKind = Literal[
    "project",
    "binding",
    "module",
    "dependency_set",
    "task",
    "delivery",
    "acceptance_item",
    "environment",
    "source_snapshot",
    "template_ref",
    "generated_content",
    "rule_draft",
    "rule_version",
    "case",
    "case_link",
    "plan",
    "acceptance_scope",
    "preparation_record",
    "model_outbound_policy",
    "model_outbound_request",
    "prepared_run",
]


class ConcurrentEditError(RuntimeError):
    """陈旧修订：`expected_revision` 与当前修订不符。

    携带**当前修订**与差异提示，供调用方提示用户；**不自动覆盖用户编辑**
    （架构文档第 8 节末）。本类是所有底座冲突错误的基类。
    """

    def __init__(
        self,
        *,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        current_revision: int,
    ) -> None:
        self.aggregate_kind = aggregate_kind
        self.record_id = record_id
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            f"{aggregate_kind} {record_id}: expected revision "
            f"{expected_revision!r} but current is {current_revision}"
        )


class SubstrateContractError(RuntimeError):
    """注入的底座缺少 B 已声明的能力。

    薄底座要的是**能力面**，不是某个具体类。注入的对象若缺方法，
    必须在调用点明确报出缺哪一个，**不得**让 AttributeError 冒到调用方，
    也不得用猜测的实现顶替（B 包 AI 规则第 3.9 节）。
    """

    def __init__(self, capability: str, *, owner: str, request: str) -> None:
        self.capability = capability
        self.owner = owner
        self.request = request
        super().__init__(
            f"the injected substrate does not provide {capability!r}; "
            f"owner={owner}; see {request}"
        )


class IndexMaintenanceRequired(RuntimeError):
    """查询索引缺失或损坏：**必须显式维护，不得当成空结果**。

    存储与恢复合同第 13 节："索引缺失须显式维护，查询不能偷偷全扫。"
    返回空页会让调用方把"查不到"当成"没有"，因此这里抛错而不是返回空。
    """

    code = "INDEX_REBUILD_REQUIRED"


class InvalidQueryCursor(ValueError):
    """查询游标非法：**不得静默退回第一页**，否则会重复或跳过记录。"""

    code = "INVALID_CURSOR"


class PreparationConflictError(ConcurrentEditError):
    """同一准备请求键、不同输入摘要。

    架构文档第 11 节："**输入摘要不同返回冲突**"。携带原记录的 `intent_id` 与摘要，
    供调用方给出明确提示，**不覆盖原记录**。
    """

    def __init__(
        self,
        *,
        project_id: str,
        client_id: str,
        prepare_request_id: str,
        existing_intent_id: str,
        existing_payload_hash: str,
        existing_created_at_commit: str,
    ) -> None:
        self.project_id = project_id
        self.client_id = client_id
        self.prepare_request_id = prepare_request_id
        self.existing_intent_id = existing_intent_id
        self.existing_payload_hash = existing_payload_hash
        self.existing_created_at_commit = existing_created_at_commit
        super().__init__(
            aggregate_kind="preparation_record",
            record_id=prepare_request_id,
            expected_revision=None,
            current_revision=1,
        )


# ------------------------------------------------------------------ 值对象


@dataclass(frozen=True, slots=True)
class CommittedRecord:
    """已提交的不可变记录。"""

    aggregate_kind: AggregateKind
    record_id: str
    revision: int
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.record_id.strip():
            raise ValueError("record_id must not be empty")
        if self.revision < 1:
            raise ValueError("record revision must be >= 1")


@dataclass(frozen=True, slots=True)
class RecordQuery:
    """有限查询：项目范围 + 可选类别 + 可选精确标识（存储与恢复第 13 节）。"""

    project_id: str
    aggregate_kind: AggregateKind | None = None
    record_id: str | None = None
    limit: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("query requires a project_id")
        if self.limit < 1:
            raise ValueError("query limit must be >= 1")
        if self.cursor is not None and not self.cursor.strip():
            raise ValueError("query cursor must not be blank when given")


@dataclass(frozen=True, slots=True)
class RecordPage:
    """稳定分页结果；详情按引用读取，列表只读摘要。"""

    items: tuple[CommittedRecord, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class StagedRevision:
    """一次暂存产生的修订。"""

    aggregate_kind: AggregateKind
    record_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class CommitResult:
    """一次提交的结果；`commit_seq` 是业务顺序的依据。"""

    commit_seq: str
    created: tuple[StagedRevision, ...]

    def revision_of(self, aggregate_kind: AggregateKind, record_id: str) -> StagedRevision:
        for staged in self.created:
            if staged.aggregate_kind == aggregate_kind and staged.record_id == record_id:
                return staged
        raise ValueError(f"commit did not create {aggregate_kind} {record_id}")


# ------------------------------------------------------------------ 协议


class UnitOfWork(Protocol):
    """短事务：暂存记录与准备意图，一次提交。

    `open()` 必须先调用；未开事务就暂存或提交是不变量 1，实现须拒绝。
    """

    def open(self, project_id: str) -> None: ...

    def commit_seq(self) -> str: ...

    def next_commit_seq(self) -> str:
        """**本次提交后**会得到的提交序号。

        存在的理由：暂存的记录（如 `PreparationRecord.created_at_commit`）需要在
        `commit()` 之前就带上正确的序号，否则记录里的序号会比实际提交早一格。
        这是"业务顺序按提交序号判断"这条约定在**暂存阶段**的落点。
        """
        ...

    def stage_record(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> StagedRevision: ...

    def stage_preparation(self, *, record: PreparationRecord) -> StagedRevision:
        """登记准备记录，**与 `intent_id` 同一次提交**。

        准备记录的**落盘形状由身份合同唯一决定**（`preparation_record_payload()`），
        因此这里**不接受**调用方另给一份 payload——两处形状一旦分叉，
        "重启后按三元组查回原意图"就会在某个字段上悄悄失效。

        同一 `(project_id, client_id, prepare_request_id)`：
        摘要相同返回原记录的修订；摘要不同抛 `PreparationConflictError`（**不覆盖**）。
        """
        ...

    def commit(self) -> CommitResult: ...

    def rollback(self) -> None: ...


class RecordReader(Protocol):
    """只读查询；**按准确修订读取，没有"读最新"**。"""

    def read(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        revision: int,
    ) -> CommittedRecord: ...

    def query(self, query: RecordQuery) -> RecordPage: ...

    def find_preparation(
        self,
        *,
        project_id: str,
        client_id: str,
        prepare_request_id: str,
    ) -> PreparationRecord | None: ...

    def find_preparation_by_intent(self, *, intent_id: str) -> PreparationRecord | None: ...


__all__ = [
    "AggregateKind",
    "CommitResult",
    "CommittedRecord",
    "ConcurrentEditError",
    "IndexMaintenanceRequired",
    "InvalidQueryCursor",
    "PreparationConflictError",
    "RecordPage",
    "RecordQuery",
    "RecordReader",
    "StagedRevision",
    "SubstrateContractError",
    "UnitOfWork",
]
