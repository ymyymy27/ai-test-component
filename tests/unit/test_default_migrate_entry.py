"""受控结构迁移入口（`migrate`）与维护窗口（架构 04 §6/§9/§11、P1-FR16）。

报告点名「backup/migrate 虽有底层组件，工作空间级受控写身份与产品入口尚未完成」。
底层 `FileMigrationManager` 已实现五个子命令与硬门禁（apply 前必须有可校验备份、
活动未核实不得迁移、不可逆回滚整体拒绝）；本文件锁定**入口**补上的部分：

- `inspect` 只读、任何受控会话可用；
- `plan`／`apply`／`resume`／`rollback` 必须**受控交互会话**且必须在**维护窗口**内；
- 维护窗口期间**新的写动作被拒绝**（`maintenance_required`），只读动作与维护动作本身
  继续可用（架构 04 §142「进入维护状态暂停新写入」）；
- 迁移失败按 `MIGRATION_FAILED` 结构化拒绝，不冒泡成内部错误；
- 不在入口重造备份/活动门禁（那是存储组件已实现的合同规则）。

真实主机、真实掉电与真实迁移窗口未验收。
"""

from __future__ import annotations

import pathlib
import shutil
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest

from aitest.application.maintenance.migration import (
    MIGRATE_MODES,
    WRITE_MODES,
    MigrationBlocked,
    SavedMigrationEntry,
)
from aitest.application.maintenance.mode import MaintenanceMode, MaintenanceModeActive
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session

PROJECT = "project-1"


class _Actors:
    def __init__(self, *, interactive: bool) -> None:
        self._actor = SimpleNamespace(interactive=interactive)

    def current(self) -> Any:
        return self._actor


