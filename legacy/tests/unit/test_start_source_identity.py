"""start 来源身份校验的口径：解释器按**解析后的路径**比较，入口按逐字一致比较。

背景（2026-10-11）：`require_interpreter` 里曾残留一段 `return` 之后的不可达代码，
用的是被废弃的字符串相等口径（`frozen_interpreter != observed_executable`）。
它虽然不可达，但会让后来者以为该口径仍生效——甚至可能被"修好"成活的。
本文件锁定当前口径：解释器走 `Path(...).resolve()`，**不接受**同一路径的不同写法被误判为不符。
"""

from __future__ import annotations

import pytest

from aitest.application.execution.start_source_binding import (
    SourceBindingUnverified,
    StartSourceBindingResolver,
)


def _validators() -> StartSourceBindingResolver:
    """纯校验方法不需要端口；与生产代码（action_resolver）用同一构造方式。"""

    return StartSourceBindingResolver.__new__(StartSourceBindingResolver)


def test_interpreter_identity_accepts_two_spellings_of_the_same_executable(tmp_path) -> None:
    """同一可执行文件的不同写法（含 `..`）必须视为同一个解释器。"""

    executable = tmp_path / "bin" / "python.exe"
    frozen = str(executable)
    observed = str(tmp_path / "bin" / "nested" / ".." / "python.exe")

    assert frozen != observed
    assert _validators().require_interpreter(
        frozen_interpreter=frozen, observed_executable=observed
    ) == frozen


def test_interpreter_identity_rejects_a_genuinely_different_executable(tmp_path) -> None:
    with pytest.raises(SourceBindingUnverified):
        _validators().require_interpreter(
            frozen_interpreter=str(tmp_path / "bin" / "python.exe"),
            observed_executable=str(tmp_path / "other" / "python.exe"),
        )


def test_registered_entry_identity_is_compared_verbatim(tmp_path) -> None:
    """入口是**逐字**一致，不做路径解析——换入口只能重新 prepare。"""

    entry = str(tmp_path / "run.sh")
    assert _validators().require_registered_entry(
        frozen_entry=entry, observed_entry=entry
    ) == entry
    with pytest.raises(SourceBindingUnverified):
        _validators().require_registered_entry(
            frozen_entry=entry, observed_entry=str(tmp_path / "nested" / ".." / "run.sh")
        )
