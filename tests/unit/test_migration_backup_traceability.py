"""迁移前强制备份的**可追溯性**与位置事实（架构 04 §9/§10/§140/§230、§197）。

分册原文：

- §9.4「迁移 inspect 检查实际结构与缺口，plan 给出转换范围；**apply 前验证备份并进入维护模式**，
  暂停新写动作」；
- §9.1/§10「备份…记录 workspace/generation/epoch/事件边界…最后生成带摘要的备份清单，
  校验成功才标可恢复」；
- §140/§230「备份…**位置独立于活动目录**」「备份位置**由用户配置**在独立于活动目录的存储上」；
- §197「维护记录给出检查范围、已完成项、错误类别、材料缺口与下一步」。

本文件锁定两件**已冻结**的事：①迁移前强制备份进入维护审计（可经 `backup_facts` 与
`backup inspect` 读回，含清单摘要/文件数/字节数，只登记**校验通过**的备份）；
②回执如实给出该备份**是否落在活动工作空间之外**，让"与活动目录同盘"这一风险可见
（位置口径是否同样适用于迁移前备份仍属
[待裁定](../docs/一期工程整改/05-待裁定/2026-10-13-剩余合同缺口与所需决定.md)第 5 项）。
"""

from __future__ import annotations

import json

from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.infrastructure.file_store.maintenance import FileMaintenanceService
from aitest.infrastructure.file_store.migrations import FileMigrationManager
from aitest.interfaces.local.api import EntryKind, Session

SESSION = Session("maintenance-fixture", EntryKind.HUMAN_UI, True)
LEGACY_MIGRATION = "0001-workspace-schema-version"


def _core(tmp_path):
    return assemble_workspace_core(tmp_path, instance_id="migration-backup-core")


def _make_one_migration_pending(tmp_path) -> None:
    """把工作空间退回旧格式：`workspace.json` 缺 `schema_version`。"""

    path = tmp_path / "workspace.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("schema_version", None)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_pre_migration_backup_is_verified_recorded_and_reported(tmp_path) -> None:
    core = _core(tmp_path)
    try:
        _make_one_migration_pending(tmp_path)
        maintenance = FileMaintenanceService(tmp_path)
        manager = FileMigrationManager(tmp_path, maintenance=maintenance)

        plan = manager.plan([LEGACY_MIGRATION])
        report = manager.apply(plan.plan_id)

        assert report.state == "applied"
        assert report.executed == (LEGACY_MIGRATION,)
        assert report.backup_path is not None
        # 冻结要求：apply 前必须有**可校验**备份（清单最后写入、校验通过）。
        manifest = report.backup_path / "backup.json"
        assert manifest.is_file()
        from aitest.infrastructure.file_store.backup import FileBackupStore

        assert FileBackupStore(tmp_path).verify(report.backup_path)["ok"] is True
        # 位置事实如实回执：当前实现落在活动工作空间内（不是"独立于活动目录"）。
        assert report.backup_outside_active_workspace is False

        # ① 维护审计登记：kind 标明是迁移前备份，且带清单摘要/文件数/字节数。
        facts = maintenance.backup_facts()
        migration_facts = [item for item in facts if item.get("kind") == "migration_pre_apply"]
        assert len(migration_facts) == 1
        fact = migration_facts[0]
        assert fact["destination"] == str(report.backup_path)
        assert fact["verified"] is True and fact["complete"] is True
        assert str(fact["manifest_digest"]).startswith("sha256:")
        assert fact["file_count"] >= 1 and fact["total_bytes"] > 0

        # ② 经既有只读读取路径可见：`backup inspect`（任何受控会话可用）。
        inspected = core.api.dispatch(
            Command(
                request_id="req-backup-inspect",
                action="backup",
                project_id="project-1",
                intent_id="intent-backup-inspect",
                expected_revision=0,
                parameters={"mode": "inspect"},
            ),
            SESSION,
        )
        assert inspected.error is None, inspected.error
        destinations = {item["destination"] for item in inspected.result["backups"]}
        assert str(report.backup_path) in destinations
        kinds = {item["kind"] for item in inspected.result["backups"]}
        assert "migration_pre_apply" in kinds
    finally:
        core.lifetime_lock.release()


def test_recorded_backup_matches_the_reported_location(tmp_path) -> None:
    """审计里的位置与回执一致，且可现场复核（`verify=True`）。"""

    core = _core(tmp_path)
    try:
        _make_one_migration_pending(tmp_path)
        maintenance = FileMaintenanceService(tmp_path)
        manager = FileMigrationManager(tmp_path, maintenance=maintenance)
        report = manager.apply(manager.plan([LEGACY_MIGRATION]).plan_id)

        inspected = core.api.dispatch(
            Command(
                request_id="req-backup-verify",
                action="backup",
                project_id="project-1",
                intent_id="intent-backup-verify",
                expected_revision=0,
                parameters={"mode": "inspect", "verify": True},
            ),
            SESSION,
        )
        assert inspected.error is None, inspected.error
        entry = next(
            item
            for item in inspected.result["backups"]
            if item["destination"] == str(report.backup_path)
        )
        assert entry["kind"] == "migration_pre_apply"
        assert entry["verified_at_creation"] is True
        assert entry["verified_now"] is True
        assert entry["exists"] is True
    finally:
        core.lifetime_lock.release()
