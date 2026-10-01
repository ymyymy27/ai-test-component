"""运行中修订消费侧的词汇表锁定：B 领域镜像的 C 状态枚举必须与 C 合同逐值一致。

`domain/` 不得 import `contracts/`（`tests/architecture/test_boundaries.py`），
因此 B 的领域层对 C 的活动状态做**双层声明**，与 `plans.CaseLayer` 同一做法
（见 `03-待冻结枚举与跨包字段确认表.md`、`07-规则版本与计划用例领域层设计说明.md` 第 2 节）。

本测试是那道锁：C 的合同增删枚举值时，这里立刻失败，而不是让 B 的守卫在运行中
遇到一个它不认识的取值再"静默按通过处理"（`CD-001` 第 7 节）。
"""

from __future__ import annotations

from aitest.contracts.execution_facts import (
    AttemptStateFact,
    RunControlStateFact,
    StepStateFact,
)
from aitest.domain.planning.runtime_revision import (
    ACTIVE_ATTEMPT_STATES,
    AttemptRuntimeState,
    RunRuntimeState,
    StepRuntimeState,
)


def test_step_states_mirror_the_execution_facts_contract() -> None:
    assert {state.value for state in StepRuntimeState} == {
        state.value for state in StepStateFact
    }


def test_attempt_states_mirror_the_execution_facts_contract() -> None:
    assert {state.value for state in AttemptRuntimeState} == {
        state.value for state in AttemptStateFact
    }


def test_run_control_states_mirror_the_execution_facts_contract() -> None:
    assert {state.value for state in RunRuntimeState} == {
        state.value for state in RunControlStateFact
    }


def test_the_active_attempt_states_are_the_non_terminal_ones() -> None:
    """"还在推进的尝试"必须正好是合同里的非终态取值：少一个就会漏拦正在执行的步骤。"""
    terminal = {
        AttemptStateFact.COMPLETED,
        AttemptStateFact.CANCELLED,
        AttemptStateFact.PENDING_VERIFICATION,
        AttemptStateFact.EXECUTION_ERROR,
        AttemptStateFact.INVALIDATED,
        AttemptStateFact.UNKNOWN,
    }
    assert {state.value for state in ACTIVE_ATTEMPT_STATES} == {
        state.value for state in AttemptStateFact
    } - {state.value for state in terminal}
