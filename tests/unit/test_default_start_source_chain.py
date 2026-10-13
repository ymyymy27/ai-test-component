"""默认 start 来源链的端到端回归（C-15 / C-16）。

反例基线：`docs/validation/p1-source-completion-20261012/source-provider-counterexamples.py`
在 `fix@708439b` 上复现了两件事：

1. **C-15**：`prepare_run` 为 prepared，但默认 `prepare_execution` 在技术快照读取处抛
   `SnapshotError: pinned source snapshot identity is invalid`——默认解析器把业务
   `source-*` 直接当技术 `snap-*` 交物化端口，且把 snapshot_id 写进
   `ExecutionRequest.materialized_snapshot_ref`（默认注册入口把它当执行目录）。
2. **C-16**：三组真实固定字节的反例（平铺入口语法错误、正常包+导入错误包、命名空间包内
   子文件语法错误）此前都返回 `verified` 且无缺口。

本文件用**真实**核心/UOW/固定字节/物化 + 未替换的默认解析器与来源 provider 断言修复后的
行为。环境与受控确认仍来自既有测试夹具，不冒充真实 Trae 人工验收。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aitest.application.execution.action_resolver import ActionResolutionBlocked
from tests.support.environment_resolution import FixtureEnvironmentResolver
from tests.unit.test_authoritative_preparation import _authoritative, prepare
from tests.unit.test_initial_run_registration import register


class _RealInterpreterFixtureEnvironment(FixtureEnvironmentResolver):
    """合成环境事实 + **真实**解释器路径。

    来源探针必须真的启动登记解释器；`FixtureEnvironmentResolver` 的
    `synthetic-registered-python` 不可执行。这里只把可执行路径换成本机真实解释器，
    其余事实（载体/依赖/配置摘要）仍是显式合成夹具，不冒充真实环境验收。
    """

    def resolve(self, request):  # type: ignore[no-untyped-def]
        fact = super().resolve(request)
        assert fact.resolution is not None
        detail = fact.resolution.model_copy(
            update={
                "executable_path": sys.executable,
                "base_executable_path": sys.executable,
            }
        )
        return fact.model_copy(
            update={
                "resolution": detail,
                # 解释器身份随可执行路径变化，必须与解析事实一致，否则环境事实自相矛盾。
                "interpreter_identity": detail.interpreter_identity,
            }
        )


def _chain_environment():
    return _RealInterpreterFixtureEnvironment()


def _prepare_execution(core, inputs, prepared, *, intent: str = "default-start-intent"):
    """走默认装配：register_run → execution_authorizations.prepare（未替换解析器）。"""

    facts = register(core, prepared, request=intent + "-register", intent=intent + "-register")
    return core.execution_authorizations.prepare(
        project_id=inputs.project_id,
        run_id=facts.run_id,
        step_id=facts.steps[0].step_id,
        intent_id=intent,
        request_id=intent + "-request",
    )


def _saved_action(core, action_id: str) -> dict[str, object]:
    record = core.unit_of_work.repo.read(
        aggregate_kind="execution_authorization", record_id=action_id, revision=1
    )
    return dict(record.payload)


def test_business_snapshot_identity_differs_from_the_technical_pinned_identity(tmp_path):
    """先证明测试真的有区分度：业务 source-ID 与技术 snap-ID 不同。"""

    with _authoritative(
        tmp_path, environment_resolver=_chain_environment()
    ) as (core, inputs, _):
        prepared = prepare(core, inputs)
        assert prepared.error is None, prepared.error
        assert prepared.result["status"] == "prepared", prepared.result
        saved = core.unit_of_work.repo.read(
            aggregate_kind="source_snapshot",
            record_id=inputs.snapshot.source_snapshot_id,
            revision=1,
        )
        business = inputs.snapshot.source_snapshot_id
        technical = saved.payload["pinned_snapshot_id"]
        assert business != technical
        assert business.startswith("source-")
        assert technical.startswith("snap-")
        core.lifetime_lock.release()


def test_default_prepare_execution_resolves_business_record_to_pinned_technical_source(
    tmp_path,
):
    """C-15：默认链必须贯通，且物化引用是**固定 workdir**、不是任何快照身份。"""

    with _authoritative(
        tmp_path, environment_resolver=_chain_environment()
    ) as (core, inputs, _):
        prepared = prepare(core, inputs)
        assert prepared.error is None, prepared.error
        assert prepared.result["status"] == "prepared", prepared.result

        action = _prepare_execution(core, inputs, prepared.result)

        assert action["execution_action_id"]
        payload = _saved_action(core, action["execution_action_id"])
        action_payload = payload["action"]
        assert isinstance(action_payload, dict)
        request = action_payload["request"]
        materialized = Path(str(request["materialized_snapshot_ref"]))
        assert str(materialized) != inputs.snapshot.source_snapshot_id
        # 物化引用必须是**固定 workdir**（默认注册入口把它当执行目录）。
        assert materialized.is_dir()
        assert materialized.parent == (core.workspace.root / "workdirs").resolve()
        # 真实物化目录必须含冻结清单里的实际文件。
        assert (materialized / "main.py").is_file()
        core.lifetime_lock.release()


def test_a_real_syntax_error_in_the_frozen_source_blocks_start(tmp_path):
    """C-16 反例一（平铺入口语法错误）：真实固定字节、实际编译 → 必须阻塞。"""

    with _authoritative(
        tmp_path, main_source="def broken(:\n", environment_resolver=_chain_environment()
    ) as (core, inputs, _):
        prepared = prepare(core, inputs)
        assert prepared.error is None, prepared.error
        assert prepared.result["status"] == "prepared", prepared.result

        with pytest.raises(ActionResolutionBlocked) as error:
            _prepare_execution(core, inputs, prepared.result)

        message = str(error.value)
        assert "source_syntax_error:main.py" in message, message
        assert "mismatch" in message, message
        core.lifetime_lock.release()


def test_a_clean_frozen_source_still_verifies_under_the_same_chain(tmp_path):
    """对照组：同一链路下干净源码仍然通过（避免把"一律阻塞"当成修复）。"""

    with _authoritative(
        tmp_path, environment_resolver=_chain_environment()
    ) as (core, inputs, _):
        prepared = prepare(core, inputs)
        assert prepared.result["status"] == "prepared", prepared.result
        action = _prepare_execution(core, inputs, prepared.result)
        assert action["execution_action_id"]
        core.lifetime_lock.release()
