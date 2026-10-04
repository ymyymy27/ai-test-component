"""提交边界、快照游标、事件续读与重复事件去重。

正式事件日志，信封为 ``aitest.event/2.0``（见
``aitest.contracts.events.Event``）。

存储布局（均位于工作空间根下）::

    event-log/journal.jsonl          # 仅含已提交事件，每行一个 Event，序列连续
    event-log/staging/<commit>.jsonl # 事务边界内暂存事件
    event-log/boundaries/<commit>.json  # 已提交边界标记
    event-log/position.json          # 当前位置（最后序列/提交/event_id）

设计要点：

- 事件先写入边界暂存文件；``commit_boundary`` 才追加进 journal，随后写
  边界标记。因此 journal 中只存在已提交事件，读取无需按状态过滤。
- ``event_id`` 由事件身份元组确定性派生，同一事务重放（重试/恢复）产生
  相同 id；边界内重复追加按幂等处理，不产生第二个事件。
- journal 以单 ``write`` 追加并 fsync；崩溃只可能损坏尾部。打开时尾部
  损坏行被隔离到 ``quarantine/`` 并截断；历史行损坏则报
  ``maintenance_required``，绝不静默截断历史。
- 游标为不透明 token，编码最后读取序列，支持快照后续读（事件续读）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from aitest.contracts.events import Event

_JOURNAL_NAME: Final = "journal.jsonl"
_POSITION_NAME: Final = "position.json"
_STAGING_DIR: Final = "staging"
_BOUNDARY_DIR: Final = "boundaries"
_QUARANTINE_DIR: Final = "quarantine"
_POSITION_SCHEMA: Final = "aitest.event-position/1.0"
_BOUNDARY_SCHEMA: Final = "aitest.event-boundary/1.0"
_DEFAULT_LIMIT: Final = 100
_MAX_LIMIT: Final = 500

ReadStatus = Literal["ok", "maintenance_required", "invalid_cursor"]


class EventMaintenanceRequired(RuntimeError):
    """历史区损坏，无法在不丢历史的前提下继续读取或追加。"""


@dataclass(frozen=True, slots=True)
class BoundaryHandle:
    """一次活动事件边界的定位信息。"""

    commit_sequence: int
    start_sequence: int


@dataclass(frozen=True, slots=True)
class EventReadResult:
    """事件续读结果；游标非法或需维护时返回结构化状态。"""

    status: ReadStatus
    events: tuple[Event, ...] = ()
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """恢复核对结论；未知副作用只登记不自动重放。"""

    repaired_tail_lines: int
    completed_boundaries: tuple[int, ...]
    orphaned_staging: tuple[int, ...]
    actions: tuple[str, ...]


def encode_cursor(sequence: int) -> str:
    """把序列编码为不透明游标 token。"""
    raw = json.dumps({"s": sequence}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(token: str) -> int:
    """解码游标；非法或非当前日志代次抛 ValueError。"""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid event cursor") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("s"), int):
        raise ValueError("invalid event cursor")
    sequence = int(payload["s"])
    if sequence < 0:
        raise ValueError("invalid event cursor")
    return sequence


def derive_event_id(
    *,
    instance_id: str,
    commit_sequence: int,
    event_type: str,
    project_id: str,
    aggregate_kind: str = "",
    record_id: str,
    revision: int,
) -> str:
    """由事件身份元组确定性派生 event_id，供重放幂等。

    aggregate_kind 必须进入身份（A-17）：rule_draft/x@1 与
    rule_version/x@1 是不同聚合的事件，不能因 record_id/revision 相同
    而在暂存去重时互相吞掉。
    """
    identity = "\u001f".join(
        [
            instance_id,
            str(commit_sequence),
            event_type,
            project_id,
            aggregate_kind,
            record_id,
            str(revision),
        ]
    )
    return "evt_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


class FileEventJournal:
    """基于文件的正式追加事件日志。"""

    @property
    def instance_id(self) -> str:
        return self._instance_id

    def __init__(self, workspace_root: Path, *, instance_id: str) -> None:
        if not instance_id:
            raise ValueError("instance_id is required")
        from .commit_manifest import FileCommitStore

        FileCommitStore.reject_links(workspace_root)
        self._root = workspace_root.resolve()
        self._instance_id = instance_id
        self._dir = self._root / "event-log"
        self._journal = self._dir / _JOURNAL_NAME
        self._position_path = self._dir / _POSITION_NAME
        self._staging_dir = self._dir / _STAGING_DIR
        self._boundary_dir = self._dir / _BOUNDARY_DIR
        self._quarantine_dir = self._dir / _QUARANTINE_DIR
        if FileCommitStore(self._root).read_current() is not None:
            self._repaired_tail_lines = 0
            return
        for path in (self._staging_dir, self._boundary_dir, self._quarantine_dir):
            FileCommitStore.reject_links(path)
            path.mkdir(parents=True, exist_ok=True)
        if not self._journal.exists():
            self._journal.touch()
        self._repaired_tail_lines = (
            0 if FileCommitStore(self._root).read_current() is not None else self._repair_tail()
        )

    # ----- 公开读能力 -------------------------------------------------

    def health(self) -> dict[str, object]:
        """日志健康与当前位置摘要。"""
        from .commit_manifest import FileCommitStore

        current = FileCommitStore(self._root).read_current()
        position = (
            current["manifest"]["event_root"] if current is not None else self._load_position()
        )
        return {
            "ok": True,
            "instance_id": self._instance_id,
            "last_sequence": position["last_sequence"],
            "last_commit_sequence": position["last_commit_sequence"],
            "repaired_tail_lines": self._repaired_tail_lines,
        }

    def read(self, *, cursor: str | None = None, limit: int = _DEFAULT_LIMIT) -> EventReadResult:
        """从游标后续读事件；cursor=None 从头开始。"""
        from .commit_manifest import CommitMaterialError, FileCommitStore
        from .ordered_events import OrderedEventStore

        try:
            if FileCommitStore(self._root).read_current() is not None:
                return OrderedEventStore(self._root).read(cursor=cursor, limit=limit)
        except CommitMaterialError:
            return EventReadResult(status="maintenance_required")
        after_sequence = 0
        if cursor is not None:
            try:
                after_sequence = decode_cursor(cursor)
            except ValueError:
                return EventReadResult(status="invalid_cursor")
        bounded = max(1, min(limit, _MAX_LIMIT))
        try:
            events = self._parse_journal()
        except EventMaintenanceRequired:
            return EventReadResult(status="maintenance_required")
        later = (event for event in events if event.event_sequence > after_sequence)
        selected = tuple(later)[:bounded]
        if not selected:
            return EventReadResult(status="ok", next_cursor=None)
        return EventReadResult(
            status="ok",
            events=selected,
            next_cursor=encode_cursor(selected[-1].event_sequence),
        )

    def snapshot_cursor(self, *, commit_sequence: int) -> str | None:
        """返回某已提交边界末尾的游标，供快照后续读；边界不存在返回 None。"""
        from .commit_manifest import FileCommitStore
        from .ordered_events import OrderedEventStore

        if FileCommitStore(self._root).read_current() is not None:
            return OrderedEventStore(self._root).snapshot_cursor(commit_sequence=commit_sequence)
        marker = self._load_boundary(commit_sequence)
        if marker is None:
            return None
        last_sequence = marker["last_sequence"]
        assert isinstance(last_sequence, int)
        return encode_cursor(last_sequence)

    # ----- 边界写能力 -------------------------------------------------

    def begin_boundary(
        self,
        *,
        commit_sequence: int,
        request_id: str | None,
        intent_id: str | None,
        workspace_id: str,
        project_id: str,
        writer_epoch: int,
    ) -> BoundaryHandle:
        """开启一个事件边界；已存在标记的边界禁止重开（幂等提交走 commit）。"""
        from .commit_manifest import FileCommitStore

        if FileCommitStore(self._root).read_current() is not None:
            raise ValueError("event publication must use the shared commit unit")
        if commit_sequence < 1:
            raise ValueError("commit_sequence must be >= 1")
        if self._load_boundary(commit_sequence) is not None:
            raise ValueError(f"event boundary already committed: {commit_sequence}")
        staging = self._staging_path(commit_sequence)
        if staging.exists() and staging.stat().st_size > 0:
            raise ValueError(f"event boundary already started: {commit_sequence}")
        position = self._load_position()
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.touch()
        return BoundaryHandle(
            commit_sequence=commit_sequence,
            start_sequence=int(position["last_sequence"]),
        )

    def record_event(
        self,
        *,
        commit_sequence: int,
        event_type: str,
        project_id: str,
        record_id: str,
        revision: int,
        request_id: str | None,
        intent_id: str | None,
        workspace_id: str,
        writer_epoch: int,
        aggregate_kind: str = "",
    ) -> Event:
        """在边界内记录事件；同一事件重复追加返回既有事件（幂等）。"""
        staging = self._staging_path(commit_sequence)
        if not staging.exists():
            raise ValueError(f"event boundary not active: {commit_sequence}")
        event_id = derive_event_id(
            instance_id=self._instance_id,
            commit_sequence=commit_sequence,
            event_type=event_type,
            project_id=project_id,
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            revision=revision,
        )
        existing = self._find_in_staging(staging, event_id)
        if existing is not None:
            return existing
        next_sequence = int(self._load_position()["last_sequence"]) + self._count_lines(staging) + 1
        event = Event(
            event_id=event_id,
            request_id=request_id,
            intent_id=intent_id,
            instance_id=self._instance_id,
            workspace_id=workspace_id,
            writer_epoch=writer_epoch,
            commit_sequence=commit_sequence,
            event_sequence=next_sequence,
            project_id=project_id,
            record_id=record_id,
            revision=revision,
            event_type=event_type,
        )
        self._append_line(staging, self._serialize(event))
        return event

    def commit_boundary(
        self,
        *,
        commit_sequence: int,
    ) -> dict[str, object]:
        """把暂存事件追加进 journal 并写边界标记；重复提交幂等。"""
        marker = self._load_boundary(commit_sequence)
        staging = self._staging_path(commit_sequence)
        if marker is not None:
            self._unlink_quiet(staging)
            return {"state": "already_committed", "commit_sequence": commit_sequence}
        if not staging.exists():
            raise ValueError(f"event boundary not active: {commit_sequence}")
        staged_events = self._read_staging(staging)
        payload = staging.read_bytes()
        if payload:
            with self._journal.open("ab") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        first_sequence = staged_events[0].event_sequence if staged_events else None
        last_sequence = staged_events[-1].event_sequence if staged_events else None
        marker_payload = {
            "schema": _BOUNDARY_SCHEMA,
            "commit_sequence": commit_sequence,
            "first_sequence": first_sequence,
            "last_sequence": last_sequence,
            "event_ids": [event.event_id for event in staged_events],
            "state": "committed",
        }
        self._write_boundary(commit_sequence, marker_payload)
        if last_sequence is None:
            last_sequence = int(self._load_position()["last_sequence"])
        self._save_position(
            last_sequence=last_sequence,
            last_commit_sequence=commit_sequence,
            last_event_id=staged_events[-1].event_id if staged_events else None,
        )
        self._unlink_quiet(staging)
        return {
            "state": "committed",
            "commit_sequence": commit_sequence,
            "events": len(staged_events),
        }

    def rollback_boundary(self, *, commit_sequence: int) -> dict[str, object]:
        """放弃未提交边界；暂存事件隔离保留以备核查，不写入 journal。"""
        staging = self._staging_path(commit_sequence)
        if self._load_boundary(commit_sequence) is not None:
            raise ValueError(f"event boundary already committed: {commit_sequence}")
        retained = 0
        if staging.exists() and staging.stat().st_size > 0:
            retained = self._count_lines(staging)
            quarantine = self._quarantine_dir / f"staging-{commit_sequence}.jsonl"
            os.replace(staging, quarantine)
        else:
            self._unlink_quiet(staging)
        return {
            "state": "rolled_back",
            "commit_sequence": commit_sequence,
            "retained_events": retained,
        }

    # ----- 恢复核对 ---------------------------------------------------

    def reconcile(self, *, committed_sequences: set[int]) -> ReconcileReport:
        """崩溃后核对：修复尾行、补提交已确认边界、登记孤立暂存。

        序号仅为查找提示；发布必须逐项核对真实业务权威提交与记录。
        """
        from .locking import writer_lock

        with writer_lock(self._root / "writer.lock", reentrant=True):
            return self._reconcile_locked(committed_sequences=committed_sequences)

    def _reconcile_locked(self, *, committed_sequences: set[int]) -> ReconcileReport:
        from .commit_manifest import FileCommitStore, canonical_bytes
        from .records import FileRecordRepository

        if FileCommitStore(self._root).read_current(verify_material=True) is not None:
            return ReconcileReport(
                repaired_tail_lines=0,
                completed_boundaries=(),
                orphaned_staging=(),
                actions=(),
            )
        if any(type(sequence) is not int or sequence < 1 for sequence in committed_sequences):
            raise EventMaintenanceRequired("recovery sequence hints are invalid")
        store = FileCommitStore(self._root)
        legacy_authority = self._root / "records.json"
        store.reject_links(legacy_authority)
        if legacy_authority.exists():
            store._decode(store._read_bytes(legacy_authority, 64 * 1024 * 1024))
        authority = {}
        for entry in FileRecordRepository(self._root).authoritative_commits():
            sequence = entry.get("commit_sequence")
            if (
                type(sequence) is not int
                or sequence < 1
                or sequence in authority
                or entry.get("state") != "committed"
            ):
                raise EventMaintenanceRequired("business commit authority cannot be verified")
            authority[sequence] = entry
        candidates = []
        orphaned: list[int] = []
        for staging in sorted(self._staging_dir.glob("*.jsonl")):
            commit_sequence = self._commit_from_staging_name(staging.name)
            if commit_sequence not in committed_sequences or commit_sequence not in authority:
                orphaned.append(commit_sequence)
                continue
            events = self._read_staging(staging)
            self._verify_recovery_events(authority[commit_sequence], events)
            boundary = self._load_boundary(commit_sequence)
            if boundary is not None:
                expected = {
                    "schema": _BOUNDARY_SCHEMA,
                    "commit_sequence": commit_sequence,
                    "first_sequence": events[0].event_sequence,
                    "last_sequence": events[-1].event_sequence,
                    "event_ids": [event.event_id for event in events],
                    "state": "committed",
                }
                if canonical_bytes(boundary) != canonical_bytes(expected):
                    raise EventMaintenanceRequired("saved boundary does not match committed events")
            candidates.append((staging, commit_sequence, boundary is not None, events))
        actions: list[str] = []
        repaired = self._repair_tail()
        if repaired:
            actions.append(f"repaired {repaired} torn journal tail line(s)")
        completed: list[int] = []
        for staging, commit_sequence, had_boundary, events in candidates:
            self._merge_staging_after_crash(staging, commit_sequence, verified_events=events)
            if had_boundary:
                actions.append(f"removed staging for already committed boundary {commit_sequence}")
            else:
                completed.append(commit_sequence)
                actions.append(f"completed boundary {commit_sequence} after crash")
        for commit_sequence in orphaned:
            actions.append(
                f"orphaned staging for boundary {commit_sequence}; left for human recovery"
            )
        self._rebuild_position_from_journal(actions)
        return ReconcileReport(
            repaired_tail_lines=repaired,
            completed_boundaries=tuple(completed),
            orphaned_staging=tuple(orphaned),
            actions=tuple(actions),
        )

    def _verify_recovery_events(self, entry: dict[str, object], events: tuple[Event, ...]) -> None:
        from aitest.infrastructure.security import guard_value

        from .records import FileRecordRepository

        created = entry.get("created")
        if (
            not isinstance(created, list)
            or not created
            or len(created) != len(events)
            or type(entry.get("writer_epoch")) is not int
            or any(
                not isinstance(entry.get(key), str) or not str(entry[key]).strip()
                for key in ("workspace_id", "project_id")
            )
        ):
            raise EventMaintenanceRequired("committed event set or ownership is unverified")
        repository = FileRecordRepository(self._root)
        for event, reference in zip(events, created, strict=True):
            payload = event.model_dump(mode="json")
            safe, changed = guard_value(payload)
            if changed or safe != payload:
                raise EventMaintenanceRequired("staged event identity cannot be safely preserved")
            if (
                not isinstance(reference, dict)
                or type(reference.get("revision")) is not int
                or not isinstance(reference.get("aggregate_kind"), str)
                or not isinstance(reference.get("record_id"), str)
                or not event.instance_id
                or event.event_type != "record_created"
                or any(
                    getattr(event, key) != entry.get(key)
                    for key in (
                        "request_id",
                        "intent_id",
                        "project_id",
                        "workspace_id",
                        "writer_epoch",
                        "commit_sequence",
                    )
                )
                or event.record_id != reference["record_id"]
                or event.revision != reference["revision"]
            ):
                raise EventMaintenanceRequired("staged event does not match business authority")
            try:
                record = repository.read(
                    aggregate_kind=reference["aggregate_kind"],
                    record_id=reference["record_id"],
                    revision=reference["revision"],
                )
            except (ValueError, KeyError, TypeError, OSError) as error:
                raise EventMaintenanceRequired("committed event record is unavailable") from error
            owner = record.payload.get("project_id", record.payload.get("local_project_id"))
            if owner != event.project_id or event.event_id != derive_event_id(
                instance_id=event.instance_id,
                commit_sequence=event.commit_sequence,
                event_type=event.event_type,
                project_id=event.project_id,
                aggregate_kind=reference["aggregate_kind"],
                record_id=event.record_id,
                revision=event.revision,
            ):
                raise EventMaintenanceRequired(
                    "committed record ownership or event id is unverified"
                )

    # ----- 内部：journal 解析与尾部修复 -------------------------------

    def _parse_journal(self) -> tuple[Event, ...]:
        if not self._journal.exists():
            return ()
        events: list[Event] = []
        lines = self._journal.read_bytes().splitlines()
        for index, raw_line in enumerate(lines):
            if not raw_line.strip():
                continue
            try:
                events.append(self._deserialize(raw_line))
            except (ValueError, json.JSONDecodeError):
                if index != len(lines) - 1:
                    raise EventMaintenanceRequired("corrupt event in committed history") from None
                raise EventMaintenanceRequired("torn journal tail requires repair") from None
        return tuple(events)

    def _repair_tail(self) -> int:
        """隔离并截断损坏的尾部行；返回修复行数。"""
        if not self._journal.exists() or self._journal.stat().st_size == 0:
            return 0
        lines = self._journal.read_bytes().splitlines()
        if not lines:
            return 0
        last_index = len(lines) - 1
        while last_index >= 0 and not lines[last_index].strip():
            last_index -= 1
        if last_index < 0:
            return 0
        try:
            self._deserialize(lines[last_index])
        except (ValueError, json.JSONDecodeError):
            quarantine = self._quarantine_dir / f"torn-tail-{os.getpid()}.jsonl"
            quarantine.write_bytes(lines[last_index] + b"\n")
            kept = b"".join(line + b"\n" for line in lines[:last_index])
            self._journal.write_bytes(kept)
            return 1
        return 0

    # ----- 内部：暂存 -------------------------------------------------

    def _staging_path(self, commit_sequence: int) -> Path:
        return self._staging_dir / f"{commit_sequence}.jsonl"

    @staticmethod
    def _commit_from_staging_name(name: str) -> int:
        return int(name.removesuffix(".jsonl"))

    def _read_staging(self, staging: Path) -> tuple[Event, ...]:
        from .commit_manifest import FileCommitStore

        store = FileCommitStore(self._root)
        events: list[Event] = []
        for raw_line in store._read_bytes(staging, 64 * 1024 * 1024).splitlines():
            if raw_line.strip():
                events.append(Event.model_validate(store._decode(raw_line)))
        return tuple(events)

    def _find_in_staging(self, staging: Path, event_id: str) -> Event | None:
        for event in self._read_staging(staging):
            if event.event_id == event_id:
                return event
        return None

    def _merge_staging_after_crash(
        self, staging: Path, commit_sequence: int, *, verified_events: tuple[Event, ...]
    ) -> None:
        """journal 已有该提交的事件时去重，否则补追加，最后写标记。"""
        journal_events = self._parse_journal()
        staged_events = self._read_staging(staging)
        if staged_events != verified_events:
            raise EventMaintenanceRequired("staged events changed after authority verification")
        existing: dict[int, Event] = {}
        identities: set[str] = set()
        last_sequence = 0
        for event in journal_events:
            if event.event_sequence != last_sequence + 1 or event.event_id in identities:
                raise EventMaintenanceRequired("journal event identity or range cannot be verified")
            existing[event.event_sequence] = event
            identities.add(event.event_id)
            last_sequence = event.event_sequence
        missing: list[Event] = []
        previous_staged = None
        for event in staged_events:
            if (
                event.commit_sequence != commit_sequence
                or previous_staged is not None
                and event.event_sequence != previous_staged + 1
            ):
                raise EventMaintenanceRequired("staged boundary range cannot be verified")
            previous_staged = event.event_sequence
            saved = existing.get(event.event_sequence)
            if saved is not None:
                if saved != event:
                    raise EventMaintenanceRequired(
                        "same event sequence has different saved material"
                    )
                continue
            if event.event_sequence != last_sequence + 1 or event.event_id in identities:
                raise EventMaintenanceRequired("staged event cannot extend the saved range")
            missing.append(event)
            identities.add(event.event_id)
            last_sequence = event.event_sequence
        if missing:
            with self._journal.open("ab") as handle:
                for event in missing:
                    handle.write(self._serialize(event))
                handle.flush()
                os.fsync(handle.fileno())
        marker_payload = {
            "schema": _BOUNDARY_SCHEMA,
            "commit_sequence": commit_sequence,
            "first_sequence": staged_events[0].event_sequence if staged_events else None,
            "last_sequence": staged_events[-1].event_sequence if staged_events else None,
            "event_ids": [event.event_id for event in staged_events],
            "state": "committed",
        }
        self._write_boundary(commit_sequence, marker_payload)
        self._unlink_quiet(staging)

    # ----- 内部：边界标记与位置 ---------------------------------------

    def _load_boundary(self, commit_sequence: int) -> dict[str, object] | None:
        from .commit_manifest import FileCommitStore

        store = FileCommitStore(self._root)
        path = self._boundary_dir / f"{commit_sequence}.json"
        store.reject_links(path)
        if not path.exists():
            return None
        return store._decode(store._read_bytes(path, 64 * 1024))

    def _write_boundary(self, commit_sequence: int, payload: dict[str, object]) -> None:
        path = self._boundary_dir / f"{commit_sequence}.json"
        self._atomic_write_json(path, payload)

    def _load_position(self) -> dict[str, int]:
        if not self._position_path.exists():
            return {"last_sequence": 0, "last_commit_sequence": 0}
        raw = json.loads(self._position_path.read_text(encoding="utf-8"))
        return {
            "last_sequence": int(raw.get("last_sequence", 0)),
            "last_commit_sequence": int(raw.get("last_commit_sequence", 0)),
        }

    def _save_position(
        self,
        *,
        last_sequence: int,
        last_commit_sequence: int,
        last_event_id: str | None,
    ) -> None:
        payload = {
            "schema": _POSITION_SCHEMA,
            "last_sequence": last_sequence,
            "last_commit_sequence": last_commit_sequence,
            "last_event_id": last_event_id,
        }
        self._atomic_write_json(self._position_path, payload)

    def _rebuild_position_from_journal(self, actions: list[str]) -> None:
        events = self._parse_journal()
        if not events:
            return
        last = events[-1]
        current = self._load_position()
        # 幂等：位置已与 journal 末尾一致时不重写、不登记修复动作，
        # 否则健康工作空间每次恢复都会被误报为 repaired。
        if (
            current["last_sequence"] == last.event_sequence
            and current["last_commit_sequence"] == last.commit_sequence
        ):
            return
        self._save_position(
            last_sequence=last.event_sequence,
            last_commit_sequence=last.commit_sequence,
            last_event_id=last.event_id,
        )
        actions.append("rebuilt position from journal")

    # ----- 内部：序列化与 IO 小工具 -----------------------------------

    @staticmethod
    def _serialize(event: Event) -> bytes:
        return (
            json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")

    @staticmethod
    def _deserialize(raw_line: bytes) -> Event:
        return Event.model_validate(json.loads(raw_line.decode("utf-8")))

    @staticmethod
    def _append_line(path: Path, line: bytes) -> None:
        with path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _count_lines(path: Path) -> int:
        return sum(1 for line in path.read_bytes().splitlines() if line.strip())

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)

    @staticmethod
    def _unlink_quiet(path: Path) -> None:
        with suppress(FileNotFoundError):
            path.unlink()


__all__ = [
    "BoundaryHandle",
    "EventMaintenanceRequired",
    "EventReadResult",
    "FileEventJournal",
    "ReconcileReport",
    "decode_cursor",
    "derive_event_id",
    "encode_cursor",
]
