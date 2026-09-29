"""Short file-backed unit of work with immutable revisions and idempotency."""
from __future__ import annotations
from collections.abc import Mapping
from pathlib import Path
from aitest.contracts.identity import IntentId, RequestId
from .records import FileRecordRepository
from .workspace import Workspace
class FileUnitOfWork:
    def __init__(self, root: Path): self.workspace=Workspace(root); self.repo=FileRecordRepository(root); self.project=None; self.pending=[]; self.request_id=None; self.intent_id=None; self._lock_context=None
    def begin(self, request_id: RequestId, project_id: str, workspace_id: str | None = None, intent_id: IntentId | None = None):
        if self.project is not None: raise RuntimeError("transaction already open")
        if workspace_id is not None: self.workspace.validate(workspace_id)
        self._lock_context=self.workspace.acquire(); self._lock_context.__enter__()
        try:
            self.request_id=request_id; self.intent_id=intent_id; self.open(project_id); return {"request_id":request_id,"state":"active","intent_id":intent_id}
        except BaseException:
            self._lock_context.__exit__(None,None,None); self._lock_context=None; raise
    def open(self, project_id:str):
        if self.project is not None: raise RuntimeError("transaction already open")
        self.project=project_id
    def stage_record(self, *, aggregate_kind:str, record_id:str, expected_revision:int|None, payload:Mapping[str,object]):
        if self.project is None: raise RuntimeError("no open transaction")
        current=self.repo.current_revision(aggregate_kind,record_id)
        if current != (expected_revision or 0): raise ValueError("revision conflict")
        self.pending.append((aggregate_kind,record_id,current,dict(payload))); return current+1
    def commit(self, request_id: RequestId | None = None, workspace_id: str | None = None):
        if request_id is not None and request_id != self.request_id: raise RuntimeError("request_id does not own transaction")
        if workspace_id is not None: self.workspace.validate(workspace_id)
        if self.project is None or not self.pending: raise RuntimeError("nothing staged")
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
        rid=self.request_id; self.project=None; self.pending=[]; self.request_id=None; self.intent_id=None
        return {"request_id":rid,"state":"committed","created":created,"commit_sequence":commit_sequence}
    def rollback(self, request_id: RequestId | None = None, workspace_id: str | None = None):
        if request_id is not None and request_id != self.request_id: raise RuntimeError("request_id does not own transaction")
        if workspace_id is not None: self.workspace.validate(workspace_id)
        rid=self.request_id; iid=self.intent_id; self.project=None; self.pending=[]; self.request_id=None; self.intent_id=None; self._release()
        return {"request_id":rid,"state":"rolled_back","intent_id":iid}
    def recover(self, workspace_id: str):
        self.workspace.validate(workspace_id); return {"workspace_id":workspace_id,"state":"not_started" if self.project is None else "active"}
    def _release(self):
        if self._lock_context is not None: self._lock_context.__exit__(None,None,None); self._lock_context=None
