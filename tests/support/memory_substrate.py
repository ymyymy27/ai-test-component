"""内存版薄底座：让 `prepare_run` 等编排在没有 A 的端口时也能真实执行。

**这是测试支撑，不是生产实现，也不注册为可用能力。**
真实文件存储属 A 的 `infrastructure/file_store/`，B 不碰；A 的端口签名落地后，
由 `aitest.application.planning.substrate_adapter` 写一层薄转接头对接。

结构：`MemoryStore` 持有全部状态；`MemoryUnitOfWork` / `MemoryReader` 是它的两个
**视图**，分别只暴露事务侧与只读侧，因此测试可以只给一个只读替身。

本模块实现 `substrate.py` 第 3.4 节的九条不变量，逐条对应见各方法 docstring。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

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
    PreparationConflictError,
    RecordPage,
    RecordQuery,
    StagedRevision,
)


class FixedClock:
    """可测试的时间来源；满足 A 已冻结的 `Clock` 协议。"""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return 0.0


class MemoryStore:
    """内存记录库：不可变修订、提交序号、幂等准备记录。

    `_records[kind][record_id]` 是**按修订索引的 payload 列表**（下标 `revision - 1`）。
    列表只追加不覆盖，因此不变量 9（记录不可变）天然成立。
    """

    def __init__(self) -> None:
        self._records: dict[str, dict[str, list[Mapping[str, object]]]] = {}
        self._commit_index: list[str] = []
        self._project_id: str | None = None
        self._pending: list[tuple[AggregateKind, str, Mapping[str, object]]] = []
        self._staged_keys: set[tuple[str, str]] = set()

    # ---------------------------------------------------------------- 只读

    def current_revision(self, aggregate_kind: AggregateKind, record_id: str) -> int:
        return len(self._records.get(aggregate_kind, {}).get(record_id, []))

    def _read(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        revision: int,
    ) -> CommittedRecord:
        """不变量 8：必须显式给修订号，没有"读最新"的入口。"""
        revisions = self._records.get(aggregate_kind, {}).get(record_id, [])
        if revision < 1 or revision > len(revisions):
            raise ValueError(
                f"unknown revision {revision} for {aggregate_kind} {record_id}"
            )
        return CommittedRecord(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            revision=revision,
            payload=revisions[revision - 1],
        )

    def _query(self, query: RecordQuery) -> RecordPage:
        matched: list[CommittedRecord] = []
        for kind, by_id in self._records.items():
            if query.aggregate_kind is not None and kind != query.aggregate_kind:
                continue
            for record_id, revisions in by_id.items():
                if query.record_id is not None and record_id != query.record_id:
                    continue
                for index, payload in enumerate(revisions):
                    if payload.get("project_id") != query.project_id:
                        continue
                    matched.append(
                        CommittedRecord(
                            aggregate_kind=kind,  # type: ignore[arg-type]
                            record_id=record_id,
                            revision=index + 1,
                            payload=payload,
                        )
                    )
        matched.sort(key=lambda item: (item.aggregate_kind, item.record_id, item.revision))
        page = matched[: query.limit]
        next_cursor = str(query.limit) if len(matched) > query.limit else None
        return RecordPage(items=tuple(page), next_cursor=next_cursor)

    def _find_preparation(
        self,
        *,
        project_id: str,
        client_id: str,
        prepare_request_id: str,
    ) -> PreparationRecord | None:
        """**从落盘 payload 重建**，不查进程内字典。

        这一条是"重启后仍能查回原意图"的可测代理：只要 payload 少了任何身份字段，
        重建就会在这里失败，而不是等到真的重启才暴露。
        """
        record_id = preparation_record_id(
            project_id=project_id,
            client_id=client_id,
            prepare_request_id=prepare_request_id,
        )
        return self._rebuild_preparation(record_id)

    def _find_preparation_by_intent(self, *, intent_id: str) -> PreparationRecord | None:
        return self._rebuild_preparation(record_id_for_intent_id(intent_id))

    def _rebuild_preparation(self, record_id: str) -> PreparationRecord | None:
        revision = self.current_revision(PREPARATION_AGGREGATE_KIND, record_id)
        if revision < 1:
            return None
        committed = self._read(
            aggregate_kind=PREPARATION_AGGREGATE_KIND,
            record_id=record_id,
            revision=revision,
        )
        return preparation_record_from_payload(committed.payload)

    # ---------------------------------------------------------------- 事务

    def _open(self, project_id: str) -> None:
        if not project_id.strip():
            raise ValueError("project_id must not be empty")
        self._project_id = project_id

    def _commit_seq(self) -> str:
        return self._commit_index[-1] if self._commit_index else "commit-0"

    def _next_commit_seq(self) -> str:
        return f"commit-{len(self._commit_index) + 1}"

    def _stage_record(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> StagedRevision:
        return self._stage(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def _stage_preparation(self, *, record: PreparationRecord) -> StagedRevision:
        """不变量 5：同键同摘要复用原修订；同键异摘要抛冲突（**不覆盖**）。

        记录标识与落盘形状都由身份合同决定，与真实转接头
        （`application/planning/substrate_adapter.py`）**逐字一致**。
        """
        record_id = preparation_record_id(
            project_id=record.request.project_id,
            client_id=record.request.client_id,
            prepare_request_id=record.request.prepare_request_id,
        )
        existing = self._rebuild_preparation(record_id)
        if existing is not None:
            if existing.request.payload_hash != record.request.payload_hash:
                raise PreparationConflictError(
                    project_id=record.request.project_id,
                    client_id=record.request.client_id,
                    prepare_request_id=record.request.prepare_request_id,
                    existing_intent_id=existing.intent_id,
                    existing_payload_hash=existing.request.payload_hash,
                    existing_created_at_commit=existing.created_at_commit,
                )
            return StagedRevision(
                aggregate_kind=PREPARATION_AGGREGATE_KIND,
                record_id=record_id,
                revision=self.current_revision(PREPARATION_AGGREGATE_KIND, record_id),
            )
        return self._stage(
            aggregate_kind=PREPARATION_AGGREGATE_KIND,
            record_id=record_id,
            expected_revision=None,
            payload=preparation_record_payload(record),
        )

    def _commit(self) -> CommitResult:
        """不变量 6：提交后序号前进，记录对后续读取可见。"""
        self._require_open()
        if not self._pending:
            raise ValueError("nothing staged in this transaction")
        staged = tuple(self._pending)
        self._pending = []
        self._staged_keys = set()
        self._project_id = None

        created: list[StagedRevision] = []
        for kind, record_id, payload in staged:
            by_id = self._records.setdefault(kind, {})
            by_id.setdefault(record_id, []).append(payload)
            created.append(
                StagedRevision(
                    aggregate_kind=kind,
                    record_id=record_id,
                    revision=len(by_id[record_id]),
                )
            )
        self._commit_index.append(f"commit-{len(self._commit_index) + 1}")
        return CommitResult(commit_seq=self._commit_seq(), created=tuple(created))

    def _rollback(self) -> None:
        """不变量 7：丢弃本次全部暂存，不产生任何可见修订。"""
        self._require_open()
        self._pending = []
        self._staged_keys = set()
        self._project_id = None

    # ---------------------------------------------------------------- 内部

    def _require_open(self) -> None:
        if self._project_id is None:
            raise ValueError("no open transaction")

    def _stage(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> StagedRevision:
        """不变量 1—4：事务必须已开、修订必须匹配、同事务内不得重复暂存。"""
        self._require_open()
        if not record_id.strip():
            raise ValueError("record_id must not be empty")
        key = (aggregate_kind, record_id)
        if key in self._staged_keys:
            raise ValueError(
                f"{aggregate_kind} {record_id} is staged twice in one transaction"
            )

        current = self.current_revision(aggregate_kind, record_id)
        if expected_revision is None:
            if current != 0:
                raise ConcurrentEditError(
                    aggregate_kind=aggregate_kind,
                    record_id=record_id,
                    expected_revision=None,
                    current_revision=current,
                )
        elif expected_revision != current:
            raise ConcurrentEditError(
                aggregate_kind=aggregate_kind,
                record_id=record_id,
                expected_revision=expected_revision,
                current_revision=current,
            )

        self._staged_keys.add(key)
        self._pending.append((aggregate_kind, record_id, dict(payload)))
        return StagedRevision(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            revision=current + 1,
        )


class MemoryUnitOfWork:
    """`UnitOfWork` 的内存实现；与 `MemoryReader` 共享同一个 `MemoryStore`。

    **不继承 `MemoryStore`**：组合而不是继承，避免把只读方法也暴露给写侧。
    """

    def __init__(self, store: MemoryStore | None = None) -> None:
        self.store = store if store is not None else MemoryStore()

    def open(self, project_id: str) -> None:
        self.store._open(project_id)

    def commit_seq(self) -> str:
        return self.store._commit_seq()

    def next_commit_seq(self) -> str:
        return self.store._next_commit_seq()

    def stage_record(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> StagedRevision:
        return self.store._stage_record(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def stage_preparation(self, *, record: PreparationRecord) -> StagedRevision:
        return self.store._stage_preparation(record=record)

    def commit(self) -> CommitResult:
        return self.store._commit()

    def rollback(self) -> None:
        self.store._rollback()


class MemoryReader:
    """`RecordReader` 的内存实现；与 `MemoryUnitOfWork` 共享同一个 `MemoryStore`。"""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def read(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        revision: int,
    ) -> CommittedRecord:
        return self.store._read(
            aggregate_kind=aggregate_kind, record_id=record_id, revision=revision
        )

    def query(self, query: RecordQuery) -> RecordPage:
        return self.store._query(query)

    def find_preparation(
        self,
        *,
        project_id: str,
        client_id: str,
        prepare_request_id: str,
    ) -> PreparationRecord | None:
        return self.store._find_preparation(
            project_id=project_id,
            client_id=client_id,
            prepare_request_id=prepare_request_id,
        )

    def find_preparation_by_intent(self, *, intent_id: str) -> PreparationRecord | None:
        return self.store._find_preparation_by_intent(intent_id=intent_id)


__all__ = [
    "FixedClock",
    "MemoryReader",
    "MemoryStore",
    "MemoryUnitOfWork",
]
