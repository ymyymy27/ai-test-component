"""A 的端口签名落地时，把 `WorkspaceUnitOfWork` / `RecordRepository` 适配到本地薄底座。

**当前状态：骨架。** A 的端口在 `application/ports.py` 中仍只有文档字符串，
因此本模块的方法体一律 `NotImplementedError`，实现留到 A 的签名可用之后。

保留本文件的意义是**把三处翻译职责先写下来**，届时只需填方法体，
不必重新推演（设计说明第 5 节）：

1. **修订语义**：本地 `expected_revision: int | None`（`None` = 新建）
   ↔ A 的 `expected_revision`。若 A 用别的"新建"表示法（如 `0` 或省略参数），
   在 `to_expected_revision()` 里转换，**不要改用例**。
2. **错误类型**：本地 `ConcurrentEditError` / `PreparationConflictError`
   ↔ A 的端口错误类。若 A 返回"当前修订 + 差异提示"的元组而不抛异常，
   在适配层包成异常，**用例依赖异常语义**。
3. **payload 形状**：本地 `Mapping[str, object]` ↔ A 的记录 payload。
   若 A 直接回领域对象而非 payload，在适配层做转换
   （这正是 `docs/接口对接/B-A-端口与保存需求.md` 第 8.3 节第 2 问的两种选择）。

**不得**把本模块的方法体做成"猜测 A 的实现"：A 的签名未定之前，宁可保持未实现，
也不要按推测写一套（B 包 AI 规则第 3.9 节：未完成的动作不注册为可用能力）。
"""

from __future__ import annotations

from collections.abc import Mapping

from aitest.application.planning.preparation import PreparationRecord
from aitest.application.planning.substrate import (
    AggregateKind,
    CommitResult,
    CommittedRecord,
    RecordPage,
    RecordQuery,
    StagedRevision,
)

_UNIMPLEMENTED = (
    "A has not published WorkspaceUnitOfWork/RecordRepository signatures yet; "
    "see docs/接口对接/B-A-端口与保存需求.md section 8"
)


def to_expected_revision(local: int | None) -> object:
    """本地"新建"语义 → A 的期望修订表示。签名落地后按 A 的约定填写。"""
    raise NotImplementedError(_UNIMPLEMENTED)


class PortsUnitOfWork:
    """把 A 的 `WorkspaceUnitOfWork` 适配成本地 `UnitOfWork`。"""

    def open(self, project_id: str) -> None:
        raise NotImplementedError(_UNIMPLEMENTED)

    def commit_seq(self) -> str:
        raise NotImplementedError(_UNIMPLEMENTED)

    def next_commit_seq(self) -> str:
        raise NotImplementedError(_UNIMPLEMENTED)

    def stage_record(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> StagedRevision:
        raise NotImplementedError(_UNIMPLEMENTED)

    def stage_preparation(
        self,
        *,
        record: PreparationRecord,
        payload: Mapping[str, object],
    ) -> StagedRevision:
        raise NotImplementedError(_UNIMPLEMENTED)

    def commit(self) -> CommitResult:
        raise NotImplementedError(_UNIMPLEMENTED)

    def rollback(self) -> None:
        raise NotImplementedError(_UNIMPLEMENTED)


class PortsRecordReader:
    """把 A 的 `RecordRepository` 适配成本地 `RecordReader`。"""

    def read(
        self,
        *,
        aggregate_kind: AggregateKind,
        record_id: str,
        revision: int,
    ) -> CommittedRecord:
        raise NotImplementedError(_UNIMPLEMENTED)

    def query(self, query: RecordQuery) -> RecordPage:
        raise NotImplementedError(_UNIMPLEMENTED)

    def find_preparation(
        self,
        *,
        project_id: str,
        client_id: str,
        prepare_request_id: str,
    ) -> PreparationRecord | None:
        raise NotImplementedError(_UNIMPLEMENTED)

    def find_preparation_by_intent(self, *, intent_id: str) -> PreparationRecord | None:
        raise NotImplementedError(_UNIMPLEMENTED)


__all__ = ["PortsRecordReader", "PortsUnitOfWork", "to_expected_revision"]
