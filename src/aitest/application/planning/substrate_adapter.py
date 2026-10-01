"""薄转接头：把 A 的正式工作单元/仓储接到 B 的窄底座协议。

B 的用例（`prepare_run` / `publish_*` / `model_orchestration`）只依赖
`substrate.UnitOfWork` 与 `substrate.RecordReader` 这两个窄协议。
本模块把它们翻译到 A 的真实实现上（`infrastructure/file_store/` 的
`FileUnitOfWork` / `FileRecordRepository`），**用例代码与用例测试不改**。

## 为什么用注入而不是 import

`tests/architecture/test_boundaries.py` 硬断言 `application/**` 不得 import
`aitest.infrastructure`。因此本模块**不 import 任何基础设施**，底层对象一律由
装配点（`bootstrap.py`）注入，类型上用本模块声明的窄协议约束。

## B 声明的底层最小能力面

| 能力 | A 现状 | 说明 |
| --- | --- | --- |
| `begin(request_id, project_id)` | 已具备 | 取**排他写锁**并开启事务；只用 `open()` 不取锁 |
| `stage_record(...)` | 已具备 | 返回新修订号（整数） |
| `commit(request_id)` / `rollback(request_id)` | 已具备 | 返回 `created` 与 `commit_sequence` |
| `read(...)` / `query(...)` | 已具备 | 读原始 payload；查询按索引，缺索引须显式维护 |
| `current_revision(...)` | **有实现，未进 `ports.py`** | 身份查询与冲突提示要用 |
| `commit_seq()` / `next_commit_seq()` | **尚无** | 业务顺序按提交序号判断 |

后三项已写入 `docs/接口对接/进行中/AB-001-端口与保存/contract.md` 第 8.8 节，
由 A 冻结签名；本模块**不猜测、不自造**，缺哪个就在调用点明确报出
（`SubstrateContractError`），不用默认值顶替。

## 三处翻译职责

1. **修订语义**：B 的 `expected_revision: int | None`（`None` = 新建）
   ↔ A 的 `expected_revision`（`None` 与 `0` 同义）。A 冲突时只抛
   `ValueError("revision conflict")`，**不带当前修订**；B 的
   `ConcurrentEditError` 要求携带当前修订与差异提示，因此本模块**先比对再暂存**。
2. **错误类型**：A 的查询结果里 `maintenance_required` / `invalid_cursor` 是**状态值**，
   不是异常。B 侧必须抛 `IndexMaintenanceRequired` / `InvalidQueryCursor`——
   把"索引缺失"当成空页会让调用方把"查不到"读成"没有"。
3. **身份与 payload**：准备记录的记录标识、意图标识与落盘形状**由 B 的身份合同唯一决定**
   （`preparation.py` 的 `preparation_record_id` / `preparation_intent_id` /
   `preparation_record_payload`）。A 的修订计数按 `record_id` 全局计，**不含项目维度**，
   所以记录标识必须自带项目与客户端命名空间。

## 事务收尾（B-Q10）

A 的 `FileUnitOfWork.begin()` 会取**工作空间级排他写锁**，只有 `commit()` / `rollback()`
释放；`portalocker` 是非阻塞排他锁，同进程再取也会失败。本模块的收尾分三层：

1. **端口层事务上下文**（`substrate.transaction`）：B 的用例统一
   `with transaction(uow, project_id) as tx:` 写入。锁只有在**进入作用域**时才取，
   `with` 体抛异常 / 提前 `return` / 没提交都由上下文对象回滚并放锁。
   B 的用例**不得**直接调 `open()`（`tests/unit/test_substrate_adapter.py` 有守卫测试）。
2. **失败路径自动回滚**：本转接头在 `stage_record` / `stage_preparation` / `commit`
   抛错时调用 `_abandon()`，先重置事务再回滚，放弃路径不掩盖原始失败。
3. **回收兜底**：转接头对象被回收（`weakref.finalize`，进程退出时也会触发）而事务仍开着时，
   由 `_release_on_collection()` 补一次回滚放锁。这是**最后一道网**，不是正确性依据。

    还有一条**不在 B 可修范围**内的路径：绕过窄底座、直接调 A 的 `FileUnitOfWork.open()`
    而后既不提交也不回滚。那属于 A 的实现面，B 只能在自己的端口与用例上闭合。
"""

