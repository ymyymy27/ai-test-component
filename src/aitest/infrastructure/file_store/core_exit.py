"""Explicit exit admission: sidecars cannot hide authoritative unknown execution."""

import json
from collections.abc import Callable
from pathlib import Path

from aitest.domain.execution.output import require_saved_output_cursors
from aitest.domain.execution.runs import (
    Attempt,
    CaptureCompleteness,
    ExecutionHandle,
    RecoveryRecord,
    execution_boundary_pending,
    has_complete_capture,
)

from .commit_manifest import FileCommitStore
from .execution_handles import FileExecutionHandleStore
from .maintenance import detect_activity_blocker
from .records import FileRecordRepository
from .references import verify_record_objects
from .sharded_records import ShardedRows


class FileCoreExitGuard:
    """A shutdown maintenance read, never a periodic or list-query full scan."""

    def __init__(
        self,
        root: Path,
        checkpoint_reader: Callable[[str, str], RecoveryRecord],
        material_reader: Callable[[Attempt], None],
    ) -> None:
        self.root, self.checkpoint_reader = root, checkpoint_reader
        self.material_reader = material_reader

    def blocker(self) -> str | None:
        try:
            FileCommitStore.reject_links(self.root)
            if detect_activity_blocker(self.root) is not None:
                return "执行或输出材料尚未核实，核心保留并继续服务"
            repository = FileRecordRepository(self.root)
            data = repository._load()
            handles: dict[str, ExecutionHandle | None] = {}
            # Only each Attempt's current authority is read; no history payload replay.
            for identity, rows in data["records"].get("execution_checkpoint", {}).items():
                raw = rows[-1]
                project = raw.get("project_id")
                if (
                    not isinstance(identity, str)
                    or not isinstance(project, str)
                    or not project.strip()
                    or isinstance(rows, ShardedRows)
                    and rows.metadata.get("project_id") != project
                ):
                    return "权威执行检查点归属无法核实，核心保留并继续服务"
                record = self.checkpoint_reader(project, identity)
                if record.project_id != project or record.attempt.attempt_id != identity:
                    return "权威执行检查点身份无法核实，核心保留并继续服务"
                verify_record_objects(self.root, raw, project)
                if execution_boundary_pending(record.attempt):
                    return "权威执行尚无可靠终止事实，核心保留并继续服务"
                attempt = record.attempt
                if (
                    attempt.capture_completeness is CaptureCompleteness.COMPLETE
                    and not has_complete_capture(attempt)
                ):
                    return "完整采集声明无法核实，核心保留并继续服务"
                require_saved_output_cursors(
                    attempt.attempt_id, attempt.output_cursors, attempt.output_block_refs
                )
                self.material_reader(attempt)
                handles[identity] = attempt.execution_handle_ref
            store = FileExecutionHandleStore(self.root)
            for path in (self.root / "execution-handles").glob("*.json"):
                if path.stem.endswith(("-result", "-stop")):
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                saved = store.load(payload["handle"]["handle_id"])
                if handles.get(saved.attempt_id) != saved.handle:
                    return "执行句柄尚未与权威检查点闭合，核心保留并继续服务"
            return None
        except Exception:
            return "退出所需材料无法核实，核心保留并继续服务"
