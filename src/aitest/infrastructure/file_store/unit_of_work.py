"""Short file-backed unit of work with immutable revisions and idempotency."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from pathlib import Path

from aitest.contracts.identity import IntentId, RequestId

from .events import FileEventJournal
from .records import FileRecordRepository
from .workspace import Workspace


class FileUnitOfWork:
    def __init__(
        self,
        root: Path,
        *,
        journal: FileEventJournal | None = None,
    ) -> None:
        """文件 UOW。

        :param journal: 正式事件日志。注入后 commit 走正式事件日志路径；
            None 时 records.commit_transaction 写旧版 ``events.json``
            （迁移过渡期使用）。
        """
        self.workspace = Workspace(root)
        self.repo = FileRecordRepository(root, journal=journal)
        self.project: str | None = None
        self.pending: list[
            tuple[str, str, int | None, Mapping[str, object]]
        ] = []
        self.request_id: RequestId | None = None
        self.intent_id: IntentId | None = None
        self._lock_context: AbstractContextManager[object] | None = None

    def begin(
        self,
        request_id: RequestId,
        project_id: str,
        workspace_id: str | None = None,
        intent_id: IntentId | None = None,
    ) -> dict[str, object]:
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
        if self.project is not None:
            raise RuntimeError("transaction already open")
        self.project = project_id

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
        current = self.repo.current_revision(aggregate_kind, record_id)
        if current != (expected_revision or 0):
            raise ValueError("revision conflict")
        self.pending.append((aggregate_kind, record_id, current, dict(payload)))
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
        finally:
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

    def recover(self, workspace_id: str) -> dict[str, str]:
        self.workspace.validate(workspace_id)
        return {
            "workspace_id": workspace_id,
            "state": "not_started" if self.project is None else "active",
        }

    def _release(self) -> None:
        if self._lock_context is not None:
            self._lock_context.__exit__(None, None, None)
            self._lock_context = None
