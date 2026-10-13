"""`retry_step` 入口：对已有尝试的步骤再执行一次（P1-FR09 / AC19）。

报告点名「retry_step/attach_evidence/return_stage 未注册」。本文件锁定 `retry_step`
的**前置条件与委派**：重试必须已解析出新一轮执行动作（`attempt_index >= 2`），
随后交给既有的步骤执行用例；"新尝试立即当前 + 依赖旧尝试的下游标为需重跑"由执行
提交路径在同一业务提交内完成（不在本入口另造失效机制），旧尝试保留为历史。

真实核心执行与真实宿主未验收；这里证明入口形状、拒绝条件与委派目标。
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
STEP = "step-1"


def _action(*, attempt_index: int, step_id: str = STEP) -> Any:
    return SimpleNamespace(
        attempt=SimpleNamespace(attempt_index=attempt_index),
        request=SimpleNamespace(step_id=step_id),
    )


class _Resolver:
    def __init__(self, action: Any) -> None:
        self._action = action
        self.reads: list[tuple[str, str]] = []

    def read(self, project: str, action_id: str) -> tuple[str, Any]:
        self.reads.append((project, action_id))
        return ("frozen", self._action)


class _Execution:
    def __init__(self, action: Any) -> None:
        self.authorizations = SimpleNamespace(resolver=_Resolver(action))
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


def _commands(action: Any, *, execution: Any | None = None) -> tuple[ExecutionCommands, Any]:
    execution = execution if execution is not None else _Execution(action)
    return (
        ExecutionCommands(
            service=SimpleNamespace(),  # retry 只用执行侧；授权服务由子用例负责
            execution=execution,
        ),
        execution,
    )


def _command(
    *, action: str = "retry_step", target: str = STEP, parameters: dict[str, object] | None = None
) -> Command:
    return Command(
        request_id="req-retry-1",
        action=action,
        project_id=PROJECT,
        intent_id="intent-retry-1",
        expected_revision=0,
        target=target,
        parameters=parameters
        if parameters is not None
        else {"execution_action_id": "action-1", "record_revision": 1},
    )


def test_retry_delegates_to_step_execution_and_reports_the_previous_attempt() -> None:
    commands, execution = _commands(_action(attempt_index=2))
    result = commands.retry(_command())
    assert result["status"] == "executed"
    assert result["attempt_id"] == "attempt-2"
    # 旧尝试保留为历史：只回执"从第几次尝试重试而来"。
    assert result["retried_from_attempt_index"] == 1
    assert result["previous_attempt_preserved"] is True
    assert execution.calls == [
        {
            "project_id": PROJECT,
            "intent_id": "intent-retry-1",
            "action_id": "action-1",
            "step_id": STEP,
        }
    ]
    # 动作身份经解析器读取核对，不接受调用方自报步骤。
    assert execution.authorizations.resolver.reads == [(PROJECT, "action-1")]


def test_first_attempt_is_refused_with_a_named_reason() -> None:
    commands, execution = _commands(_action(attempt_index=1))
    with pytest.raises(InvalidExecutionCommand) as refused:
        commands.retry(_command())
    assert "execute_step" in str(refused.value)
    assert execution.calls == []


def test_target_must_match_the_frozen_action() -> None:
    commands, execution = _commands(_action(attempt_index=3, step_id="step-other"))
    with pytest.raises(InvalidExecutionCommand):
        commands.retry(_command())
    assert execution.calls == []


@pytest.mark.parametrize(
    "parameters",
    [
        {"execution_action_id": "action-1", "record_revision": 2},
        {"execution_action_id": "", "record_revision": 1},
        {"execution_action_id": "action-1"},
        {"execution_action_id": "action-1", "record_revision": 1, "attempt_index": 1},
    ],
)
def test_retry_requires_the_exact_saved_action_reference(parameters: dict[str, object]) -> None:
    commands, execution = _commands(_action(attempt_index=2))
    with pytest.raises(InvalidExecutionCommand):
        commands.retry(_command(parameters=parameters))
    assert execution.calls == []


def test_retry_without_a_configured_execution_is_unavailable() -> None:
    from aitest.application.errors import CapabilityUnavailable

    commands = ExecutionCommands(service=SimpleNamespace(), execution=None)
    with pytest.raises(CapabilityUnavailable):
        commands.retry(_command())


def test_default_assembly_advertises_retry_step() -> None:
    """真装配里 `retry_step` 必须真实可派发（不是"文档里存在"）。"""

    import pathlib
    import shutil
    import tempfile

    from aitest.bootstrap import assemble_workspace_core
    from aitest.interfaces.local.api import EntryKind, Session

    root = pathlib.Path(tempfile.mkdtemp(prefix="aitest-retry-entry-"))
    try:
        core = assemble_workspace_core(root, instance_id="core-retry")
        assert "retry_step" in core.api.handlers
        doctor = core.api.dispatch(
            Command(request_id="req-doctor", action="doctor"),
            Session(session_id="cli-retry", entry_kind=EntryKind.HUMAN_UI),
        )
        advertised = {str(item) for item in doctor.result["supported_actions"]}
        assert "retry_step" in advertised
        assert "execute_step" in advertised
    finally:
        shutil.rmtree(root, ignore_errors=True)
