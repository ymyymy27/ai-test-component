"""File-backed immutable record repository used by the A substrate."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from aitest.application.ports import CommittedRecord, RecordQuery
from aitest.contracts.queries import QuerySpec

from . import atomic
from .events import FileEventJournal
from .index import FileQueryIndex

#: 待提交业务记录：(聚合类型, 记录 ID, 期望修订（None 表示新建）, 业务载荷)。
#: 使用 Sequence + Mapping 而非 list/dict，保证 list 不变性与 dict→Mapping
#: 的协变：调用方传 ``list[tuple[..., dict[str, object]]]`` 同样合法。
PendingRecordEntry = tuple[str, str, int | None, Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class RecordQueryResult:
    """Structured query outcome; never raises on missing/corrupt index."""

    status: str  # "ok" | "maintenance_required" | "invalid_cursor"
    items: tuple[CommittedRecord, ...] = ()
    next_cursor: str | None = None


class FileRecordRepository:
    def __init__(
        self,
        root: Path,
        *,
        journal: FileEventJournal | None = None,
    ) -> None:
        """文件记录仓库。

        :param journal: 正式事件日志。非 None 时 ``commit_transaction`` 把
            事件写入该日志（``aitest.event/2.0``）并跳过旧版
            ``events.json``；None 时保持旧路径写 ``events.json``
            （向后兼容，迁移过渡期使用）。
        """
        self.root = root.resolve()
        self.path = self.root / "records.json"
        self._journal = journal

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"records": {}, "commit": 0}
        return cast(
            dict[str, Any],
            json.loads(self.path.read_text(encoding="utf-8")),
        )

    def _save(self, data: dict[str, Any]) -> None:
        atomic.write_json(self.path, data)

    def current_revision(self, kind: str, record_id: str) -> int:
        return len(self._load()["records"].get(kind, {}).get(record_id, []))

    def read(
        self, *, aggregate_kind: str, record_id: str, revision: int
    ) -> CommittedRecord:
        rows = self._load()["records"].get(aggregate_kind, {}).get(record_id, [])
        if revision < 1 or revision > len(rows):
            raise ValueError("unknown revision")
        return CommittedRecord(
            aggregate_kind=cast(Any, aggregate_kind),
            record_id=record_id,
            revision=revision,
            payload=rows[revision - 1],
        )

    def append(
        self,
        kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> int:
        data = self._load()
        rows = data["records"].setdefault(kind, {}).setdefault(record_id, [])
        current = len(rows)
        if expected_revision != current:
            raise ValueError(
                f"revision conflict: expected {expected_revision}, current {current}"
            )
        rows.append(dict(payload))
        data["commit"] += 1
        self._save(data)
        return current + 1

    def append_batch(
        self,
        pending: Sequence[PendingRecordEntry],
    ) -> list[tuple[str, str, int]]:
        data = self._load()
        created: list[tuple[str, str, int]] = []
        for kind, record_id, expected_revision, payload in pending:
            rows = data["records"].setdefault(kind, {}).setdefault(record_id, [])
            current = len(rows)
            if expected_revision != current:
                raise ValueError(
                    f"revision conflict: expected {expected_revision}, current {current}"
                )
            rows.append(dict(payload))
            data["commit"] += 1
            created.append((kind, record_id, current + 1))
        self._save(data)
        return created

    def append_intent(
        self,
        *,
        intent_id: str,
        kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> int:
        data = self._load()
        intents = data.setdefault("intents", {})
        fingerprint = json.dumps(dict(payload), sort_keys=True, ensure_ascii=False)
        if intent_id in intents:
            previous = intents[intent_id]
            if previous["fingerprint"] != fingerprint:
                raise ValueError("intent conflict")
            return int(previous["revision"])
        rows = data["records"].setdefault(kind, {}).setdefault(record_id, [])
        current = len(rows)
        if expected_revision != current:
            raise ValueError(
                f"revision conflict: expected {expected_revision}, current {current}"
            )
        rows.append(dict(payload))
        revision = current + 1
        data["commit"] += 1
        data.setdefault("intents", {})[intent_id] = {
            "fingerprint": fingerprint,
            "revision": revision,
        }
        self._save(data)
        return revision

    def query(self, query: RecordQuery) -> RecordQueryResult:
        index = FileQueryIndex(self.root)
        result = index.query_spec(
            QuerySpec(
                project_id=query.project_id,
                aggregate_kind=query.aggregate_kind,
                record_id=query.record_id,
                limit=query.limit,
            )
        )
        if result.status in ("maintenance_required", "invalid_cursor"):
            return RecordQueryResult(status=result.status)
        items = tuple(
            self.read(
                aggregate_kind=row["aggregate_kind"],
                record_id=row["record_id"],
                revision=int(row["revision"]),
            )
            for row in result.items
        )
        return RecordQueryResult(
            status="ok", items=items, next_cursor=result.next_cursor
        )

    def _load_index_rows(self) -> list[dict[str, Any]]:
        path = self.root / "indexes.json"
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            rows = raw.get("rows")
            return list(rows) if isinstance(rows, list) else []
        except (OSError, json.JSONDecodeError, TypeError):
            return []

    def _load_commits(self) -> list[dict[str, Any]]:
        path = self.root / "commit.json"
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            commits = raw.get("commits")
            return list(commits) if isinstance(commits, list) else []
        except (OSError, json.JSONDecodeError, TypeError):
            return []

    def _save_commits(
        self, commits: list[dict[str, Any]], sequence: int
    ) -> None:
        atomic.write_json(
            self.root / "commit.json",
            {"schema": "aitest.commit/1.0", "sequence": sequence, "commits": commits},
        )

    def _load_events(self) -> list[dict[str, Any]]:
        path = self.root / "events.json"
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            events = raw.get("events")
            return list(events) if isinstance(events, list) else []
        except (OSError, json.JSONDecodeError, TypeError):
            return []

    def _save_events(self, events: list[dict[str, Any]]) -> None:
        atomic.write_json(
            self.root / "events.json",
            {"schema": "aitest.events/1.0", "events": events},
        )

    def _write_active_marker(
        self,
        *,
        request_id: str,
        intent_id: str | None,
        project_id: str,
        commit_sequence: int,
    ) -> None:
        atomic.write_json(
            self.root / "transactions" / "active.json",
            {
                "request_id": request_id,
                "intent_id": intent_id,
                "project_id": project_id,
                "commit_sequence": commit_sequence,
                "state": "in_progress",
            },
        )

    def _clear_active_marker(self) -> None:
        path = self.root / "transactions" / "active.json"
        if path.exists():
            path.unlink()

    @staticmethod
    def _intent_fingerprint(
        pending: Sequence[PendingRecordEntry],
        project_id: str,
    ) -> str:
        """Business-input fingerprint for an intent; resulting revisions excluded.

        Same intent retries carry the same business inputs (kind/record/payload)
        and must return the original result even though expected revisions differ.
        """
        body = [
            {
                "aggregate_kind": kind,
                "record_id": record_id,
                "payload": dict(payload),
            }
            for kind, record_id, _expected_revision, payload in pending
        ]
        return json.dumps(
            {"project_id": project_id, "records": body},
            sort_keys=True,
            ensure_ascii=False,
        )

    def commit_transaction(
        self,
        pending: Sequence[PendingRecordEntry],
        *,
        request_id: str,
        intent_id: str | None,
        project_id: str,
        workspace_id: str | None,
        writer_epoch: int = 1,
    ) -> tuple[list[tuple[str, str, int]], int]:
        """Publish intent records, business records, commit list, index and events.

        Records (records.json) are written last as the source of truth. If any
        projection write fails before records.json is replaced, the old boundary
        stays valid and the active marker (transactions/active.json) lets recovery
        clean up stale projections.

        事件日志双轨：

        - 注入 ``journal`` 时走正式事件日志 ``aitest.event/2.0``：
          一个事务对应一个 boundary（commit_sequence = 事务最后一条
          记录的 commit_sequence），事务内每条记录对应一个 event；
          旧版 ``events.json`` 不再写入。
        - 未注入时保持旧路径写 ``events.json``（迁移过渡期使用）。
        """
        data = self._load()

        # 持久意图幂等：同意图 + 同业务输入（跨入口/重启）直接返回原提交结果，
        # 不生成第二个修订；业务输入不同则意图冲突。重跑须建立新意图。
        if intent_id is not None:
            stored_intent = data.get("intents", {}).get(intent_id)
            if isinstance(stored_intent, dict):
                fingerprint = self._intent_fingerprint(pending, project_id)
                if stored_intent.get("fingerprint") != fingerprint:
                    raise ValueError("intent conflict")
                original_created = [
                    (str(item[0]), str(item[1]), int(item[2]))
                    for item in stored_intent.get("created", [])
                    if isinstance(item, (list, tuple)) and len(item) == 3
                ]
                return original_created, int(stored_intent["commit_sequence"])

        current_index_rows = self._load_index_rows()
        commits = self._load_commits()
        events = self._load_events()
        created: list[tuple[str, str, int]] = []
        new_index_rows = list(current_index_rows)
        commit_sequence = int(data.get("commit", 0))
        new_events: list[dict[str, Any]] = []
        for kind, record_id, expected_revision, payload in pending:
            rows = data["records"].setdefault(kind, {}).setdefault(record_id, [])
            current = len(rows)
            if expected_revision != current:
                raise ValueError(
                    f"revision conflict: expected {expected_revision}, "
                    f"current {current}"
                )
            rows.append(dict(payload))
            commit_sequence += 1
            revision = current + 1
            created.append((kind, record_id, revision))
            new_index_rows.append(
                {
                    "project_id": project_id,
                    "aggregate_kind": kind,
                    "record_id": record_id,
                    "revision": revision,
                    "commit_sequence": commit_sequence,
                }
            )
            new_events.append(
                {
                    "event_type": "record_created",
                    "project_id": project_id,
                    "aggregate_kind": kind,
                    "record_id": record_id,
                    "revision": revision,
                    "commit_sequence": commit_sequence,
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "workspace_id": workspace_id,
                    "writer_epoch": writer_epoch,
                }
            )
        if intent_id is not None:
            data.setdefault("intents", {})[intent_id] = {
                "fingerprint": self._intent_fingerprint(pending, project_id),
                "commit_sequence": commit_sequence,
                "created": [list(item) for item in created],
            }
        data["commit"] = commit_sequence
        commit_entry = {
            "commit_sequence": commit_sequence,
            "request_id": request_id,
            "intent_id": intent_id,
            "project_id": project_id,
            "workspace_id": workspace_id,
            "writer_epoch": writer_epoch,
            "created": [
                {"aggregate_kind": k, "record_id": rid, "revision": rev}
                for k, rid, rev in created
            ],
            "state": "committed",
        }
        self._write_active_marker(
            request_id=request_id,
            intent_id=intent_id,
            project_id=project_id,
            commit_sequence=commit_sequence,
        )
        records_published = False
        boundary_started = False
        try:
            if self._journal is not None:
                # 正式事件日志：事件先进入边界暂存（journal 尚不可见），
                # 一个事务一个 boundary，事务内多条 record_event。
                self._journal.begin_boundary(
                    commit_sequence=commit_sequence,
                    request_id=request_id,
                    intent_id=intent_id,
                    workspace_id=workspace_id or "",
                    project_id=project_id,
                    writer_epoch=writer_epoch,
                )
                boundary_started = True
                for (_kind, record_id, revision), event_payload in zip(
                    created, new_events, strict=True
                ):
                    self._journal.record_event(
                        commit_sequence=commit_sequence,
                        event_type=str(event_payload["event_type"]),
                        project_id=project_id,
                        record_id=record_id,
                        revision=revision,
                        request_id=request_id,
                        intent_id=intent_id,
                        workspace_id=workspace_id or "",
                        writer_epoch=writer_epoch,
                    )
            # 唯一权威提交边界：records.json 先发布。此前事件仅在暂存区、
            # 索引/提交清单未写，任何投影都读不到本次事务。
            self._save(data)
            records_published = True
            # records 之后再发布派生投影；投影可由权威边界重建。
            self._save_commits(commits + [commit_entry], commit_sequence)
            if self._journal is not None:
                self._journal.commit_boundary(commit_sequence=commit_sequence)
            else:
                self._save_events(events + new_events)
            FileQueryIndex(self.root).rebuild(new_index_rows)
        except BaseException:
            # records 未发布：没有已确认边界，把暂存事件隔离留证（不写入
            # journal），保证未提交投影不可读；活动标记由 finally 清除。
            if boundary_started and not records_published and self._journal is not None:
                with suppress(Exception):
                    self._journal.rollback_boundary(commit_sequence=commit_sequence)
            raise
        finally:
            self._clear_active_marker()
        return created, commit_sequence
