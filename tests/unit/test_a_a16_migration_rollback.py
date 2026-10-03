import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.infrastructure.file_store.backup import FileBackupStore
from aitest.infrastructure.file_store.migrations import (
    FileMigrationManager,
    MigrationError,
)

_STEPS = (
    "0001-workspace-schema-version",
    "0002-records-intents-container",
)


def _workspace(root: Path, *, preexisting: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    workspace = {"workspace_id": "preexisting", "writer_epoch": 1}
    if preexisting:
        workspace["schema_version"] = "1.0"
    records: dict[str, object] = {"records": {}, "commit": 0}
    if preexisting:
        records["intents"] = {}
    (root / "workspace.json").write_text(
        json.dumps(workspace), encoding="utf-8"
    )
    (root / "records.json").write_text(json.dumps(records), encoding="utf-8")


def _apply_and_rollback(root: Path):
    manager = FileMigrationManager(root)
    plan = manager.plan(_STEPS)
    applied = manager.apply(plan.plan_id)
    assert applied.state == "applied"
    rolled = manager.rollback(plan.plan_id)
    assert rolled.state == "rolled_back"
    return manager, plan


def test_rollback_preserves_preexisting_fields(tmp_path: Path) -> None:
    _workspace(tmp_path, preexisting=True)
    _apply_and_rollback(tmp_path)
    workspace = json.loads((tmp_path / "workspace.json").read_text())
    records = json.loads((tmp_path / "records.json").read_text())
    # 迁移前已有的字段不是本步新增，回滚不得删除（A-16 探针形态）。
    assert workspace["schema_version"] == "1.0"
    assert records["intents"] == {}


def test_rollback_removes_fields_the_steps_actually_added(tmp_path: Path) -> None:
    _workspace(tmp_path, preexisting=False)
    _apply_and_rollback(tmp_path)
    workspace = json.loads((tmp_path / "workspace.json").read_text())
    records = json.loads((tmp_path / "records.json").read_text())
    assert "schema_version" not in workspace
    assert "intents" not in records


def test_rollback_blocked_after_new_business_writes(tmp_path: Path) -> None:
    _workspace(tmp_path, preexisting=False)
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(_STEPS)
    manager.apply(plan.plan_id)

    # 迁移后产生了新的业务提交（commit 水位前进）。
    records_path = tmp_path / "records.json"
    records = json.loads(records_path.read_text())
    records["commit"] = 1
    records["records"]["case"] = {"c1": [{"project_id": "p"}]}
    records_path.write_text(json.dumps(records), encoding="utf-8")

    with pytest.raises(MigrationError, match="新的业务写入"):
        manager.rollback(plan.plan_id)


def test_repeated_apply_is_nothing_to_do_and_backup_is_verifiable(
    tmp_path: Path,
) -> None:
    _workspace(tmp_path, preexisting=False)
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(_STEPS)
    first = manager.apply(plan.plan_id)
    assert first.state == "applied"
    backup = FileBackupStore(tmp_path)
    assert backup.verify(first.backup_path)["ok"]

    second = manager.apply(plan.plan_id)
    assert second.state == "nothing_to_apply"


def test_resume_after_partial_forward_failure(tmp_path: Path) -> None:
    _workspace(tmp_path, preexisting=False)
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(_STEPS)

    # 第一步成功后破坏 records.json，并移除第二步登记以模拟步骤间崩溃。
    manager.apply(plan.plan_id)
    registry_path = tmp_path / "migrations" / "registry.json"
    registry = json.loads(registry_path.read_text())
    registry["applied"].pop("0002-records-intents-container", None)
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    (tmp_path / "records.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        manager.resume(plan.plan_id)

    # 修复后 resume：只补跑第二步，第一步不重复执行（幂等 resume）。
    records = {"records": {}, "commit": 0, "intents": {}}
    (tmp_path / "records.json").write_text(json.dumps(records), encoding="utf-8")
    resumed = manager.resume(plan.plan_id)
    assert resumed.executed == ("0002-records-intents-container",)