from __future__ import annotations

import weakref
from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import Protocol, cast
from uuid import uuid4

from aitest.application.planning.preparation import (
    PREPARATION_AGGREGATE_KIND,
    PreparationRecord,
    preparation_record_from_payload,
    preparation_record_id,
    preparation_record_payload,
    record_id_for_intent_id,
)
from aitest.application.planning.substrate import (
    AggregateKind,
    CommitResult,
    CommittedRecord,
    ConcurrentEditError,
    IndexMaintenanceRequired,
    InvalidQueryCursor,
    PreparationConflictError,
    RecordPage,
    RecordQuery,
    StagedRevision,
    SubstrateContractError,
)

#: AB-001 中记录本模块所需端口签名的小节。
_PORT_REQUEST = "docs/接口对接/进行中/AB-001-端口与保存/contract.md 第 8.8 节"
_PORT_OWNER = "A"

#: 单页最多读多少行；与 `contracts.queries.QuerySpec.limit` 的上限一致。
_PAGE_LIMIT = 500


# ------------------------------------------------------------------ 底层能力面


class WorkspacePorts(Protocol):
    """装配点注入的**事务侧**最小能力面。"""

    def begin(self, request_id: str, project_id: str) -> object: ...

    def stage_record(
        self,
        *,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> int: ...

    def commit(self, request_id: str | None = None) -> Mapping[str, object]: ...

    def rollback(self, request_id: str | None = None) -> Mapping[str, object]: ...


class RecordRepositoryPorts(Protocol):
    """装配点注入的**只读侧**最小能力面。"""

    def read(
        self, *, aggregate_kind: str, record_id: str, revision: int
    ) -> CommittedRecord: ...

    def query(self, query: RecordQuery) -> object: ...

    def current_revision(self, aggregate_kind: str, record_id: str) -> int: ...


class CommitSequenceSource(Protocol):
    """当前提交序号的只读来源（A 尚未冻结进 `ports.py`，见模块 docstring）。"""

    def current_commit_sequence(self) -> int: ...


def _require(ports: object, capability: str) -> None:
    if not callable(getattr(ports, capability, None)):
        raise SubstrateContractError(capability, owner=_PORT_OWNER, request=_PORT_REQUEST)


def _release_on_collection(ports: WorkspacePorts, live: dict[str, str]) -> None:
    """转接头对象被回收时的兜底：事务还开着就补一次回滚，把排他写锁还回去。

    参数里**不能出现被回收的对象本身**（`weakref.finalize` 强引用回调参数，
    引用 `self` 会让对象永远不被回收），因此这里只拿底层端口与一个可变字典。
    回调在 GC 或进程退出时执行，**不抛异常**。
    """
    request_id = live.pop("request_id", None)
    if request_id is None:
        return
    with suppress(Exception):
        ports.rollback(request_id=request_id)


# ------------------------------------------------------------------ 身份查询


def _latest_committed(
    repository: RecordRepositoryPorts, *, aggregate_kind: str, record_id: str
) -> CommittedRecord | None:
    """按记录标识取**当前修订**的已提交记录；不存在返回 `None`。

    走 A 的 `current_revision` + 显式修订 `read`，**不经索引**：
    新工作空间还没有索引文件，经索引会把"第一条记录"误报成"索引缺失"。
    """
    _require(repository, "current_revision")
    current = int(repository.current_revision(aggregate_kind, record_id))
    if current < 1:
        return None
    return repository.read(
        aggregate_kind=aggregate_kind, record_id=record_id, revision=current
    )


def _preparation_entry(
    repository: RecordRepositoryPorts, *, intent_id: str
) -> PreparationRecord | None:
    """按业务意图标识读回准备记录。

    身份键 `(project_id, client_id, prepare_request_id)` 与意图标识**同域**，
    因此可以从意图标识反推记录标识——不需要按任意 payload 字段建索引，
    也不做全表扫描。
    """
    committed = _latest_committed(
        repository,
        aggregate_kind=PREPARATION_AGGREGATE_KIND,
        record_id=record_id_for_intent_id(intent_id),
    )
    if committed is None:
        return None
    return preparation_record_from_payload(committed.payload)


# ------------------------------------------------------------------ 只读侧


class PortsRecordReader:
    """把 A 的 `RecordRepository` 适配成本地 `RecordReader`。"""

    def __init__(self, repository: RecordRepositoryPorts) -> None:
        self._repository = repository

    def read(
        self, *, aggregate_kind: AggregateKind, record_id: str, revision: int
    ) -> CommittedRecord:
        return self._repository.read(
            aggregate_kind=aggregate_kind, record_id=record_id, revision=revision
        )

    def query(self, query: RecordQuery) -> RecordPage:
        _require(self._repository, "query")
        result = self._repository.query(
            RecordQuery(
                project_id=query.project_id,
                aggregate_kind=query.aggregate_kind,
                record_id=query.record_id,
                limit=min(query.limit, _PAGE_LIMIT),
                cursor=query.cursor,
            )
        )
        status = getattr(result, "status", "ok")
        if status == "maintenance_required":
            raise IndexMaintenanceRequired(
                "the query index is missing or stale; rebuild it before querying"
            )
        if status == "invalid_cursor":
            raise InvalidQueryCursor(str(query.cursor))
        return RecordPage(
            items=tuple(getattr(result, "items", ())),
            next_cursor=getattr(result, "next_cursor", None),
        )

    def find_preparation(
        self,
        *,
        project_id: str,
        client_id: str,
        prepare_request_id: str,
    ) -> PreparationRecord | None:
        _require(self._repository, "current_revision")
        record_id = preparation_record_id(
            project_id=project_id,
            client_id=client_id,
            prepare_request_id=prepare_request_id,
        )
        committed = _latest_committed(
            self._repository,
            aggregate_kind=PREPARATION_AGGREGATE_KIND,
            record_id=record_id,
        )
        if committed is None:
            return None
        return preparation_record_from_payload(committed.payload)

    def find_preparation_by_intent(self, *, intent_id: str) -> PreparationRecord | None:
        _require(self._repository, "current_revision")
        return _preparation_entry(self._repository, intent_id=intent_id)


# ------------------------------------------------------------------ 事务侧


class PortsUnitOfWork:
    """把 A 的 `WorkspaceUnitOfWork` 适配成本地 `UnitOfWork`。

    与只读侧**共用同一份身份规则**：准备记录的记录标识与 payload 形状都取自
    `preparation.py`，因此转接头不会引入第二套口径。

    **用例侧请用 `substrate.transaction(uow, project_id)`**（事务上下文），
    不要直接调 `open()`：`open()` 只开启事务，收尾要靠调用方，而底层 `begin()`
    握着工作空间级排他写锁。`open()` / `commit()` / `rollback()` 与 `with uow:`
    为兼容保留，语义不变。
    """

    def __init__(
        self,
        ports: WorkspacePorts,
        *,
        repository: RecordRepositoryPorts,
        sequence: CommitSequenceSource | None = None,
    ) -> None:
        self._ports = ports
        self._repository = repository
        self._sequence = sequence
        self._request_id: str | None = None
        self._project_id: str | None = None
        self._pending = 0
        #: 当前事务的请求号，供 GC 兜底回调读取。**回调不能引用 `self`**
        #: （见 `_release_on_collection`），所以请求号另放一份在可变字典里。
        self._live: dict[str, str] = {}
        self._finalizer = weakref.finalize(
            self, _release_on_collection, ports, self._live
        )

    # ---------------------------------------------------------- 内部

    def _current_commit_sequence(self) -> int:
        if self._sequence is None or not callable(
            getattr(self._sequence, "current_commit_sequence", None)
        ):
            raise SubstrateContractError(
                "commit_seq/next_commit_seq", owner=_PORT_OWNER, request=_PORT_REQUEST
            )
        return int(self._sequence.current_commit_sequence())

    def _require_open(self) -> str:
        if self._project_id is None or self._request_id is None:
            raise RuntimeError("no open transaction")
        return self._project_id

    def _reset(self) -> None:
        self._request_id = None
        self._project_id = None
        self._pending = 0
        self._live.pop("request_id", None)

    def _abandon(self) -> None:
        """放弃事务并**释放排他写锁**；只用于失败路径。"""
        request_id = self._request_id
        self._reset()
        if request_id is None:
            return
        with suppress(Exception):
            # 放弃路径不得掩盖触发放弃的原始失败。
            self._ports.rollback(request_id=request_id)

    # ---------------------------------------------------------- 协议

    def open(self, project_id: str) -> None:
        if self._project_id is not None:
            raise RuntimeError("transaction already open")
        if not project_id.strip():
            raise ValueError("project_id must not be empty")
        _require(self._ports, "begin")
        request_id = f"uow-{uuid4().hex}"
        self._ports.begin(request_id, project_id)
        self._request_id = request_id
        self._project_id = project_id
        self._pending = 0
        self._live["request_id"] = request_id

    def commit_seq(self) -> str:
        """当前提交序号；**未开事务也可读**（阻塞与复用分支要用它）。"""
        return str(self._current_commit_sequence())

    def next_commit_seq(self) -> str:
        """本次提交完成后会得到的提交序号。

        A 的提交序号**按记录递增**，因此已暂存 N 条记录的这次提交落在
        `当前序号 + N`；下一条要暂存的记录拿到 `当前序号 + N + 1`。
        """
        return str(self._current_commit_sequence() + self._pending + 1)

    def stage_record(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> StagedRevision:
        self._require_open()
        try:
            _require(self._ports, "stage_record")
            _require(self._repository, "current_revision")
            return self._stage(
                aggregate_kind=aggregate_kind,
                record_id=record_id,
                expected_revision=expected_revision,
                payload=payload,
            )
        except BaseException:
            self._abandon()
            raise

    def _stage(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> StagedRevision:
        if not record_id.strip():
            raise ValueError("record_id must not be empty")

        current = int(self._repository.current_revision(aggregate_kind, record_id))
        if current != (expected_revision or 0):
            raise ConcurrentEditError(
                aggregate_kind=aggregate_kind,
                record_id=record_id,
                expected_revision=expected_revision,
                current_revision=current,
            )

        revision = self._ports.stage_record(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            expected_revision=expected_revision,
            payload=dict(payload),
        )
        self._pending += 1
        return StagedRevision(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            revision=int(revision),
        )

    def stage_preparation(self, *, record: PreparationRecord) -> StagedRevision:
        """登记准备记录；同键同摘要复用，同键异摘要冲突（**不覆盖**）。

        落盘 payload **由身份合同唯一决定**，因此不接受调用方另给一份。
        """
        project_id = self._require_open()
        if record.request.project_id != project_id:
            raise ValueError(
                "preparation record belongs to another project than the open transaction"
            )
        try:
            return self._stage_preparation(record)
        except BaseException:
            self._abandon()
            raise

    def _stage_preparation(self, record: PreparationRecord) -> StagedRevision:
        _require(self._repository, "current_revision")
        record_id = preparation_record_id(
            project_id=record.request.project_id,
            client_id=record.request.client_id,
            prepare_request_id=record.request.prepare_request_id,
        )
        committed = _latest_committed(
            self._repository,
            aggregate_kind=PREPARATION_AGGREGATE_KIND,
            record_id=record_id,
        )
        if committed is not None:
            existing = preparation_record_from_payload(committed.payload)
            if existing.request.payload_hash != record.request.payload_hash:
                raise PreparationConflictError(
                    project_id=record.request.project_id,
                    client_id=record.request.client_id,
                    prepare_request_id=record.request.prepare_request_id,
                    existing_intent_id=existing.intent_id,
                    existing_payload_hash=existing.request.payload_hash,
                    existing_created_at_commit=existing.created_at_commit,
                )
            # 同键同摘要：复用原记录，不新建第二条。
            return StagedRevision(
                aggregate_kind=PREPARATION_AGGREGATE_KIND,
                record_id=record_id,
                revision=committed.revision,
            )
        return self._stage(
            aggregate_kind=PREPARATION_AGGREGATE_KIND,
            record_id=record_id,
            expected_revision=None,
            payload=preparation_record_payload(record),
        )

    def commit(self) -> CommitResult:
        self._require_open()
        if self._pending < 1:
            raise ValueError("nothing staged in this transaction")
        _require(self._ports, "commit")
        request_id = self._request_id
        try:
            result = self._ports.commit(request_id=request_id)
        except BaseException:
            self._abandon()
            raise
        created = tuple(
            StagedRevision(aggregate_kind=kind, record_id=record_id, revision=revision)
            for kind, record_id, revision in _created_rows(result)
        )
        commit_sequence = result.get("commit_sequence")
        if commit_sequence is None:
            self._abandon()
            raise SubstrateContractError(
                "commit().commit_sequence", owner=_PORT_OWNER, request=_PORT_REQUEST
            )
        self._reset()
        return CommitResult(commit_seq=str(commit_sequence), created=created)

    def rollback(self) -> None:
        self._require_open()
        _require(self._ports, "rollback")
        request_id = self._request_id
        self._reset()
        self._ports.rollback(request_id=request_id)

    # ---------------------------------------------------------- 作用域收尾

    def __enter__(self) -> PortsUnitOfWork:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """离开作用域时收尾：没提交就回滚，避免一直握着排他写锁。

        **兼容保留**：`with uow:` 要求调用方先自己 `open()`，锁在 `__enter__` 之前
        就已经取到了，因此它不能替代 `substrate.transaction()`。用例请用后者。
        """
        self._abandon()


def _created_rows(
    result: Mapping[str, object],
) -> tuple[tuple[AggregateKind, str, int], ...]:
    """把 A 的 `commit()["created"]` 归一成 `(类别, 记录标识, 修订号)`。

    形状不符一律报明确错误，**不猜**：读错的修订号会让后续暂存以错误的
    `expected_revision` 覆盖记录。
    """
    raw = result.get("created")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise SubstrateContractError(
            "commit().created", owner=_PORT_OWNER, request=_PORT_REQUEST
        )
    rows: list[tuple[AggregateKind, str, int]] = []
    for entry in raw:
        if (
            not isinstance(entry, Sequence)
            or isinstance(entry, (str, bytes))
            or len(entry) != 3
        ):
            raise SubstrateContractError(
                "commit().created entries", owner=_PORT_OWNER, request=_PORT_REQUEST
            )
        kind, record_id, revision = entry
        if (
            not isinstance(kind, str)
            or not isinstance(record_id, str)
            or not isinstance(revision, int)
            or isinstance(revision, bool)
        ):
            raise SubstrateContractError(
                "commit().created entries", owner=_PORT_OWNER, request=_PORT_REQUEST
            )
        rows.append((cast(AggregateKind, kind), record_id, revision))
    return tuple(rows)


__all__ = [
    "CommitSequenceSource",
    "PortsRecordReader",
    "PortsUnitOfWork",
    "RecordRepositoryPorts",
    "WorkspacePorts",
]
