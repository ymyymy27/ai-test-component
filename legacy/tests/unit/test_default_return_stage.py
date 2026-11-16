"""`return_stage`（退回阶段）入口：与上游重试同一失效机制，并如实给出下游需重跑清单。

分册冻结的语义：功能 §4/§221「退回阶段：形成新修订，将依赖下游标为需重跑，旧记录可查」；
功能 §15「**退回阶段与普通上游重试使用同一失效机制**」；架构 02 §157「退回阶段生成修订与
下游需重跑清单，**不把沿用证据记为重新执行**」；设计 §372「退回阶段先展示下游需重跑」。

本文件锁定：①前置条件与 `retry_step` 一致（必须已解析出新一轮动作）；②影响面来自
**已提交事实的依赖边**（不接受调用方自报）；③执行后只回执"实际被登记为需重跑"的集合；
④旧尝试保留为历史且**不记为已重跑**；⑤没有下游/无失效事实时如实说明，不代为标记。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from aitest.application.execution.commands import (
    ExecutionCommands,
    InvalidExecutionCommand,
)
from aitest.contracts.commands import Command

PROJECT = "project-1"
RUN = "run-1"
TARGET = "step-1"


def _action(*, attempt_index: int, step_id: str = TARGET, run_id: str = RUN) -> Any:
    return SimpleNamespace(
        attempt=SimpleNamespace(attempt_index=attempt_index, run_id=run_id),
        request=SimpleNamespace(step_id=step_id),
    )


def _step(step_id: str, *dependencies: str) -> Any:
    return SimpleNamespace(step_id=step_id, dependency_step_ids=tuple(dependencies))


def _facts(*, invalidations: tuple[str, ...] = ()) -> Any:
    return SimpleNamespace(
        steps=(
            _step("step-1"),
            _step("step-2", "step-1"),
            _step("step-3", "step-2"),
            _step("step-4"),  # 无依赖：不属下游
        ),
        dependency_invalidations=tuple(
            SimpleNamespace(affected_step_id=step_id) for step_id in invalidations
        ),
    )


class _Resolver:
    def __init__(self, action: Any) -> None:
        self._action = action
        self.reads: list[tuple[str, str]] = []

    def read(self, project: str, action_id: str) -> tuple[str, Any]:
        self.reads.append((project, action_id))
        return ("frozen", self._action)


class _Coordinator:
    def __init__(self, snapshots: list[Any]) -> None:
        self._snapshots = snapshots
        self.calls: list[tuple[str, str]] = []

    def read_runtime_revision_facts(self, *, project_id: str, run_id: str) -> Any:
        self.calls.append((project_id, run_id))
        if not self._snapshots:
            raise ValueError("no snapshot")
        return self._snapshots.pop(0)


class _Execution:
    def __init__(self, action: Any, coordinator: Any) -> None:
        self.authorizations = SimpleNamespace(resolver=_Resolver(action))
        self.coordinator = coordinator
        self.calls: list[dict[str, object]] = []

    def execute(
        self, *, project_id: str, intent_id: str, action_id: str, step_id: str
    ) -> dict[str, object]:
        self.calls.append(
            {
                "project_id": project_id,
                "intent_id": intent_id,
                "action_id": action_id,
                "step_id": step_id,
            }
        )
        return {"status": "executed", "attempt_id": "attempt-2"}


def _commands(
    *, attempt_index: int = 2, snapshots: list[Any] | None = None
) -> tuple[ExecutionCommands, _Execution, _Coordinator]:
    coordinator = _Coordinator(snapshots if snapshots is not None else [_facts(), _facts()])
    execution = _Execution(_action(attempt_index=attempt_index), coordinator)
    return ExecutionCommands(service=SimpleNamespace(), execution=execution), execution, coordinator


def _command(
    *, target: str = TARGET, parameters: dict[str, object] | None = None
) -> Command:
    return Command(
        request_id="req-return-1",
        action="return_stage",
        project_id=PROJECT,
        intent_id="intent-return-1",
        expected_revision=0,
        target=target,
        parameters=parameters
        if parameters is not None
        else {"execution_action_id": "action-1", "record_revision": 1},
    )


def test_return_stage_delegates_and_reports_the_downstream_rerun_list() -> None:
    commands, execution, coordinator = _commands(
        snapshots=[_facts(), _facts(invalidations=("step-2",))]
    )
    result = commands.return_stage(_command())

    assert result["status"] == "executed"
    assert result["returned_step_id"] == TARGET
    assert result["retried_from_attempt_index"] == 1
    # 传递下游：step-2 依赖 step-1，step-3 依赖 step-2；step-4 与它无关。
    assert result["downstream_step_ids"] == ["step-2", "step-3"]
    # 只回执**实际登记为需重跑**的子集（不代标记）。
    assert result["downstream_requires_rerun"] == ["step-2"]
    assert result["previous_attempt_preserved"] is True
    assert result["inherited_evidence_not_reexecuted"] is True
    # 影响面读的是已提交事实（执行前后各一次），并按 Run 寻址。
    assert coordinator.calls == [(PROJECT, RUN), (PROJECT, RUN)]
    assert execution.calls == [
        {
            "project_id": PROJECT,
            "intent_id": "intent-return-1",
            "action_id": "action-1",
            "step_id": TARGET,
        }
    ]


def test_return_stage_notes_when_there_is_no_downstream() -> None:
    command = Command(
        request_id="req-return-2",
        action="return_stage",
        project_id=PROJECT,
        intent_id="intent-return-2",
        expected_revision=0,
        target="step-4",
        parameters={"execution_action_id": "action-1", "record_revision": 1},
    )
    # 目标无依赖下游：影响面为空，但如实说明而不是空成功。
    commands, _execution, _coordinator = _commands(
        snapshots=[_facts(), _facts(invalidations=("step-2",))]
    )
    action = _action(attempt_index=2, step_id="step-4")
    execution = _Execution(action, _Coordinator([_facts(), _facts()]))
    commands = ExecutionCommands(service=SimpleNamespace(), execution=execution)
    result = commands.return_stage(command)
    assert result["downstream_step_ids"] == []
    assert result["downstream_requires_rerun"] == []
    assert "没有已登记的依赖下游" in str(result["downstream_note"])


def test_return_stage_does_not_pretend_unregistered_downstream_was_marked() -> None:
    commands, _execution, _coordinator = _commands(
        snapshots=[_facts(), _facts(invalidations=())]
    )
    result = commands.return_stage(_command())
    assert result["downstream_step_ids"] == ["step-2", "step-3"]
    assert result["downstream_requires_rerun"] == []
    assert "按事实如实显示" in str(result["downstream_note"])


def test_first_attempt_target_and_bad_parameters_are_refused() -> None:
    commands, execution, _coordinator = _commands(attempt_index=1)
    with pytest.raises(InvalidExecutionCommand) as refused:
        commands.return_stage(_command())
    assert "execute_step" in str(refused.value)
    assert execution.calls == []

    other = _command(target="step-other")
    commands, execution, _coordinator = _commands()
    with pytest.raises(InvalidExecutionCommand):
        commands.return_stage(other)
    assert execution.calls == []

    for parameters in (
        {"execution_action_id": "action-1", "record_revision": 2},
        {"execution_action_id": ""},
        {"execution_action_id": "action-1", "extra": 1},
    ):
        commands, execution, _coordinator = _commands()
        with pytest.raises(InvalidExecutionCommand):
            commands.return_stage(_command(parameters=parameters))
        assert execution.calls == []


def test_committed_facts_are_required_for_the_impact_surface() -> None:
    """影响面只能来自已提交事实：读不到事实就拒绝，不猜下游。"""

    execution = _Execution(_action(attempt_index=2), _Coordinator([]))
    commands = ExecutionCommands(service=SimpleNamespace(), execution=execution)
    with pytest.raises(InvalidExecutionCommand):
        commands.return_stage(_command())
    # 事实读取失败发生在执行之前：不能先执行再补影响面。
    assert execution.calls == []


def test_default_assembly_advertises_return_stage() -> None:
    import pathlib
    import shutil
    import tempfile

    from aitest.bootstrap import assemble_workspace_core
    from aitest.interfaces.local.api import EntryKind, Session

    root = pathlib.Path(tempfile.mkdtemp(prefix="aitest-return-entry-"))
    try:
        core = assemble_workspace_core(root, instance_id="core-return")
        assert "return_stage" in core.api.handlers
        doctor = core.api.dispatch(
            Command(request_id="req-doctor", action="doctor"),
            Session(session_id="cli-return", entry_kind=EntryKind.HUMAN_UI),
        )
        advertised = {str(item) for item in doctor.result["supported_actions"]}
        assert {"return_stage", "retry_step", "execute_step"} <= advertised
    finally:
        shutil.rmtree(root, ignore_errors=True)
