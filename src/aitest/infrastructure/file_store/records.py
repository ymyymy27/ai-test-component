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

    def current_revision(self, aggregate_kind: str, record_id: str) -> int:
        """当前修订（该业务身份的不可变修订条数）；只读，不经索引。

        位置/关键字两种调用都支持：AB-001 §8.8 冻结的端口签名是
        关键字形态，B 转接头按位置形态调用，具体实现同时满足两者。
        """
        return len(
            self._load()["records"].get(aggregate_kind, {}).get(record_id, [])
        )

    def current_commit_sequence(self) -> int:
        """工作空间全局提交计数（``records.json`` 的 ``commit``）；只读。

        对应 AB-001 §8.8：``commit_seq`` / ``next_commit_seq`` 的事实
        来源，未开事务也可读。计数按**记录**递增，一次多条记录的提交
        会跨多个序号，提交结果取其中最后一个序号。
        """
        return int(self._load().get("commit", 0))

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
                cursor=query.cursor,
            )
        )
        if result.status in ("maintenance_required", "invalid_cursor"):
            return RecordQueryResult(status=result.status)
        # 列表只读取摘要索引；整页载荷一次性从权威边界水合，避免每条记录
        # 都整读一次 records.json。
        data = self._load()["records"]
        items: list[CommittedRecord] = []
        for row in result.items:
            kind = str(row["aggregate_kind"])
            record_id = str(row["record_id"])
            revision = int(row["revision"])
            payload = data.get(kind, {}).get(record_id, [])
            if revision < 1 or revision > len(payload):
                # 索引指向了权威边界中不存在的修订：索引已损坏，显式维护。
                return RecordQueryResult(status="maintenance_required")
            items.append(
                CommittedRecord(
                    aggregate_kind=cast(Any, kind),
                    record_id=record_id,
                    revision=revision,
                    payload=payload[revision - 1],
                )
            )
        return RecordQueryResult(
            status="ok", items=tuple(items), next_cursor=result.next_cursor
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

    # ----- 权威边界与投影重建（A-04）----------------------------------

    def authoritative_commits(self) -> list[dict[str, Any]]:
        """读取 records.json 内的权威提交台账。

        台账与业务记录在同一次原子写中发布，是“哪些提交已确认”的唯一
        事实来源；commit.json/indexes/events 都是可从它重建的投影。
        """
        data = self._load()
        commits = data.get("commits")
        if not isinstance(commits, list):
            return []
        return [entry for entry in commits if isinstance(entry, dict)]

    def committed_sequences(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                int(entry["commit_sequence"])
                for entry in self.authoritative_commits()
                if isinstance(entry.get("commit_sequence"), int)
            )
        )

    @staticmethod
    def _index_rows_from_commits(
        commits: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for entry in commits:
            project_id = entry.get("project_id")
            commit_sequence = entry.get("commit_sequence")
            created = entry.get("created")
            if not isinstance(created, list):
                continue
            for item in created:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "project_id": project_id,
                        "aggregate_kind": item.get("aggregate_kind"),
                        "record_id": item.get("record_id"),
                        "revision": item.get("revision"),
                        "commit_sequence": commit_sequence,
                    }
                )
        return rows

    @staticmethod
    def _events_from_commits(
        commits: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """从权威台账重放旧版 events.json 事件（迁移期双轨）。"""
        events: list[dict[str, Any]] = []
        for entry in commits:
            created = entry.get("created")
            if not isinstance(created, list):
                continue
            for item in created:
                if not isinstance(item, dict):
                    continue
                events.append(
                    {
                        "event_type": "record_created",
                        "project_id": entry.get("project_id"),
                        "aggregate_kind": item.get("aggregate_kind"),
                        "record_id": item.get("record_id"),
                        "revision": item.get("revision"),
                        "commit_sequence": entry.get("commit_sequence"),
                        "request_id": entry.get("request_id"),
                        "intent_id": entry.get("intent_id"),
                        "workspace_id": entry.get("workspace_id"),
                        "writer_epoch": entry.get("writer_epoch"),
                    }
                )
        return events

    def _journal_world(self) -> bool:
        """工作空间是否使用正式事件日志（含崩溃后残留暂存/边界）。"""
        log_dir = self.root / "event-log"
        journal = log_dir / "journal.jsonl"
        try:
            if journal.exists() and journal.stat().st_size > 0:
                return True
            for sub in ("boundaries", "staging"):
                directory = log_dir / sub
                if directory.exists() and any(directory.iterdir()):
                    return True
        except OSError:
            return True
        return False

    def rebuild_projections(self) -> list[str]:
        """从 records 权威边界重建落后/缺失的提交清单、索引和旧版事件。

        只修复投影，绝不改写业务记录；已与权威边界一致的投影不重写
        （健康工作空间不产生“修复”动作）。返回执行的修复动作描述。
        """
        actions: list[str] = []
        data = self._load()
        ledger = data.get("commits")
        if not isinstance(ledger, list):
            return actions
        ledger = [entry for entry in ledger if isinstance(entry, dict)]
        if not ledger:
            # 没有任何权威提交事实时不得伪造投影变更（健康/旧版工作空间
            # 不产生“修复”动作）。
            return actions
        ledger_sequences = {
            int(entry["commit_sequence"])
            for entry in ledger
            if isinstance(entry.get("commit_sequence"), int)
        }

        # 1) 提交清单 commit.json：合并既有可读条目与权威台账。
        committed = self._load_commits()
        merged_commits: dict[int, dict[str, Any]] = {
            int(entry["commit_sequence"]): entry
            for entry in committed
            if isinstance(entry, dict)
            and isinstance(entry.get("commit_sequence"), int)
        }
        for entry in ledger:
            merged_commits[int(entry["commit_sequence"])] = entry
        ordered_commits = [
            merged_commits[seq] for seq in sorted(merged_commits)
        ]
        current_commits = self._load_commits()
        current_sequences = {
            int(entry["commit_sequence"])
            for entry in current_commits
            if isinstance(entry, dict)
            and isinstance(entry.get("commit_sequence"), int)
        }
        if current_sequences < ledger_sequences or not (self.root / "commit.json").exists():
            sequence = max(merged_commits, default=0)
            self._save_commits(ordered_commits, sequence)
            actions.append("从 records 权威边界重建提交清单 commit.json")

        # 2) 查询索引 indexes.json：合并既有可读行与台账派生行后整投影重发。
        index = FileQueryIndex(self.root)
        raw_index = index._read_raw()  # noqa: SLF001 - 同底座投影协作
        need_rebuild = True
        if raw_index is not None and raw_index.get("version") == FileQueryIndex.VERSION:
            existing_rows = raw_index.get("rows")
            if isinstance(existing_rows, list):
                derived = self._index_rows_from_commits(ledger)
                merged_rows = self._merge_index_rows(
                    [row for row in existing_rows if isinstance(row, dict)],
                    derived,
                )
                index_root = raw_index.get("last_commit_sequence")
                covers_commit_root = (
                    isinstance(index_root, int) and index_root >= max(ledger_sequences)
                )
                if (
                    covers_commit_root
                    and len(merged_rows) == len(existing_rows)
                    and all(
                        row.get("commit_sequence") is not None for row in merged_rows
                    )
                ):
                    need_rebuild = False
        if need_rebuild:
            merged_rows = self._merge_index_rows(
                self._load_index_rows(), self._index_rows_from_commits(ledger)
            )
            index.rebuild(merged_rows)
            actions.append("从 records 权威边界重建查询索引 indexes.json")

        # 3) 旧版 events.json（仅非 journal 世界的迁移期工作空间）。
        if not self._journal_world():
            expected_events = self._events_from_commits(ledger)
            if len(self._load_events()) < len(expected_events):
                self._save_events(expected_events)
                actions.append("从 records 权威边界重建旧版事件 events.json")
        return actions

    @staticmethod
    def _merge_index_rows(
        existing: Sequence[dict[str, Any]],
        derived: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[tuple[object, ...], dict[str, Any]] = {}
        for row in (*existing, *derived):
            key = (
                row.get("project_id"),
                row.get("aggregate_kind"),
                row.get("record_id"),
                row.get("revision"),
            )
            # 台账派生行为准；台账缺失的历史投影行保留，不丢历史。
            if key not in merged or row.get("commit_sequence") is not None:
                merged[key] = dict(row)
        return sorted(
            merged.values(),
            key=lambda row: (
                int(row.get("commit_sequence") or 0),
                str(row.get("aggregate_kind") or ""),
                str(row.get("record_id") or ""),
                int(row.get("revision") or 0),
            ),
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
        # 权威提交台账与业务记录在同一次原子写中发布；投影全部可从它重建。
        ledger = data.setdefault("commits", [])
        ledger.append(commit_entry)
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
            if records_published:
                # records 权威边界已发布：提交事实存在。尽力从权威边界
                # 重建投影，使同一进程内也能恢复一致；仍失败则交由启动
                # 恢复编排。业务事实不回滚、不重放。
                with suppress(Exception):
                    self.rebuild_projections()
            elif boundary_started and self._journal is not None:
                # records 未发布：没有已确认边界，把暂存事件隔离留证（不写入
                # journal），保证未提交投影不可读；活动标记由 finally 清除。
                with suppress(Exception):
                    self._journal.rollback_boundary(commit_sequence=commit_sequence)
            raise
        finally:
            self._clear_active_marker()
        return created, commit_sequence
