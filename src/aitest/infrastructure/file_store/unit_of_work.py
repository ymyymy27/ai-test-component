"""Short file-backed unit of work with immutable revisions and idempotency."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from pathlib import Path

from aitest.contracts.identity import IntentId, RequestId

from ..security import KnownSecretRegistry, UnsafeMaterialError, guard_value, known_secrets
from .events import FileEventJournal
from .records import FileRecordRepository
from .workspace import Workspace


def _has_content_fingerprint(value: object) -> bool:
    if isinstance(value, Mapping):
        if any(key in value for key in ("content_digest", "projection_digest", "digest")):
            return True
        return any(_has_content_fingerprint(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_content_fingerprint(item) for item in value)
    return False


class FileUnitOfWork:
    def __init__(
        self,
        root: Path,
        *,
        journal: FileEventJournal | None = None,
        registry: KnownSecretRegistry | None = None,
    ) -> None:
        """文件 UOW。

        :param journal: 正式事件日志。注入后 commit 走正式事件日志路径；
            None 时 records.commit_transaction 写旧版 ``events.json``
            （迁移过渡期使用）。
        :param registry: 已知凭据登记表，默认进程级全局表（A-09）。
        """
        self._registry = registry if registry is not None else known_secrets()
        self.workspace = Workspace(root)
        self.repo = FileRecordRepository(root, journal=journal)
        self.project: str | None = None
        self.pending: list[tuple[str, str, int | None, Mapping[str, object]]] = []
        self.request_id: RequestId | None = None
        self.intent_id: IntentId | None = None
        self._lock_context: AbstractContextManager[object] | None = None
        #: 上一次 commit 发布结果未知（提交抛错且锁已释放）。此时禁止
        #: 无锁重试/继续暂存，只能重新持锁核实权威边界或回滚（A-12）。
        self._commit_uncertain = False

    def begin(
        self,
        request_id: RequestId,
        project_id: str,
        workspace_id: str | None = None,
        intent_id: IntentId | None = None,
    ) -> dict[str, object]:
        if self._commit_uncertain:
            raise RuntimeError("previous commit result is unknown; verify or rollback required")
        if self.project is not None:
            raise RuntimeError("transaction already open")
        if workspace_id is not None:
            self.workspace.validate(workspace_id)
        self._lock_context = self.workspace.acquire()
        self._lock_context.__enter__()
        try:
            self.request_id = request_id
            self.intent_id = intent_id
            self.open(project_id)
            result: dict[str, object] = {
                "request_id": request_id,
                "state": "active",
            }
            if intent_id is not None:
                result["intent_id"] = intent_id
            return result
        except BaseException:
            self._lock_context.__exit__(None, None, None)
            self._lock_context = None
            raise

    def open(self, project_id: str) -> None:
        if self._commit_uncertain:
            raise RuntimeError("previous commit result is unknown; verify or rollback required")
        if self.project is not None:
            raise RuntimeError("transaction already open")
        self.project = project_id

    def current_commit_sequence(self) -> int:
        """工作空间当前全局提交计数；**未开事务也可读**。

        除满足 AB-001 §8.8 冻结的 ``commit_seq`` 外，也作为 B 转接头
        ``CommitSequenceSource.current_commit_sequence`` 的正式装配实现
        （A-02 由 bootstrap 注入本对象），替代 B 侧的恢复巡检临时接法。
        """
        return self.repo.current_commit_sequence()

    def current_revision(self, *, aggregate_kind: str, record_id: str) -> int:
        return self.repo.current_revision(aggregate_kind, record_id)

    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> object:
        return self.repo.read(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            revision=revision,
        )

    def commit_seq(self) -> str:
        """当前提交序号（字符串）；未开事务也可读，冻结于 AB-001 §8.8。"""
        return str(self.current_commit_sequence())

    def next_commit_seq(self) -> str:
        """下一条暂存记录提交后将得到的序号（字符串）。

        A 的提交序号按**记录**递增：已暂存 N 条未提交记录时为
        「当前序号 + N + 1」；调用方据此在 ``commit()`` 之前把
        ``created_at_commit`` 写进不可变 payload。
        """
        return str(self.current_commit_sequence() + len(self.pending) + 1)

    def stage_record(
        self,
        *,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> int:
        if self.project is None:
            raise RuntimeError("no open transaction")
        if self._commit_uncertain:
            raise RuntimeError("previous commit result is unknown; verify or rollback required")
        current = self.repo.current_revision(aggregate_kind, record_id)
        if current != (expected_revision or 0):
            raise ValueError("revision conflict")
        # A-09 落盘前底线：业务记录 payload 经结构+已知凭据过滤后再暂存，
        # records.json 中不得出现凭据原文（后续投影/备份/导出只读安全副本）。
        safe_payload, changed = guard_value(dict(payload), self._registry)
        if changed and _has_content_fingerprint(payload):
            raise UnsafeMaterialError(
                "fingerprinted record requires filtering before computing its digest"
            )
        if not isinstance(safe_payload, Mapping):
            raise TypeError("guarded payload must remain a mapping")
        self.pending.append((aggregate_kind, record_id, current, safe_payload))
        return current + 1

    def commit(
        self,
        request_id: RequestId | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, object]:
        if request_id is not None and request_id != self.request_id:
            raise RuntimeError("request_id does not own transaction")
        if workspace_id is not None:
            self.workspace.validate(workspace_id)
        if self.project is None or not self.pending:
            raise RuntimeError("nothing staged")
        if self._commit_uncertain:
            raise RuntimeError("previous commit result is unknown; verify or rollback required")
        if self._lock_context is None:
            # 绝不允许在无 writer.lock 的状态下发布：两个交错 UOW 无锁提交
            # 会同时读到旧边界、互相覆盖并丢记录（A-12）。
            raise RuntimeError("commit requires the writer lock")
        assert self.request_id is not None
        try:
            created, commit_sequence = self.repo.commit_transaction(
                self.pending,
                request_id=self.request_id,
                intent_id=self.intent_id,
                project_id=self.project,
                workspace_id=self.workspace.workspace_id,
                writer_epoch=self.workspace.identity.get("writer_epoch", 1),
            )
        except BaseException:
            # 发布结果未知：先释放锁允许他者工作，但本实例进入“仅核实/
            # 回滚”状态——暂存与身份保留待查，禁止无锁重试或继续暂存，
            # 避免与持锁提交交错覆盖已成功发布的记录（A-12）。
            self._release()
            self._commit_uncertain = True
            raise
        self._release()
        rid = self.request_id
        self.project = None
        self.pending = []
        self.request_id = None
        self.intent_id = None
        return {
            "request_id": rid,
            "state": "committed",
            "created": created,
            "commit_sequence": commit_sequence,
        }

    def rollback(
        self,
        request_id: RequestId | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, object]:
        if request_id is not None and request_id != self.request_id:
            raise RuntimeError("request_id does not own transaction")
        if workspace_id is not None:
            self.workspace.validate(workspace_id)
        # 上一次提交结果未知时，rollback 是“重新准入”：重新持锁并核实权威
        # 边界——已实际发布则如实报 committed（业务事实不可回滚），确认未
        # 发布才丢弃暂存（A-12）。
        if self._commit_uncertain:
            return self._resolve_uncertain()
        rid = self.request_id
        iid = self.intent_id
        self.project = None
        self.pending = []
        self.request_id = None
        self.intent_id = None
        self._release()
        rollback_result: dict[str, object] = {
            "request_id": rid,
            "state": "rolled_back",
        }
        if iid is not None:
            rollback_result["intent_id"] = iid
        return rollback_result

    def _resolve_uncertain(self) -> dict[str, object]:
        """重新持锁核实未知提交的权威结果，再决定已提交或回滚。"""
        rid = self.request_id
        iid = self.intent_id
        with self.workspace.acquire():
            committed = self.repo.find_committed_request(
                request_id=rid,
                intent_id=iid,
                project_id=self.project,
            )
            if committed is not None:
                from .commit_manifest import FileCommitStore
                from .publication_backend import FilePublicationBackend

                if (
                    FileCommitStore(self.workspace.root).read_current(verify_material=True)
                    is not None
                ):
                    FilePublicationBackend(self.workspace.root).confirm_current()
                result: dict[str, object] = {
                    "request_id": rid,
                    "state": "committed",
                    "commit_sequence": committed.get("commit_sequence"),
                    "created": committed.get("created", []),
                }
            else:
                result = {"request_id": rid, "state": "rolled_back"}
            if iid is not None:
                result["intent_id"] = iid
        self.project = None
        self.pending = []
        self.request_id = None
        self.intent_id = None
        self._commit_uncertain = False
        return result

    def recover(self, workspace_id: str) -> dict[str, str]:
        self.workspace.validate(workspace_id)
        if self._commit_uncertain:
            return {"workspace_id": workspace_id, "state": "uncertain"}
        return {
            "workspace_id": workspace_id,
            "state": "not_started" if self.project is None else "active",
        }

    def _release(self) -> None:
        if self._lock_context is not None:
            self._lock_context.__exit__(None, None, None)
            self._lock_context = None
