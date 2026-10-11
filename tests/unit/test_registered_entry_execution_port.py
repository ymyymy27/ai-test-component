"""默认执行端口的**真实进程证据**：登记入口 + 参数数组，cwd 是固定 workdir。

架构02《执行与证据》第 15/122 行：`ExecutionPort` 绑定固定快照、登记入口、参数数组与环境，
命令**不得**由模型或正文拼成 shell。本测试不看"调用成功"，而是让子进程在**固定 workdir**
里写一个标记文件——只有真的起了进程、cwd 真的是固定 workdir、命令真的来自
`registered_entry` 的 entrypoint+arguments，这个文件才会出现。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from aitest.domain.execution.runs import (
    AdapterKind,
    AuthorizationRef,
    ExecutionRequest,
    PlanRevisionRef,
    RegisteredEntryRef,
    SideEffectClass,
)
from aitest.infrastructure.adapters.execution.registered_entry import (
    RegisteredEntryExecutionAdapter,
)


def _request(workdir: Path, arguments: tuple[str, ...]) -> ExecutionRequest:
    return ExecutionRequest(
        project_id="project-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        intent_id="intent-1",
        resolved_input_digest="sha256:input-1",
        registered_entry=RegisteredEntryRef(
            entry_id="registered-python",
            adapter_kind=AdapterKind.PYTHON_CHECKS,
            entrypoint=sys.executable,
            arguments=arguments,
        ),
        materialized_snapshot_ref=str(workdir),
        environment_ref="environment-1",
        source_binding_digest="sha256:source-1",
        authorization_ref=AuthorizationRef(
            authorization_id="authorization-1",
            intent_id="intent-1",
            step_id="step-1",
            resolved_input_digest="sha256:input-1",
            target_ref="target-1",
            credential_scope_ref="scope-1",
            plan_revision_ref=PlanRevisionRef(
                revision_id="plan-1",
                revision_no=1,
                digest="sha256:plan-1",
            ),
        ),
        side_effect_class=SideEffectClass.READ_ONLY,
    )


def _wait_for(path: Path, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"子进程未产出证据文件: {path}")


def test_registered_entry_port_launches_a_real_process_in_the_fixed_workdir(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "fixed-workdir"
    workdir.mkdir()
    (workdir / "make_proof.py").write_text(
        "import pathlib\npathlib.Path('proof.txt').write_text('ran')\n",
        encoding="utf-8",
    )
    adapter = RegisteredEntryExecutionAdapter()

    handle = adapter.start(_request(workdir, ("make_proof.py",)))

    assert handle.process_start_identity.strip()
    assert handle.workdir_ref
    _wait_for(workdir / "proof.txt")
    assert (workdir / "proof.txt").read_text(encoding="utf-8") == "ran"
    # 证据文件只出现在固定 workdir 内：说明进程 cwd 就是它，不是调用方当前目录。
    assert not (tmp_path / "proof.txt").exists()


def test_the_same_entry_bound_to_another_workdir_is_refused(tmp_path: Path) -> None:
    """同一 entry_id 的**不同**绑定必须拒绝，既不复用旧 cwd 也不覆盖已核对事实。"""

    adapter = RegisteredEntryExecutionAdapter()
    first = tmp_path / "first"
    second = tmp_path / "second"
    for directory in (first, second):
        directory.mkdir()

    adapter.start(_request(first, ("-c", "pass")))
    with pytest.raises(ValueError):
        adapter.start(_request(second, ("-c", "pass")))
