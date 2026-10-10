"""起始准入的「实际解释器」核对缝。

决策②（会话自行裁定）：先只接**解释器**一项，且默认关闭以零回归风险。
本测试锁定这条缝的两端：

1. **装配层**注入的探针必须真的跑一次子进程，并交回解释器**自报的 realpath**
   （`LoadSourceFact.executable`）——不是把配置值回读一遍；
2. **应用层**按 `source_checks._derive_state` 的口径（解析后路径一致）判定：
   不一致拒绝，一致放行。

应用层不得导入 infrastructure，故探针只能由 `bootstrap` 注入；这正是要锁的边界。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aitest.application.execution.action_resolver import (
    ActionResolutionBlocked,
    SavedActionResolver,
)
from aitest.bootstrap import _interpreter_verifier
from aitest.contracts.prepared_run import EnvironmentResolutionFact

WORKDIR = "materialized-workdir"


def _environment(executable: str) -> EnvironmentResolutionFact:
    return EnvironmentResolutionFact(
        carrier_id="carrier-1",
        executable_path=executable,
        executable_digest="sha256:executable",
        interpreter_version="3.13.13",
        base_executable_path=executable,
        base_executable_digest="sha256:base",
        probe_prefix="/prefix",
        base_prefix="/prefix",
        configuration_digest="sha256:config",
        dependency_roots=("/site-packages",),
        dependency_set_digest="sha256:deps",
    )


def _resolver(verifier) -> SavedActionResolver:
    return SavedActionResolver(
        attempt_index=lambda _run, _step: 1,
        side_effect_class=lambda _run, _step: None,
        admission=None,
        interpreter_verifier=verifier,
    )


def test_verifier_reports_the_interpreters_own_realpath(tmp_path: Path) -> None:
    """装配层的探针必须现场跑一次并交回自报 realpath。"""

    observed = _interpreter_verifier()(str(tmp_path), _environment(sys.executable))

    assert Path(observed).resolve() == Path(sys.executable).resolve()


def test_observed_interpreter_must_equal_the_frozen_one() -> None:
    frozen = sys.executable
    resolver = _resolver(lambda _workdir, _environment: frozen)

    # 不抛异常即通过（口径：解析后路径一致）。
    resolver._require_observed_interpreter(WORKDIR, _environment(frozen))  # noqa: SLF001


def test_a_different_observed_interpreter_blocks() -> None:
    resolver = _resolver(lambda _workdir, _environment: "/definitely/another/python")

    with pytest.raises(ActionResolutionBlocked):
        resolver._require_observed_interpreter(  # noqa: SLF001
            WORKDIR, _environment(sys.executable)
        )


def test_probe_failure_blocks_instead_of_passing_silently() -> None:
    """探针报错必须转成阻塞，不能降级成「看起来通过」。"""

    def broken(_workdir: str, _environment: EnvironmentResolutionFact) -> str:
        raise ActionResolutionBlocked("probe could not observe the interpreter")

    with pytest.raises(ActionResolutionBlocked):
        _resolver(broken)._require_observed_interpreter(  # noqa: SLF001
            WORKDIR, _environment(sys.executable)
        )


def test_without_a_verifier_the_check_is_not_claimed() -> None:
    """默认关闭时不得声称已核对：未注入即不做任何判定，也不抛错。"""

    _resolver(None)._require_observed_interpreter(  # noqa: SLF001
        WORKDIR, _environment("/nowhere/python")
    )