class _Runner:
    """`MigrationRunnerPort` 替身：返回**真实结果类型**并记录调用，可按需失败。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.failure: Exception | None = None

    def _maybe_fail(self) -> None:
        if self.failure is not None:
            raise self.failure

    def inspect(self) -> dict[str, object]:
        self.calls.append(("inspect", None))
        return {"pending": ["0003"], "applied": [], "drift": {"unknown_applied": []}}

    def plan(self, migration_ids: list[str]) -> object:
        from aitest.infrastructure.file_store.migrations import PlanResult

        self.calls.append(("plan", list(migration_ids)))
        self._maybe_fail()
        return PlanResult(
            plan_id="plan-1", steps=tuple(migration_ids), path=pathlib.Path("plans/plan-1.json")
        )

    def apply(self, plan_id: str) -> object:
        from aitest.infrastructure.file_store.migrations import ApplyReport

        self.calls.append(("apply", plan_id))
        self._maybe_fail()
        return ApplyReport(
            plan_id=plan_id,
            state="applied",
            executed=("0003",),
            skipped=(),
            backup_path=pathlib.Path("backups/plan-1"),
        )

    def resume(self, plan_id: str) -> object:
        from aitest.infrastructure.file_store.migrations import ApplyReport

        self.calls.append(("resume", plan_id))
        self._maybe_fail()
        return ApplyReport(
            plan_id=plan_id, state="nothing_to_apply", executed=(), skipped=("0003",),
            backup_path=None,
        )

    def rollback(self, plan_id: str) -> object:
        from aitest.infrastructure.file_store.migrations import RollbackReport

        self.calls.append(("rollback", plan_id))
        self._maybe_fail()
        return RollbackReport(plan_id=plan_id, state="rolled_back", rolled_back=("0003",))


def _entry(*, interactive: bool = True) -> tuple[SavedMigrationEntry, _Runner, MaintenanceMode]:
    runner, mode = _Runner(), MaintenanceMode()
    return (
        SavedMigrationEntry(runner=runner, actors=_Actors(interactive=interactive), mode=mode),
        runner,
        mode,
    )


def _command(**parameters: object) -> Command:
    return Command(
        request_id="req-migrate-1",
        action="migrate",
        project_id=PROJECT,
        intent_id="intent-migrate-1",
        expected_revision=0,
        parameters=dict(parameters),
    )


def test_inspect_is_read_only_and_needs_no_maintenance_window() -> None:
    entry, runner, mode = _entry(interactive=False)
    result = entry.apply(_command(mode="inspect"))
    assert result["mode"] == "inspect"
    assert result["report"]["pending"] == ["0003"]
    assert mode.active is False
    assert runner.calls == [("inspect", None)]


def test_write_modes_require_a_controlled_interactive_session() -> None:
    entry, runner, mode = _entry(interactive=False)
    for parameters in (
        {"mode": "plan", "migration_ids": ["0003"]},
        {"mode": "apply", "plan_id": "plan-1"},
        {"mode": "resume", "plan_id": "plan-1"},
        {"mode": "rollback", "plan_id": "plan-1"},
    ):
        with pytest.raises(MigrationBlocked) as refused:
            entry.apply(_command(**parameters))
        assert refused.value.code == "AWAITING_USER_CONFIRMATION"
    assert runner.calls == []
    assert mode.active is False


def test_write_modes_run_inside_a_maintenance_window_and_release_it() -> None:
    entry, runner, mode = _entry()
    for parameters, expected_mode in (
        ({"mode": "plan", "migration_ids": ["0003"]}, "plan"),
        ({"mode": "apply", "plan_id": "plan-1"}, "apply"),
        ({"mode": "resume", "plan_id": "plan-1"}, "resume"),
        ({"mode": "rollback", "plan_id": "plan-1"}, "rollback"),
    ):
        result = entry.apply(_command(**parameters))
        assert result["mode"] == expected_mode
        assert mode.active is False  # 窗口在动作结束后必须解除
    assert [call[0] for call in runner.calls] == ["plan", "apply", "resume", "rollback"]


def test_a_second_maintenance_window_is_required_not_nested() -> None:
    entry, _runner, mode = _entry()
    mode.enter("migrate:apply")
    try:
        with pytest.raises(MigrationBlocked) as refused:
            entry.apply(_command(mode="apply", plan_id="plan-1"))
        assert refused.value.code == "MAINTENANCE_REQUIRED"
    finally:
        mode.leave()
    assert mode.active is False


def test_runner_failures_are_reported_as_migration_failures() -> None:
    entry, runner, mode = _entry()
    runner.failure = RuntimeError("迁移前必须先完成可校验备份")
    with pytest.raises(MigrationBlocked) as refused:
        entry.apply(_command(mode="apply", plan_id="plan-1"))
    assert refused.value.code == "MIGRATION_FAILED"
    assert "可校验备份" in str(refused.value)
    assert mode.active is False  # 失败也必须解除窗口


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"mode": "teleport"},
        {"mode": "plan"},
        {"mode": "plan", "migration_ids": []},
        {"mode": "plan", "migration_ids": ["0003"], "plan_id": "plan-1"},
        {"mode": "apply"},
        {"mode": "apply", "plan_id": ""},
        {"mode": "inspect", "plan_id": "plan-1"},
        {"mode": "inspect", "extra": 1},
    ],
)
def test_parameter_shapes_are_refused_by_name(parameters: dict[str, object]) -> None:
    entry, runner, _mode = _entry()
    with pytest.raises(MigrationBlocked):
        entry.apply(_command(**parameters))
    assert runner.calls == []


def test_maintenance_window_pauses_new_writes_but_not_reads(tmp_path: pathlib.Path) -> None:
    """真装配：窗口期间写动作 `maintenance_required`，只读与维护动作继续可用。"""

    root = pathlib.Path(tempfile.mkdtemp(prefix="aitest-migrate-ws-"))
    try:
        core = assemble_workspace_core(root, instance_id="core-migrate")
        human = Session(session_id="cli", entry_kind=EntryKind.HUMAN_UI, interactive=True)
        assert "migrate" in core.api.handlers

        counter = {"value": 0}

        def dispatch(action: str, **parameters: object) -> Any:
            counter["value"] += 1
            return core.api.dispatch(
                Command(
                    request_id=f"req-{action}-{counter['value']}",
                    action=action,
                    project_id=PROJECT,
                    intent_id=f"intent-{action}-{counter['value']}",
                    expected_revision=0,
                    parameters=dict(parameters),
                ),
                human,
            )

        inspect = dispatch("migrate", mode="inspect")
        assert inspect.error is None
        assert inspect.result["mode"] == "inspect"
        assert "pending" in inspect.result["report"]

        # 窗口外：写动作正常进入 handler（这里用未知目标的备份，得到备份自身拒绝而不是维护拒绝）。
        outside = dispatch("backup", destination=str(root / "inside"))
        assert outside.error is not None and outside.error.code == "BACKUP_REFUSED"

        mode = core.maintenance_mode
        mode.enter("migrate:apply")
        try:
            paused = dispatch("backup", destination=str(root / "inside"))
            assert paused.error is not None
            assert paused.error.code == "maintenance_required"
            # 只读动作继续可用；维护动作本身也可用（否则无法完成迁移）。
            assert dispatch("doctor").error is None
            assert dispatch("storage_usage").error is None
            assert dispatch("migrate", mode="inspect").error is None
        finally:
            mode.leave()

        resumed = dispatch("backup", destination=str(root / "inside"))
        assert resumed.error is not None and resumed.error.code == "BACKUP_REFUSED"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_mode_vocabulary_matches_the_five_migrate_subcommands() -> None:
    assert MIGRATE_MODES == ("inspect", "plan", "apply", "resume", "rollback")
    assert frozenset({"plan", "apply", "resume", "rollback"}) == WRITE_MODES
    mode = MaintenanceMode()
    assert mode.active is False
    with mode.window("migrate:plan"):
        assert mode.active is True
        assert mode.reason == "migrate:plan"
    assert mode.active is False
    with pytest.raises(MaintenanceModeActive):
        mode.leave()
