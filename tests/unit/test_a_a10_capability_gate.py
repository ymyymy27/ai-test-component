"""A-10：动作级能力门、失败分类、人工降级与换核心水合。

只降级实际依赖故障能力的动作：
- 单元：依赖声明、自动事实、人工降级不被探测成功擅自解除；
- LocalAPI：依赖模型的动作在 not_configured/degraded 时收 CAPABILITY_DEGRADED，
  同核心本地动作与 doctor 照常；探测失败按归一 error_kind 写门，成功恢复；
- 默认装配：真实 SecretPort/快照/来源探针随核心装配，模型缺凭据只降级模型；
- 换核心：上一实例台账中的故障结论被新实例门水合，不重置成"就绪"。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.infrastructure.capabilities import (
    CONNECTION,
    MODEL,
    SECRET,
    SOURCE,
    CapabilityGate,
    CapabilityState,
)
from aitest.infrastructure.connections import (
    ConnectionFactStore,
    EndpointConfig,
    LocalAPIConnectionBridge,
    TransportErrorKind,
    TransportFact,
)
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session

_SESSION = Session(session_id="a10-gate", entry_kind=EntryKind.INTERACTIVE_CLI)
_UNREACHABLE = TransportFact(
    False, 3, TransportErrorKind.DNS_ERROR.value, "name resolution failed"
)
_REACHABLE = TransportFact(True, 2, None, "")


class _ScriptedProbe:
    def __init__(self, facts: list[TransportFact]) -> None:
        self._facts = facts

    def probe(
        self, endpoint: EndpointConfig, *, timeout_seconds: float = 2.0
    ) -> TransportFact:
        return self._facts.pop(0)


def _command(request_id: str, action: str) -> Command:
    return Command(request_id=request_id, action=action)


# ------------------------------------------------------------ 门单元


def test_gate_blocks_only_actions_requiring_failed_dependency() -> None:
    gate = CapabilityGate(
        action_dependencies={
            "model_draft": frozenset({MODEL, SECRET}),
            "local_save": frozenset(),
        }
    )
    # 默认全部未配置：依赖动作被拒，未声明依赖的动作恒放行。
    decision = gate.check("model_draft")
    assert decision.allowed is False
    assert any(c.key == MODEL for c in decision.denied_by)
    assert gate.check("local_save").allowed is True
    assert gate.check("never_declared").allowed is True

    gate.configure(SECRET)
    gate.configure(MODEL)
    assert gate.check("model_draft").allowed is True

    gate.report(MODEL, healthy=False, reason="429", classification="rate_limit")
    again = gate.check("model_draft")
    assert again.allowed is False
    assert gate.affected_actions(MODEL) == ("model_draft",)
    # 本地动作不受模型故障影响。
    assert gate.check("local_save").allowed is True


def test_manual_degrade_survives_healthy_fact_until_explicit_restore() -> None:
    gate = CapabilityGate()
    gate.configure(CONNECTION)
    gate.degrade(CONNECTION, "操作者暂停外连")
    # 一次成功探测不得擅自解除人工降级。
    gate.report(CONNECTION, healthy=True)
    condition = gate.condition(CONNECTION)
    assert condition.state is CapabilityState.DEGRADED
    assert condition.manual is True
    gate.restore(CONNECTION)
    assert gate.condition(CONNECTION).state is CapabilityState.READY


def test_unknown_dependency_key_rejected() -> None:
    gate = CapabilityGate()
    with pytest.raises(ValueError):
        gate.require("x", "database")
    with pytest.raises(ValueError):
        gate.configure("database")


def test_snapshot_carries_conditions_and_action_map() -> None:
    gate = CapabilityGate(
        action_dependencies={"model_draft": frozenset({MODEL})}
    )
    snapshot = gate.snapshot()
    keys = {item["key"] for item in snapshot["dependencies"]}
    assert keys == {CONNECTION, MODEL, SECRET, SOURCE}
    assert snapshot["action_dependencies"] == {"model_draft": ["model"]}


# ------------------------------------------------------------ LocalAPI 接线


def test_local_api_blocks_dependent_action_but_not_local_ones() -> None:
    gate = CapabilityGate(
        action_dependencies={"model_draft": frozenset({MODEL})}
    )
    calls: list[str] = []

    def model_handler(command: Command) -> dict[str, object]:
        calls.append(command.action)
        return {"draft": "ok"}

    def local_handler(command: Command) -> dict[str, object]:
        calls.append(command.action)
        return {"local": True}

    api = LocalAPI(
        instance_id="core",
        workspace_id="ws",
        handlers={"model_draft": model_handler, "local_tick": local_handler},
        capability_gate=gate,
    )

    blocked = api.dispatch(_command("r1", "model_draft"), _SESSION)
    assert blocked.error is not None
    assert blocked.error.code == "CAPABILITY_DEGRADED"
    assert "model" in blocked.error.message
    assert calls == []

    local = api.dispatch(_command("r2", "local_tick"), _SESSION)
    assert local.error is None
    assert local.result == {"local": True}

    gate.configure(MODEL)
    allowed = api.dispatch(_command("r3", "model_draft"), _SESSION)
    assert allowed.error is None
    assert allowed.result == {"draft": "ok"}
    assert "model_draft" in calls


def test_doctor_reports_dependency_conditions() -> None:
    gate = CapabilityGate(action_dependencies={"model_draft": frozenset({MODEL})})
    gate.configure(SECRET)
    api = LocalAPI(
        instance_id="core", workspace_id="ws", capability_gate=gate
    )
    response = api.dispatch(_command("r1", "doctor"), _SESSION)
    dependencies = response.result["dependencies"]
    by_key = {item["key"]: item["state"] for item in dependencies["dependencies"]}
    assert by_key[SECRET] == "ready"
    assert by_key[MODEL] == "not_configured"
    assert dependencies["action_dependencies"] == {"model_draft": ["model"]}


def test_connection_probe_failure_classifies_and_success_restores(
    tmp_path: Path,
) -> None:
    endpoint = EndpointConfig.from_address("https://api.example.com:443")
    failing_bridge = LocalAPIConnectionBridge(
        endpoint,
        ConnectionFactStore(tmp_path),
        probe=_ScriptedProbe([_UNREACHABLE] * 10),
    )
    gate = CapabilityGate()
    gate.configure(CONNECTION)
    api = LocalAPI(
        instance_id="core",
        workspace_id="ws",
        connector=failing_bridge,
        connection_persistence=failing_bridge,
        capability_gate=gate,
        sleeper=lambda _seconds: None,
    )
    failed = api.dispatch(
        Command(
            request_id="req-fail",
            action="test_connection",
            project_id="project-a10",
            intent_id="intent-fail",
            expected_revision=0,
        ),
        _SESSION,
    )
    assert failed.error is not None
    assert failed.error.code == "CONNECTIVITY_FAILED"
    condition = gate.condition(CONNECTION)
    assert condition.state is CapabilityState.DEGRADED
    assert condition.classification == "dns_error"
    assert condition.manual is False

    # 重连成功：同一门恢复就绪，test_connection 自身始终可执行。
    ok_bridge = LocalAPIConnectionBridge(
        endpoint,
        ConnectionFactStore(tmp_path),
        probe=_ScriptedProbe([_REACHABLE]),
    )
    api.connector = ok_bridge
    api._connection_persistence = ok_bridge
    recovered = api.dispatch(
        Command(
            request_id="req-ok",
            action="test_connection",
            project_id="project-a10",
            intent_id="intent-ok",
            expected_revision=0,
        ),
        _SESSION,
    )
    assert recovered.error is None
    assert gate.condition(CONNECTION).state is CapabilityState.READY


# ------------------------------------------------------------ 默认装配


def test_default_assembly_wires_real_secret_and_source_capabilities(
    tmp_path: Path,
) -> None:
    assembly = assemble_workspace_core(tmp_path, instance_id="core-a10")
    gate = assembly.gate
    assert gate is not None
    assert assembly.secret_manager is not None
    assert assembly.snapshot_store is not None
    assert assembly.source_probe is not None
    # 真实 SecretPort（环境变量+Windows 凭据管理器）与本地来源探针就绪；
    # 未显式配置的外网/模型能力保持 not_configured，不假装可用。
    assert gate.condition(SECRET).state is CapabilityState.READY
    assert gate.condition(SOURCE).state is CapabilityState.READY
    assert gate.condition(CONNECTION).state is CapabilityState.NOT_CONFIGURED
    assert gate.condition(MODEL).state is CapabilityState.NOT_CONFIGURED
    assert assembly.model_provider is None

    # doctor 经协议暴露同一份条件。
    response = assembly.api.dispatch(_command("req-doc", "doctor"), _SESSION)
    assert response.result["dependencies"]["dependencies"]


def test_model_endpoint_without_secret_only_degrades_model(tmp_path: Path) -> None:
    assembly = assemble_workspace_core(
        tmp_path,
        instance_id="core-model",
        model_endpoint="https://model.example.com:443",
        model_secret_reference=("model", "a10-no-such-reference"),
    )
    assert assembly.model_provider is None
    condition = assembly.gate.condition(MODEL)
    assert condition.state is CapabilityState.DEGRADED
    assert condition.classification == "auth"
    # 本地保存/查询动作不被模型凭据问题拖累。
    response = assembly.api.dispatch(_command("req-doc", "doctor"), _SESSION)
    assert response.result["status"] == "READY"


def test_extra_action_dependencies_gate_registered_handlers(tmp_path: Path) -> None:
    def model_only_handler(command: Command) -> dict[str, object]:
        return {"ran": True}

    assembly = assemble_workspace_core(
        tmp_path,
        instance_id="core-extra",
        extra_handlers={"c_model_only": model_only_handler},
        extra_action_dependencies={"c_model_only": (MODEL,)},
    )
    blocked = assembly.api.dispatch(
        _command("req-blocked", "c_model_only"), _SESSION
    )
    assert blocked.error is not None
    assert blocked.error.code == "CAPABILITY_DEGRADED"

    assembly.gate.configure(MODEL)
    allowed = assembly.api.dispatch(
        _command("req-allowed", "c_model_only"), _SESSION
    )
    assert allowed.error is None
    assert allowed.result == {"ran": True}


def test_new_core_hydrates_connection_failure_from_fact_ledger(tmp_path: Path) -> None:
    endpoint = EndpointConfig.from_address("https://api.example.com:443")
    address = endpoint.base_address
    # 上一核心遗留：最后一次探测为 DNS 失败，事实已落盘。
    store = ConnectionFactStore(tmp_path)
    store.append(
        endpoint_address=address,
        fact=_UNREACHABLE,
        source_session="S-old",
        observed_at=1.0,
    )
    first = assemble_workspace_core(
        tmp_path,
        instance_id="core-old",
        connection_endpoint=address,
    )
    assert first.gate.condition(CONNECTION).state is CapabilityState.DEGRADED
    assert first.gate.condition(CONNECTION).classification == "dns_error"
    first.lifetime_lock.release()

    # 换核心：不探测，装配水合即继承故障分类，不重置为就绪。
    second = assemble_workspace_core(
        tmp_path,
        instance_id="core-new",
        connection_endpoint=address,
    )
    try:
        condition = second.gate.condition(CONNECTION)
        assert condition.state is CapabilityState.DEGRADED
        assert condition.classification == "dns_error"
    finally:
        second.lifetime_lock.release()


# ----- 复核补强：凭据故障必须是可恢复的自动降级，不是人工降级 -------------


def test_model_credential_failure_is_recoverable_automatic_degradation(
    tmp_path: Path,
) -> None:
    assembly = assemble_workspace_core(
        tmp_path,
        instance_id="core-model-auth",
        model_endpoint="https://model.example.com:443",
        model_secret_reference=("model", "a10-missing-ref"),
    )
    condition = assembly.gate.condition(MODEL)
    assert condition.state is CapabilityState.DEGRADED
    assert condition.classification == "auth"
    # 凭据解析失败是自动事实，不得落人工降级：manual=True 会把临时故障
    # 永久钉死（探测成功也不恢复），违反"人工降级与自动事实分开记录"。
    assert condition.manual is False
    # 一次成功事实即可自动恢复，无需显式 restore。
    assembly.gate.report(MODEL, healthy=True)
    assert assembly.gate.condition(MODEL).state is CapabilityState.READY


def test_model_endpoint_without_secret_reference_stays_not_configured(
    tmp_path: Path,
) -> None:
    """给了端点但未提供凭据引用 = 缺配置（not_configured），不是故障。"""
    assembly = assemble_workspace_core(
        tmp_path,
        instance_id="core-model-noref",
        model_endpoint="https://model.example.com:443",
    )
    condition = assembly.gate.condition(MODEL)
    assert condition.state is CapabilityState.NOT_CONFIGURED
    assert assembly.model_provider is None
