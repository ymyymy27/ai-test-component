"""B 侧薄底座协议：提交、按修订读、幂等查询。

**这不是架构文档中 A 的 `application/ports.py`。** 该文件是 A 唯一所有，B 至今未修改，
所需签名以 `docs/接口对接/进行中/AB-001-端口与保存/contract.md` 第 8 节提出，
A 的评审意见见同目录 `review-A.md`。

本模块是 B 侧的**临时窄底座**：在 A 的端口签名落地前，让 `prepare_run` 等编排能够
真实执行并验证。A 的签名一旦可用，由 `substrate_adapter.py` 写一层薄转接头对接，
**用例代码与测试不需要改动**。

设计依据：一期架构文档《01-项目与计划》第 8 节末、第 11 节；实施方案第 3 节。
逐条不变量见 `docs/文档-feix-a/B包/11-薄底座与prepare_run编排设计说明.md` 第 3.4 节。

四条容易违背的约定，集中在此说明：

1. **所有读操作必须显式给修订号**，本协议**不提供"读最新"的方法**。
2. **业务顺序按提交序号**（`commit_seq`），不使用系统时间。
3. **记录不可变**：同一 `(aggregate_kind, record_id, revision)` 不允许被改写。
4. **事务必须有作用域**：`open()` 与 `commit()` / `rollback()` 之间的收尾靠的是调用方的记性，
   而真实底座的 `open()` 会取**工作空间级排他写锁**——调用方一旦在 `open()` 之后抛异常或
   提前返回，锁会一直留在这个进程里（`portalocker` 非阻塞排他锁，同进程再取也会失败）。
   因此端口层提供**事务上下文** `transaction(unit_of_work, project_id)`，由返回对象保证收尾
   （见 `Transaction`）。B 的应用用例统一经它写入，**不得直接调 `open()`**，
   由 `tests/unit/test_substrate_adapter.py` 的守卫测试守住。
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass
from typing import Literal, Protocol

from aitest.application.planning.preparation import PreparationRecord
from aitest.contracts.queries import QueryUnsupportedFilter

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
    "source_pin_intent",
    "source_binding_current",
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
            f"the injected substrate does not provide {capability!r}; owner={owner}; see {request}"
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
        if self.record_id is not None and self.aggregate_kind is None:
            raise QueryUnsupportedFilter("record identity requires aggregate_kind")
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

    **`open()` 是底座面，不是用例面。** 它只负责"开启"，收尾仍要调用方自己
    `commit()` / `rollback()`；真实实现（如 A 的 `FileUnitOfWork`）的 `begin()` 会取
    工作空间级排他写锁，忘写收尾就等于把锁留在这个进程里。因此：

    - **应用用例统一用 `transaction(unit_of_work, project_id)`**（本模块的事务上下文），
      由它保证收尾；`open()` / `commit()` / `rollback()` 保留给实现与既有测试，
      语义不变（新增一个方法会破坏既有实现的结构性兼容）。
    - 底座若自带更强的收尾能力（例如知道怎么释放排他锁），在 `rollback()` 里实现即可，
      事务上下文只依赖 `open()` / `commit()` / `rollback()` 三个已声明能力。
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


# ------------------------------------------------------------------ 事务上下文


class Transaction(AbstractContextManager["Transaction"]):
    """端口层**事务上下文**：排他写锁的持有与收尾都由本对象负责。

    要解决的问题（`09-待解决问题清单.md` 的 B-Q10）：`UnitOfWork` 只有
    `open()` / `commit()` / `rollback()`，**没有作用域语义**。真实底座的 `open()`
    会取工作空间级排他写锁，调用方在 `open()` 之后抛异常或提前返回，
    锁就留在这个进程里（`portalocker` 非阻塞排他锁，同进程再取也会失败），
    该工作空间此后再也开不了事务。本对象把"收尾"从**调用方的记性**换成**作用域语义**：

    | 情形 | 结果 |
    | --- | --- |
    | 只创建、不进入（忘写 `with`） | **没有取过锁**，不需要收尾 |
    | `with` 体内正常结束但没 `commit()` | 回滚并释放锁（宁可不写，不得半写） |
    | `with` 体内抛异常 / 提前 `return` | `__exit__` 回滚并释放锁 |
    | `open()` 自己失败 | 本对象**没有**开启过事务，绝不回滚别人的事务 |
    | 提交/回滚本身失败 | 仍尝试回滚（失败不掩盖体内原始异常） |
    | 手工进入后把对象丢掉 | `__del__` 兜底回滚（依赖解释器回收时机，**不作为正确性依据**） |

    用法（B 的用例统一写法）：

    ```python
    with transaction(unit_of_work, project_id) as tx:
        tx.stage_record(...)
        result = tx.commit()
    ```

    **不自动提交**：退出时若未 `commit()` 一律回滚。半截提交比不写更危险，
    "提交时刻"必须由用例显式给出。
    """

    __slots__ = ("_owns_transaction", "_project_id", "_unit_of_work")

    def __init__(self, unit_of_work: UnitOfWork, project_id: str) -> None:
        if not project_id.strip():
            raise ValueError("project_id must not be empty")
        self._unit_of_work = unit_of_work
        self._project_id = project_id
        #: 只有**确实开启成功**才为真；未开启时不持有任何资源，也不得回滚别人的事务。
        self._owns_transaction = False

    # ---------------------------------------------------------- 作用域

    def __enter__(self) -> Transaction:
        # 先 open，成功后才认领；`open()` 抛错（例如排他锁取不到）时本对象不认领，
        # 因此 `__exit__` / `__del__` 不会去回滚一个不属于自己的事务。
        self._unit_of_work.open(self._project_id)
        self._owns_transaction = True
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.settle()

    def __del__(self) -> None:
        # GC 兜底：手工 `__enter__()` 后把对象丢掉时也要放锁。**不抛异常**，
        # 也不把回收时机当成正确性依据（正确性由 `with` 保证）。
        with suppress(Exception):
            self.settle()

    def settle(self) -> None:
        """幂等收尾：没提交就回滚，把排他写锁还回去。

        可显式调用；`__exit__` 与 `__del__` 都走这里。回滚失败**不向上抛**：
        收尾路径不得掩盖体内触发的原始失败（与转接头的放弃路径同口径）。
        """
        if not self._owns_transaction:
            return
        self._owns_transaction = False
        with suppress(Exception):
            self._unit_of_work.rollback()

    # ---------------------------------------------------------- 事务面

    def commit_seq(self) -> str:
        return self._unit_of_work.commit_seq()

    def next_commit_seq(self) -> str:
        return self._unit_of_work.next_commit_seq()

    def stage_record(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> StagedRevision:
        return self._unit_of_work.stage_record(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def stage_preparation(self, *, record: PreparationRecord) -> StagedRevision:
        return self._unit_of_work.stage_preparation(record=record)

    def commit(self) -> CommitResult:
        """提交；**抛错时不算收尾**，由 `settle()` 兜底回滚（不得留着锁）。"""
        result = self._unit_of_work.commit()
        self._owns_transaction = False
        return result

    def rollback(self) -> None:
        self._unit_of_work.rollback()
        self._owns_transaction = False


def transaction(unit_of_work: UnitOfWork, project_id: str) -> Transaction:
    """开启一个**事务作用域**；B 的应用用例统一经它写入，不直接调 `open()`。

    返回的是**对象**而不是上下文装饰器：锁由该对象持有，因此可以用它暂存与提交，
    它的生命周期就是事务的生命周期（见 `Transaction`）。
    """
    return Transaction(unit_of_work, project_id)


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


_RECORD_BODY_IDENTITIES: Mapping[str, str] = {
    "project": "local_project_id",
    "binding": "binding_id",
    "environment": "environment_id",
    "source_snapshot": "snapshot_id",
    "task": "task_id",
    "delivery": "delivery_id",
    "case": "case_id",
    "acceptance_scope": "scope_id",
    "rule_draft": "rule_id",
}


def require_scoped_record(
    record: CommittedRecord,
    *,
    project_id: str,
    aggregate_kind: AggregateKind,
    record_id: str,
    revision: int,
) -> None:
    """Prove warehouse identity and owning project before consuming a saved body."""
    if type(revision) is not int or revision < 1:
        raise ValueError("record requires an exact positive warehouse revision")
    if (
        getattr(record, "aggregate_kind", None),
        getattr(record, "record_id", None),
        getattr(record, "revision", None),
    ) != (
        aggregate_kind,
        record_id,
        revision,
    ) or type(record.revision) is not int:
        raise ValueError("record reader returned another warehouse identity")
    if not isinstance(record.payload, Mapping):
        raise ValueError("saved record payload is not an object")
    owner = record.payload.get("project_id")
    if owner is None and aggregate_kind == "project":
        owner = record.payload.get("local_project_id")
    if owner is None:
        raise ValueError("saved record carries no project_id; owning project is unknown")
    if owner != project_id:
        raise ValueError("record belongs to another project")
    body_key = _RECORD_BODY_IDENTITIES.get(aggregate_kind)
    if body_key is not None and record.payload.get(body_key) != record_id:
        raise ValueError("saved body identity differs from its warehouse record")


def read_scoped_record(
    reader: RecordReader,
    *,
    project_id: str,
    aggregate_kind: AggregateKind,
    record_id: str,
    revision: int,
) -> CommittedRecord:
    if any(
        not isinstance(value, str) or not value.strip()
        for value in (project_id, aggregate_kind, record_id)
    ):
        raise ValueError("record lookup requires nonempty project, kind and identity")
    if type(revision) is not int or revision < 1:
        raise ValueError("record requires an exact positive warehouse revision")
    record = reader.read(aggregate_kind=aggregate_kind, record_id=record_id, revision=revision)
    require_scoped_record(
        record,
        project_id=project_id,
        aggregate_kind=aggregate_kind,
        record_id=record_id,
        revision=revision,
    )
    return record


def current_record(
    reader: RecordReader, *, project_id: str, aggregate_kind: AggregateKind, record_id: str
) -> CommittedRecord | None:
    """定位权威当前修订后准确读回；旧端口逐页查询，不能取首分页冒充最新。"""
    current = getattr(reader, "current_revision", None)
    if callable(current):
        revision = current(aggregate_kind=aggregate_kind, record_id=record_id)
        if type(revision) is not int or revision < 0:
            raise ValueError("current warehouse revision cannot be verified")
        if revision == 0:
            return None
        return read_scoped_record(
            reader,
            project_id=project_id,
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            revision=revision,
        )
    latest: CommittedRecord | None = None
    cursor: str | None = None
    seen: set[str] = set()
    while True:
        page = reader.query(
            RecordQuery(
                project_id=project_id,
                aggregate_kind=aggregate_kind,
                record_id=record_id,
                cursor=cursor,
            )
        )
        for item in page.items:
            require_scoped_record(
                item,
                project_id=project_id,
                aggregate_kind=aggregate_kind,
                record_id=record_id,
                revision=item.revision,
            )
            if latest is not None and item.revision == latest.revision and item != latest:
                raise ValueError("query returned conflicting bodies for the same revision")
            if latest is None or item.revision > latest.revision:
                latest = item
        cursor = page.next_cursor
        if cursor is None:
            return latest
        if cursor in seen:
            raise InvalidQueryCursor("query repeated a cursor while locating a revision")
        seen.add(cursor)


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
    "Transaction",
    "UnitOfWork",
    "transaction",
    "current_record",
    "read_scoped_record",
    "require_scoped_record",
]
