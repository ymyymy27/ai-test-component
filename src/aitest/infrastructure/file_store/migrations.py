"""备份及活动守卫后的 inspect/plan/apply/resume/rollback。

受控格式迁移框架。任何 ``apply`` 在执行首个迁移前强制创建可校验备份
（见 :mod:`aitest.infrastructure.file_store.backup`），迁移状态以注册表为
事实来源，审计事件追加到 JSONL。

存储布局（工作空间根下）::

    migrations/registry.json        # 已应用迁移状态（事实来源）
    migrations/plans/<token>.json    # 迁移计划
    migrations/backups/<token>/      # apply 前强制备份
    migrations/journal.jsonl        # 仅追加审计事件
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from uuid import uuid4

from . import atomic
from .backup import FileBackupStore
from .commit_manifest import FileCommitStore
from .commit_migration import migrate_commit_closure
from .index import migrate_query_layout, rollback_query_layout
from .maintenance import detect_activity_blocker
from .sharded_records import SCHEMA as RECORD_SCHEMA
from .sharded_records import migrate_to_shards, rollback_shards

#: 回滚逆向操作接收 apply 时记录的前态事实；空映射表示无记录的历史步骤。
Backward = Callable[[Path, Mapping[str, object]], str]

_REGISTRY_NAME: Final = "registry.json"
_JOURNAL_NAME: Final = "journal.jsonl"
_PLANS_DIR: Final = "plans"
_BACKUPS_DIR: Final = "backups"
_REGISTRY_SCHEMA: Final = "aitest.migration-registry/1.0"
_PLAN_SCHEMA: Final = "aitest.migration-plan/1.0"


class MigrationError(RuntimeError):
    """迁移合同违反（未知迁移、计划非法、不可逆回滚等）。"""


@dataclass(frozen=True, slots=True)
class Migration:
    """一个版本化迁移的前向/逆向定义。"""

    id: str
    description: str
    reversible: bool
    forward: Callable[[Path], str]
    backward: Backward | None
    #: 前向会修改的 JSON 对象文件；管理器据此在 apply 前后捕获顶层键集合，
    #: 回滚时只删除“本步真正新增”的键（A-16）。
    tracked_files: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class PlanResult:
    plan_id: str
    steps: tuple[str, ...]
    path: Path


@dataclass(frozen=True, slots=True)
class ApplyReport:
    plan_id: str
    state: str  # applied | resumed | nothing_to_apply | blocked
    executed: tuple[str, ...]
    skipped: tuple[str, ...]
    backup_path: Path | None


@dataclass(frozen=True, slots=True)
class RollbackReport:
    plan_id: str
    state: str
    rolled_back: tuple[str, ...]


# ----- 内置、真实、保守的格式归一化迁移 -------------------------------


def _load_json(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise MigrationError(f"{path.name} 顶层不是对象，拒绝迁移")
    return raw


def _write_json(path: Path, data: dict[str, object]) -> None:
    atomic.write_json(path, data)


def _ensure_workspace_schema_version(root: Path) -> str:
    path = root / "workspace.json"
    if not path.exists():
        return "workspace.json 不存在，跳过"
    data = _load_json(path)
    if "schema_version" in data:
        return "schema_version 已存在"
    data["schema_version"] = "1.0"
    _write_json(path, data)
    return "补充 schema_version=1.0"


def _step_added_keys(change: Mapping[str, object], file_name: str) -> set[str] | None:
    """从 apply 前态事实取本步对某文件真正新增的顶层键。

    返回 None 表示没有前态记录（历史注册表条目），调用方退回保守的值检查。
    """
    added = change.get("added_keys")
    if not isinstance(added, Mapping):
        return None
    raw = added.get(file_name)
    if not isinstance(raw, list):
        return None
    return {str(item) for item in raw}


def _remove_workspace_schema_version(root: Path, change: Mapping[str, object]) -> str:
    path = root / "workspace.json"
    if not path.exists():
        return "workspace.json 不存在，跳过"
    added = _step_added_keys(change, "workspace.json")
    if added is not None:
        if "schema_version" not in added:
            # 本步没有新增该字段（迁移前已存在），回滚绝不删除（A-16）。
            return "schema_version 非本步新增，保留"
    else:  # 历史注册表无事实：退回旧的值口径
        data = _load_json(path)
        if data.get("schema_version") != "1.0":
            return "schema_version 非本迁移引入值，保留"
    data = _load_json(path)
    data.pop("schema_version", None)
    _write_json(path, data)
    return "移除 schema_version"


def _ensure_records_intents(root: Path) -> str:
    path = root / "records.json"
    if not path.exists():
        return "records.json 不存在，跳过"
    data = _load_json(path)
    if data.get("schema") == RECORD_SCHEMA:
        return "分片意图容器已存在"
    if "intents" in data:
        return "intents 容器已存在"
    data["intents"] = {}
    _write_json(path, data)
    return "补充 intents={} 容器"


def _remove_records_intents(root: Path, change: Mapping[str, object]) -> str:
    path = root / "records.json"
    if not path.exists():
        return "records.json 不存在，跳过"
    added = _step_added_keys(change, "records.json")
    data = _load_json(path)
    if added is not None:
        if "intents" not in added:
            return "intents 容器非本步新增，保留"
    elif data.get("intents") != {}:
        return "intents 非空或非本迁移引入，保留"
    if data.get("intents") not in (None, {}):
        raise MigrationError("intents 中存在业务事实，拒绝删除")
    data.pop("intents", None)
    _write_json(path, data)
    return "移除空 intents 容器"


def _shard_records(root: Path) -> str:
    migrate_to_shards(root)
    return "发布可校验的不可变权威分片根"


def _unshard_records(root: Path, change: Mapping[str, object]) -> str:
    rollback_shards(root)
    return "恢复迁移前记录形态；分片永久保留"


def _restore_query_layout(root: Path, change: Mapping[str, object]) -> str:
    return rollback_query_layout(root)


def _upgrade_current_pointer(root: Path) -> str:
    return FileCommitStore(root).upgrade_current_pointer()


_BUILT_IN: Final[dict[str, Migration]] = {
    migration.id: migration
    for migration in (
        Migration(
            id="0001-workspace-schema-version",
            description="确保 workspace.json 含 schema_version",
            reversible=True,
            forward=_ensure_workspace_schema_version,
            backward=_remove_workspace_schema_version,
            tracked_files=("workspace.json",),
        ),
        Migration(
            id="0002-records-intents-container",
            description="确保 records.json 含 intents 容器",
            reversible=True,
            forward=_ensure_records_intents,
            backward=_remove_records_intents,
            tracked_files=("records.json",),
        ),
        Migration(
            id="0003-sharded-record-authority",
            description="将权威记录、意图和提交台账迁移为不可变分片",
            reversible=True,
            forward=_shard_records,
            backward=_unshard_records,
            tracked_files=("records.json",),
        ),
        Migration(
            id="0004-bounded-query-directory",
            description="将查询目录及报告/问题身份键账迁移为有界不可变树",
            reversible=True,
            forward=migrate_query_layout,
            backward=_restore_query_layout,
            tracked_files=("indexes.json",),
        ),
        Migration(
            id="0005-complete-commit-closure",
            description="核实并冻结同一提交的记录、查询目录和事件根",
            reversible=False,
            forward=migrate_commit_closure,
            backward=None,
            tracked_files=("current.json",),
        ),
        Migration(
            id="0006-canonical-current-publication",
            description="按存储主责合同发布七字段 current 并保留候选/前指针恢复材料",
            reversible=False,
            forward=_upgrade_current_pointer,
            backward=None,
            tracked_files=("current.json",),
        ),
    )
}


def _plan_token(steps: tuple[str, ...]) -> str:
    digest = hashlib.sha256("\u001f".join(steps).encode("utf-8")).hexdigest()[:10]
    return "plan-" + digest


class FileMigrationManager:
    """inspect/plan/apply/resume/rollback 的文件实现。"""

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root.resolve()
        self._dir = self._root / "migrations"
        self._registry_path = self._dir / _REGISTRY_NAME
        self._journal_path = self._dir / _JOURNAL_NAME
        self._plans_dir = self._dir / _PLANS_DIR
        self._backups_dir = self._dir / _BACKUPS_DIR
        self._steps_dir = self._dir / "steps"
        self._cycles_dir = self._dir / "cycles"
        for path in (self._plans_dir, self._backups_dir, self._steps_dir, self._cycles_dir):
            path.mkdir(parents=True, exist_ok=True)
        if not self._registry_path.exists():
            self._save_registry(applied={})

    # ----- inspect ----------------------------------------------------

    def inspect(self) -> dict[str, object]:
        """扫描当前文件版本、已应用迁移、可用迁移与漂移。"""
        registry = self._load_registry()
        applied_ids = set(registry)
        known_ids = set(_BUILT_IN)
        workspace_version = self._read_field("workspace.json", "schema_version")
        records_commit = self._read_field("records.json", "commit")
        plans = sorted(path.name for path in self._plans_dir.glob("*.json"))
        return {
            "workspace_schema_version": workspace_version,
            "records_commit": records_commit,
            "applied": sorted(applied_ids),
            "available": sorted(known_ids),
            "pending": sorted(known_ids - applied_ids),
            "drift": {
                "unknown_applied": sorted(applied_ids - known_ids),
            },
            "plans": plans,
        }

    # ----- plan -------------------------------------------------------

    def plan(self, migration_ids: tuple[str, ...] | list[str]) -> PlanResult:
        """校验并冻结一个有序迁移计划。"""
        if not migration_ids:
            raise MigrationError("plan 至少包含一个迁移")
        steps = tuple(migration_ids)
        if len(set(steps)) != len(steps):
            raise MigrationError("plan 中迁移必须唯一")
        ordered = sorted(steps)
        if list(steps) != ordered:
            raise MigrationError("plan 必须按迁移 id 升序")
        for migration_id in steps:
            if migration_id not in _BUILT_IN:
                raise MigrationError(f"未知迁移: {migration_id}")
        plan_id = _plan_token(steps)
        path = self._plans_dir / f"{plan_id}.json"
        payload = {
            "schema": _PLAN_SCHEMA,
            "plan_id": plan_id,
            "steps": list(steps),
            "descriptions": [_BUILT_IN[migration_id].description for migration_id in steps],
        }
        atomic.write_json(path, payload)
        return PlanResult(plan_id=plan_id, steps=steps, path=path)

    # ----- apply ------------------------------------------------------

    def apply(self, plan_id: str) -> ApplyReport:
        """按计划执行；已应用步骤跳过（天然支持 resume）。

        首个真实步骤执行前强制创建可校验备份。
        """
        steps = self._load_plan(plan_id)
        applied = self._load_registry()
        pending = [migration_id for migration_id in steps if migration_id not in applied]
        for migration_id in steps:
            state_path = self._steps_dir / plan_id / f"{migration_id}.json"
            if state_path.exists() and _load_json(state_path).get("state") == "rollback_pending":
                raise MigrationError("回滚尚未完成，请先继续 rollback，拒绝前向重放")
        if not pending:
            return ApplyReport(
                plan_id=plan_id,
                state="nothing_to_apply",
                executed=(),
                skipped=tuple(steps),
                backup_path=None,
            )
        # 活动执行门禁：存在未核实活动（活动标记或非空事件暂存）时绝不
        # 迁移；先由恢复编排核实/抢救活动执行（C 先核实活动并抢救，A 再判
        # 迁移/回收资格，A-07），再重新 apply。
        if detect_activity_blocker(self._root) is not None:
            return ApplyReport(
                plan_id=plan_id,
                state="blocked",
                executed=(),
                skipped=tuple(steps),
                backup_path=None,
            )
        self._records_watermark()  # 不把不可读业务水位当成空工作空间。
        backup_path = self._ensure_backup(plan_id)
        executed: list[str] = []
        for migration_id in pending:
            migration = _BUILT_IN[migration_id]
            state_path = self._steps_dir / plan_id / f"{migration_id}.json"
            previous = _load_json(state_path) if state_path.exists() else {}
            if previous.get("state") == "rolled_back":
                previous = {}
            if not previous:
                watermark = self._records_watermark()
                previous = {
                    "state": "prepared",
                    "plan_id": plan_id,
                    "before_keys": {
                        file_name: (
                            sorted(keys)
                            if (keys := self._json_top_keys(file_name)) is not None
                            else None
                        )
                        for file_name in migration.tracked_files
                    },
                    "records_commit_before": watermark,
                }
                # 写前日志必须先于 forward；重启沿用最初前态，不能重新采样。
                atomic.write_json(state_path, previous)
            before_keys = previous["before_keys"]
            if not isinstance(before_keys, dict):
                raise MigrationError("迁移前态损坏，拒绝继续")
            commit_before = previous.get("records_commit_before")
            self._append_journal(plan_id, migration_id, "step_started")
            try:
                detail = migration.forward(self._root)
            except BaseException:
                self._append_journal(plan_id, migration_id, "step_failed")
                raise
            after_keys = {
                file_name: self._json_top_keys(file_name) for file_name in migration.tracked_files
            }
            added_keys: dict[str, list[str]] = {}
            for file_name in migration.tracked_files:
                before_set = before_keys[file_name]
                after_set = after_keys[file_name]
                if before_set is not None and after_set is not None:
                    added_keys[file_name] = sorted(after_set - set(before_set))
            changed = any(added_keys.values())
            self._append_journal(plan_id, migration_id, f"step_succeeded: {detail}")
            applied = dict(applied)
            applied[migration_id] = {
                "plan_id": plan_id,
                "changed": changed,
                "added_keys": added_keys,
                "records_commit_before": commit_before,
            }
            self._save_registry(applied=applied)
            executed.append(migration_id)
        state = "resumed" if len(executed) < len(steps) else "applied"
        return ApplyReport(
            plan_id=plan_id,
            state=state,
            executed=tuple(executed),
            skipped=tuple(migration_id for migration_id in steps if migration_id not in executed),
            backup_path=backup_path,
        )

    # ----- resume -----------------------------------------------------

    def resume(self, plan_id: str) -> ApplyReport:
        """崩溃后续跑：状态来自注册表，apply 本身逐步骤幂等。"""
        self._load_plan(plan_id)  # 校验计划存在
        return self.apply(plan_id)

    # ----- rollback ---------------------------------------------------

    def rollback(self, plan_id: str) -> RollbackReport:
        """按计划逆序回滚；任一不可逆则整体阻塞（不执行任何回滚）。"""
        steps = self._load_plan(plan_id)
        applied = self._load_registry()
        if detect_activity_blocker(self._root) is not None:
            raise MigrationError("存在未核实活动，拒绝回滚")
        for migration_id in steps:
            path = self._steps_dir / plan_id / f"{migration_id}.json"
            if migration_id in applied or not path.exists():
                continue
            pending_state = _load_json(path)
            if pending_state.get("state") == "rollback_pending":
                applied[migration_id] = pending_state
                continue
            if pending_state.get("state") != "prepared":
                continue
            before = pending_state.get("before_keys")
            if not isinstance(before, dict):
                raise MigrationError("迁移前态损坏，拒绝回滚")
            added = {
                file_name: sorted((self._json_top_keys(file_name) or set()) - set(keys))
                for file_name, keys in before.items()
                if isinstance(keys, list)
            }
            applied[migration_id] = {**pending_state, "added_keys": added}
        reversible = [migration_id for migration_id in reversed(steps) if migration_id in applied]
        if FileCommitStore(self._root).read_current() is not None and any(
            migration_id < "0005-complete-commit-closure" for migration_id in reversible
        ):
            raise MigrationError("完整提交根依赖当前格式，拒绝独立回滚旧格式步骤")
        for migration_id in reversible:
            migration = _BUILT_IN[migration_id]
            if not migration.reversible or migration.backward is None:
                raise MigrationError(f"迁移不可逆，回滚阻塞: {migration_id}")
        # 回滚前有迁移后新写入时拒绝：格式回滚会破坏新写入依赖的结构，
        # 必须先核实并处置新业务事实（A-16）。
        current_commit = self._read_field("records.json", "commit")
        if isinstance(current_commit, int):
            watermarks = [
                watermark
                for migration_id in reversible
                for entry in (applied[migration_id],)
                if isinstance((watermark := entry.get("records_commit_before")), int)
            ]
            if watermarks and current_commit > min(watermarks):
                raise MigrationError("迁移后存在新的业务写入，拒绝回滚；请先核实并处置新事实")
        rolled_back: list[str] = []
        for migration_id in reversible:
            backward = _BUILT_IN[migration_id].backward
            assert backward is not None
            entry = applied.get(migration_id, {})
            state_path = self._steps_dir / plan_id / f"{migration_id}.json"
            # 原逆向输入先落盘；逆向或登记中断后继续同一次回滚。
            atomic.write_json(
                state_path, {**entry, "state": "rollback_pending", "plan_id": plan_id}
            )
            detail = backward(self._root, entry)
            self._append_journal(plan_id, migration_id, f"rolled_back: {detail}")
            applied = dict(applied)
            applied.pop(migration_id)
            self._save_registry(applied=applied)
            atomic.write_json(
                state_path,
                {"state": "rolled_back", "plan_id": plan_id},
            )
            rolled_back.append(migration_id)
        cycle_path = self._cycles_dir / f"{plan_id}.json"
        if cycle_path.exists() and all(migration_id not in applied for migration_id in steps):
            cycle = _load_json(cycle_path)
            atomic.write_json(cycle_path, {**cycle, "state": "rolled_back"})
        return RollbackReport(
            plan_id=plan_id,
            state="rolled_back" if rolled_back else "nothing_to_rollback",
            rolled_back=tuple(rolled_back),
        )

    # ----- 内部 -------------------------------------------------------

    def _load_plan(self, plan_id: str) -> tuple[str, ...]:
        if (
            not plan_id.startswith("plan-")
            or len(plan_id) != 15
            or any(character not in "0123456789abcdef" for character in plan_id[5:])
        ):
            raise MigrationError("计划标识非法")
        path = self._plans_dir / f"{plan_id}.json"
        if not path.exists():
            raise MigrationError(f"计划不存在: {plan_id}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        steps = raw.get("steps")
        if not isinstance(steps, list) or not all(isinstance(step, str) for step in steps):
            raise MigrationError("计划内容损坏")
        frozen_steps = tuple(str(step) for step in steps)
        if (
            not frozen_steps
            or list(frozen_steps) != sorted(set(frozen_steps))
            or any(step not in _BUILT_IN for step in frozen_steps)
            or _plan_token(frozen_steps) != plan_id
        ):
            raise MigrationError("计划内容与冻结标识不一致")
        return frozen_steps

    def _ensure_backup(self, plan_id: str) -> Path:
        cycle_path = self._cycles_dir / f"{plan_id}.json"
        cycle = _load_json(cycle_path) if cycle_path.exists() else {}
        if cycle and (
            cycle.get("plan_id") != plan_id or cycle.get("state") not in {"active", "rolled_back"}
        ):
            raise MigrationError("迁移周期状态损坏，拒绝沿用未知前态")
        destination = self._backups_dir / plan_id
        states = list((self._steps_dir / plan_id).glob("*.json"))
        if cycle.get("state") == "active":
            name = cycle.get("backup_name")
            if not isinstance(name, str) or Path(name).name != name:
                raise MigrationError("迁移备份引用损坏")
            destination = self._backups_dir / name
        elif cycle.get("state") == "rolled_back" or (
            states and all(_load_json(path).get("state") == "rolled_back" for path in states)
        ):
            # 新一轮迁移留独立备份，不覆盖旧备份也不沿用过时前态。
            destination = self._backups_dir / f"{plan_id}-{uuid4().hex}"
        manifest = destination / "backup.json"
        if manifest.exists():
            # 复用既有备份前必须重新核对，绝不信任未校验副本。
            verification = FileBackupStore(self._root).verify(destination)
            if not verification["ok"]:
                raise MigrationError(f"复用备份校验失败: {verification['errors']}")
        else:
            FileBackupStore(self._root).create(destination)
            verification = FileBackupStore(self._root).verify(destination)
            if not verification["ok"]:
                raise MigrationError(f"迁移前备份校验失败: {verification['errors']}")
        # 在任何 forward 之前冻结本轮备份引用，重启不能切回第一轮备份。
        atomic.write_json(
            cycle_path,
            {"plan_id": plan_id, "state": "active", "backup_name": destination.name},
        )
        return destination

    def _load_registry(self) -> dict[str, dict[str, object]]:
        raw = json.loads(self._registry_path.read_text(encoding="utf-8"))
        applied = raw.get("applied")
        if not isinstance(applied, dict):
            raise MigrationError("迁移注册表损坏，拒绝猜测已执行状态")
        if any(not isinstance(value, dict) for value in applied.values()):
            raise MigrationError("迁移步骤状态损坏，拒绝重放")
        return {str(key): dict(value) for key, value in applied.items()}

    def _save_registry(self, *, applied: dict[str, dict[str, object]]) -> None:
        payload = {"schema": _REGISTRY_SCHEMA, "applied": applied}
        atomic.write_json(self._registry_path, payload)

    def _json_top_keys(self, file_name: str) -> set[str] | None:
        """迁移跟踪文件的顶层键集合；文件不存在返回 None。"""
        path = self._root / file_name
        if not path.exists():
            return None
        data = _load_json(path)
        return set(data)

    def _append_journal(self, plan_id: str, migration_id: str, event: str) -> None:
        entry = {"plan_id": plan_id, "migration_id": migration_id, "event": event}
        with self._journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _read_field(self, file_name: str, field: str) -> object:
        if file_name == "records.json" and field == "commit":
            current = FileCommitStore(self._root).read_current()
            if current is not None:
                return current["manifest"]["commit_sequence"]
        path = self._root / file_name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8")).get(field)
        except json.JSONDecodeError:
            return None

    def _records_watermark(self) -> int:
        current = FileCommitStore(self._root).read_current()
        if current is not None:
            return int(current["manifest"]["commit_sequence"])
        path = self._root / "records.json"
        if not path.exists():
            return 0
        try:
            data = _load_json(path)
        except (OSError, ValueError) as error:
            raise MigrationError("权威业务提交水位不可读取，拒绝迁移") from error
        if "commit" not in data and not data.get("records"):
            return 0
        value = data.get("commit")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MigrationError("权威业务提交水位损坏，拒绝迁移")
        return value


__all__ = [
    "ApplyReport",
    "FileMigrationManager",
    "Migration",
    "MigrationError",
    "PlanResult",
    "RollbackReport",
]
