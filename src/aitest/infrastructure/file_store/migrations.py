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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from . import atomic
from .backup import FileBackupStore

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
    backward: Callable[[Path], str] | None


@dataclass(frozen=True, slots=True)
class PlanResult:
    plan_id: str
    steps: tuple[str, ...]
    path: Path


@dataclass(frozen=True, slots=True)
class ApplyReport:
    plan_id: str
    state: str  # applied | resumed | nothing_to_apply
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


def _remove_workspace_schema_version(root: Path) -> str:
    path = root / "workspace.json"
    if not path.exists():
        return "workspace.json 不存在，跳过"
    data = _load_json(path)
    if data.get("schema_version") != "1.0":
        return "schema_version 非本迁移引入值，保留"
    data.pop("schema_version")
    _write_json(path, data)
    return "移除 schema_version"


def _ensure_records_intents(root: Path) -> str:
    path = root / "records.json"
    if not path.exists():
        return "records.json 不存在，跳过"
    data = _load_json(path)
    if "intents" in data:
        return "intents 容器已存在"
    data["intents"] = {}
    _write_json(path, data)
    return "补充 intents={} 容器"


def _remove_records_intents(root: Path) -> str:
    path = root / "records.json"
    if not path.exists():
        return "records.json 不存在，跳过"
    data = _load_json(path)
    if data.get("intents") != {}:
        return "intents 非空或非本迁移引入，保留"
    data.pop("intents")
    _write_json(path, data)
    return "移除空 intents 容器"


_BUILT_IN: Final[dict[str, Migration]] = {
    migration.id: migration
    for migration in (
        Migration(
            id="0001-workspace-schema-version",
            description="确保 workspace.json 含 schema_version",
            reversible=True,
            forward=_ensure_workspace_schema_version,
            backward=_remove_workspace_schema_version,
        ),
        Migration(
            id="0002-records-intents-container",
            description="确保 records.json 含 intents 容器",
            reversible=True,
            forward=_ensure_records_intents,
            backward=_remove_records_intents,
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
        for path in (self._plans_dir, self._backups_dir):
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
        if not pending:
            return ApplyReport(
                plan_id=plan_id,
                state="nothing_to_apply",
                executed=(),
                skipped=tuple(steps),
                backup_path=None,
            )
        backup_path = self._ensure_backup(plan_id)
        executed: list[str] = []
        for migration_id in pending:
            migration = _BUILT_IN[migration_id]
            self._append_journal(plan_id, migration_id, "step_started")
            try:
                detail = migration.forward(self._root)
            except BaseException:
                self._append_journal(plan_id, migration_id, "step_failed")
                raise
            self._append_journal(plan_id, migration_id, f"step_succeeded: {detail}")
            applied = dict(applied)
            applied[migration_id] = {"plan_id": plan_id}
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
        reversible = [
            migration_id
            for migration_id in reversed(steps)
            if migration_id in applied
        ]
        for migration_id in reversible:
            migration = _BUILT_IN[migration_id]
            if not migration.reversible or migration.backward is None:
                raise MigrationError(f"迁移不可逆，回滚阻塞: {migration_id}")
        rolled_back: list[str] = []
        for migration_id in reversible:
            backward = _BUILT_IN[migration_id].backward
            assert backward is not None
            detail = backward(self._root)
            self._append_journal(plan_id, migration_id, f"rolled_back: {detail}")
            applied = dict(applied)
            applied.pop(migration_id)
            self._save_registry(applied=applied)
            rolled_back.append(migration_id)
        return RollbackReport(
            plan_id=plan_id,
            state="rolled_back" if rolled_back else "nothing_to_rollback",
            rolled_back=tuple(rolled_back),
        )

    # ----- 内部 -------------------------------------------------------

    def _load_plan(self, plan_id: str) -> tuple[str, ...]:
        path = self._plans_dir / f"{plan_id}.json"
        if not path.exists():
            raise MigrationError(f"计划不存在: {plan_id}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        steps = raw.get("steps")
        if not isinstance(steps, list) or not all(isinstance(step, str) for step in steps):
            raise MigrationError("计划内容损坏")
        return tuple(str(step) for step in steps)

    def _ensure_backup(self, plan_id: str) -> Path:
        destination = self._backups_dir / plan_id
        manifest = destination / "backup.json"
        if manifest.exists():
            return destination
        FileBackupStore(self._root).create(destination)
        return destination

    def _load_registry(self) -> dict[str, dict[str, str]]:
        raw = json.loads(self._registry_path.read_text(encoding="utf-8"))
        applied = raw.get("applied")
        if not isinstance(applied, dict):
            return {}
        return {str(key): dict(value) for key, value in applied.items() if isinstance(value, dict)}

    def _save_registry(self, *, applied: dict[str, dict[str, str]]) -> None:
        payload = {"schema": _REGISTRY_SCHEMA, "applied": applied}
        atomic.write_json(self._registry_path, payload)

    def _append_journal(self, plan_id: str, migration_id: str, event: str) -> None:
        entry = {"plan_id": plan_id, "migration_id": migration_id, "event": event}
        with self._journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _read_field(self, file_name: str, field: str) -> object:
        path = self._root / file_name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8")).get(field)
        except json.JSONDecodeError:
            return None


__all__ = [
    "ApplyReport",
    "FileMigrationManager",
    "Migration",
    "MigrationError",
    "PlanResult",
    "RollbackReport",
]
