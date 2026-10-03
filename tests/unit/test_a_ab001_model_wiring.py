"""AB-001 §8.16.3：模型端口默认装配、三动作注册与凭据窄适配（候选甲）。

覆盖三项 A 侧开发任务：

1. **默认装配**：`assemble_workspace_core` 把模型三端口（投影/调用/凭据解析）
   注入 `BUseCaseDependencies`；模型能力未配置时经能力门降级为
   `CAPABILITY_DEGRADED`，其余动作不受影响。
2. **动作注册**：`request_model_draft` / `revise_pending_steps` /
   `narrow_driver` 进入 `OWNED_ACTIONS` 与注册表，handler 可用且错误码结构化。
3. **凭据窄适配器（候选甲）**：`PurposeBoundCredentialResolver` 把 A 的
   `SecretPort` 包成 B 的 `CredentialResolver` 形状——按用途绑定引用，
   只回状态，类型层面拿不到正文。

工作空间用临时目录（与 `test_b_use_case_registration.py` 同一约定）；
执行事实用 C 的交付夹具（`CD-001` fixtures），产品代码不读文件系统。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from aitest.application.planning.model_ports import CredentialStatus
from aitest.application.planning.substrate_adapter import (
    PortsRecordReader,
    PortsUnitOfWork,
)
from aitest.application.usecase_registry import (
    OWNED_ACTIONS,
    BUseCaseDependencies,
)
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.planning.model_outbound import (
    MaterialKind,
    ModelEndpoint,
    ModelOutboundPolicy,
    endpoint_digest,
    material_kinds_digest,
)
from aitest.domain.planning.plans import (
    AssertionBasisState,
    CaseLayer,
    PlanPublicationStatus,
    RunDriver,
    RunTier,
)
from aitest.infrastructure.capabilities import CapabilityState
from aitest.infrastructure.credential_resolver import PurposeBoundCredentialResolver
from aitest.infrastructure.file_store.index import FileQueryIndex
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from aitest.interfaces.local.b_registration import register_b_use_cases
from tests.support.memory_model import (
    MemoryCredentialResolver,
    MemoryModelCaller,
    MemoryProjector,
)

PROJECT_ID = "project-ab001"
WORKSPACE_ID = "ws-ab001"

FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "docs/接口对接/进行中/CD-001-ExecutionFacts/fixtures"
)

_BASIS_TEXT = "订单创建后状态为已支付"
_BASIS_DIGEST = "sha256:basis-1"


# ------------------------------------------------------------------ 公共夹具


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


@pytest.fixture
def workspace_root() -> Iterator[Path]:
    root = Path(tempfile.gettempdir()) / f"aitest-ab001-{uuid.uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _deps(
    root: Path,
    *,
    projector: MemoryProjector | None = None,
    caller: MemoryModelCaller | None = None,
    credentials: MemoryCredentialResolver | None = None,
) -> BUseCaseDependencies:
    raw = FileUnitOfWork(root)
    # 新工作空间尚无查询索引；按既有合同测试约定先建空索引
    # （缺索引的查询在 A 侧是 maintenance_required，不是隐式全表扫描）。
    FileQueryIndex(root).rebuild(())
    return BUseCaseDependencies(
        unit_of_work=PortsUnitOfWork(raw, repository=raw.repo),
        reader=PortsRecordReader(raw.repo),
        clock=_FixedClock(),
        projector=projector,
        caller=caller,
        credentials=credentials,
    )


def _session() -> Session:
    return Session(session_id="cli-ab001", entry_kind=EntryKind.HUMAN_UI)


def _command(
    action: str,
    parameters: Mapping[str, object],
    *,
    request_id: str | None = None,
) -> Command:
    return Command(
        request_id=request_id or f"req-{uuid.uuid4().hex[:8]}",
        action=action,
        project_id=PROJECT_ID,
        expected_revision=0,
        intent_id=f"intent-{uuid.uuid4().hex[:8]}",
        parameters=cast("dict[str, JsonValue]", dict(parameters)),
    )


# ------------------------------------------------------------------ 3. 凭据窄适配器


class _StubSecretPort:
    """SecretPort 形状的最小实现：按 (reference, purpose) 记录调用并返回标记对象。"""

    def __init__(self, *, failing: bool = False) -> None:
        self.failing = failing
        self.calls: list[tuple[str, str]] = []

    def resolve(self, reference: str, *, purpose: str) -> object:
        self.calls.append((reference, purpose))
        if self.failing:
            raise LookupError("reference not found in any registered source")
        return object()  # 受控明文对象的替身；适配器不得把它带出去


def test_resolver_available_for_configured_purpose() -> None:
    port = _StubSecretPort()
    resolver = PurposeBoundCredentialResolver(port, {"model": "env:MODEL_KEY"})
    resolution = resolver.resolve(purpose="model")
    assert resolution.status is CredentialStatus.AVAILABLE
    assert resolution.purpose == "model"
    # 装配方绑定的引用被原样使用，B 侧无法选择或更换引用。
    assert port.calls == [("env:MODEL_KEY", "model")]


def test_resolver_rejects_unconfigured_purpose_without_touching_port() -> None:
    port = _StubSecretPort()
    resolver = PurposeBoundCredentialResolver(port, {"model": "env:MODEL_KEY"})
    resolution = resolver.resolve(purpose="github")
    assert resolution.status is CredentialStatus.PURPOSE_MISMATCH
    assert "github" in resolution.detail
    # 未配置的用途不得落到 SecretPort（不静默回退到其他用途）。
    assert port.calls == []


def test_resolver_maps_resolution_failure_to_missing() -> None:
    port = _StubSecretPort(failing=True)
    resolver = PurposeBoundCredentialResolver(port, {"model": "env:MODEL_KEY"})
    resolution = resolver.resolve(purpose="model")
    assert resolution.status is CredentialStatus.MISSING
    assert "LookupError" in resolution.detail


def test_resolver_never_exposes_secret_material() -> None:
    """`ResolvedSecret` 在适配器内即取即弃：结果类型没有正文，字符串化也不含引用值。"""
    resolver = PurposeBoundCredentialResolver(
        _StubSecretPort(), {"model": "env:SUPER-SECRET-REF"}
    )
    resolution = resolver.resolve(purpose="model")
    assert not hasattr(resolution, "secret")
    assert not hasattr(resolution, "value")
    assert "SUPER-SECRET-REF" not in str(resolution)


# ------------------------------------------------------------------ 2. 动作注册与 handler


def test_model_and_revision_actions_are_owned() -> None:
    assert {"request_model_draft", "revise_pending_steps", "narrow_driver"} <= set(
        OWNED_ACTIONS
    )


def test_narrow_driver_allows_planned_to_stepwise(workspace_root: Path) -> None:
    api = LocalAPI(instance_id="core-ab001", workspace_id=WORKSPACE_ID, handlers={})
    register_b_use_cases(api, _deps(workspace_root))
    response = api.dispatch(
        _command("narrow_driver", {"current": "planned", "requested": "stepwise"}),
        _session(),
    )
    assert response.error is None
    assert response.result is not None
    assert response.result["driver"] == "stepwise"


def test_narrow_driver_rejects_expansion(workspace_root: Path) -> None:
    """stepwise → planned 是扩大授权，必须拒绝且是结构化参数错误。"""
    api = LocalAPI(instance_id="core-ab001", workspace_id=WORKSPACE_ID, handlers={})
    register_b_use_cases(api, _deps(workspace_root))
    response = api.dispatch(
        _command("narrow_driver", {"current": "stepwise", "requested": "planned"}),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"


def test_narrow_driver_rejects_unknown_value(workspace_root: Path) -> None:
    api = LocalAPI(instance_id="core-ab001", workspace_id=WORKSPACE_ID, handlers={})
    register_b_use_cases(api, _deps(workspace_root))
    response = api.dispatch(
        _command("narrow_driver", {"current": "planned", "requested": "ludicrous"}),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"


def _policy_payload() -> dict[str, object]:
    endpoint = ModelEndpoint(
        provider="stub",
        address="https://model.example.com:443",
        model_id="stub-model",
        purpose="model",
    )
    policy = ModelOutboundPolicy(
        project_id=PROJECT_ID,
        revision=1,
        endpoint=endpoint,
        allowed_material_kinds=frozenset({MaterialKind.PROJECT_CONTEXT}),
    )
    endpoint_d = endpoint_digest(endpoint)
    kinds_d = material_kinds_digest(policy)
    return {
        "revision": 1,
        "endpoint": {
            "provider": "stub",
            "address": "https://model.example.com:443",
            "model_id": "stub-model",
            "purpose": "model",
        },
        "allowed_material_kinds": ["project_context"],
        "source_snippets_enabled": False,
        "confirmation": {
            "confirmation_id": "confirm-outbound-1",
            "endpoint_digest": endpoint_d,
            "material_kinds_digest": kinds_d,
            "source_snippets_enabled": False,
            "confirmed_at_commit": "commit-1",
        },
    }


def test_request_model_draft_succeeds_with_injected_ports(
    workspace_root: Path,
) -> None:
    """三端口齐备时出站成功：意图+结果+草稿落盘，返回草稿元数据。"""
    caller = MemoryModelCaller(draft_text="draft: proposed checks")
    api = LocalAPI(instance_id="core-ab001", workspace_id=WORKSPACE_ID, handlers={})
    register_b_use_cases(
        api,
        _deps(
            workspace_root,
            projector=MemoryProjector(),
            caller=caller,
            credentials=MemoryCredentialResolver(available_purposes=("model",)),
        ),
    )
    response = api.dispatch(
        _command(
            "request_model_draft",
            {
                "policy": _policy_payload(),
                "task_type": "context_summary",
                "selected_material": {"project_context": "订单项目的上下文材料"},
                "source_revision": 1,
                "base_manual_revision": 1,
                "generation_request_id": "gen-1",
            },
        ),
        _session(),
    )
    assert response.error is None
    assert response.result is not None
    assert response.result["status"] == "draft_ready"
    assert response.result["request_id"] == f"outbound:{PROJECT_ID}:gen-1"
    content = response.result["content"]
    assert isinstance(content, dict)
    assert content["status"] == "draft"
    assert len(caller.calls) == 1


def test_request_model_draft_without_ports_is_structured_error(
    workspace_root: Path,
) -> None:
    """模型三端口未注入：结构化"未配置"，不得变成 INTERNAL_ERROR。"""
    api = LocalAPI(instance_id="core-ab001", workspace_id=WORKSPACE_ID, handlers={})
    register_b_use_cases(api, _deps(workspace_root))
    response = api.dispatch(
        _command(
            "request_model_draft",
            {
                "policy": _policy_payload(),
                "task_type": "context_summary",
                "selected_material": {"project_context": "订单项目的上下文材料"},
                "source_revision": 1,
                "base_manual_revision": 1,
            },
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_DEPENDENCY_NOT_CONFIGURED"


def test_request_model_draft_rejects_unknown_material_kind(
    workspace_root: Path,
) -> None:
    api = LocalAPI(instance_id="core-ab001", workspace_id=WORKSPACE_ID, handlers={})
    register_b_use_cases(
        api,
        _deps(
            workspace_root,
            projector=MemoryProjector(),
            caller=MemoryModelCaller(),
            credentials=MemoryCredentialResolver(),
        ),
    )
    policy = _policy_payload()
    policy["allowed_material_kinds"] = ["not_a_kind"]
    response = api.dispatch(
        _command(
            "request_model_draft",
            {
                "policy": policy,
                "task_type": "context_summary",
                "selected_material": {"project_context": "材料"},
                "source_revision": 1,
                "base_manual_revision": 1,
            },
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"


# ------------------------------------------------------------------ revise_pending_steps


def _case_payload(
    *,
    case_id: str = "case-1",
    revision: int = 1,
    steps: tuple[str, ...] = ("创建订单", "查询订单"),
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "revision": revision,
        "layer": CaseLayer.L2.value,
        "objective": "确认订单创建链路可用",
        "preconditions": ["订单服务已启动"],
        "inputs": ["下单请求"],
        "steps": list(steps),
        "expected": "订单状态为已支付",
        "verification_method": "命令输出比对",
        "links": {
            "acceptance_item_ids": ["AC-01"],
            "module_ids": [],
            "environment_ids": [],
            "critical_path_ids": ["path-1"],
            "no_critical_path_reason": None,
        },
        "assertion_basis": {
            "revision": revision,
            "state": AssertionBasisState.PRESENT_UNCONFIRMED.value,
            "text": _BASIS_TEXT,
            "text_digest": _BASIS_DIGEST,
        },
        "independent_verification": "查询订单库核对状态",
        "mock_scope": [],
        "importance": "P1",
    }


def _plan_payload() -> dict[str, object]:
    return {
        "plan_id": "plan-1",
        "revision": 1,
        "scope": {
            "scope_id": "scope-1",
            "revision": 1,
            "name": "订单模块回归",
            "required_case_ids": ["case-1"],
            "template_case_ids": ["case-1"],
            "objective": "",
            "excluded_case_ids": [],
            "exclusion_reasons": [],
            "dependency_closure_ids": [],
            "applicability_exclusions": [],
        },
        "case_revisions": [
            {"case_id": "case-1", "revision": 1, "digest": "sha256:c1"}
        ],
        "rule_revisions": [
            {"rule_id": "rule-1", "revision": 1, "digest": "sha256:r1"}
        ],
        "template_versions": [
            {"template_id": "http-workflow", "version": "1.0.0", "digest": "sha256:t"}
        ],
        "run_tier": RunTier.FULL.value,
        "initial_driver": RunDriver.PLANNED.value,
        "status": PlanPublicationStatus.PUBLISHED.value,
        "confirmation_id": "confirm-1",
    }


def _request_payload(facts: Mapping[str, object]) -> dict[str, object]:
    next_case = _case_payload(revision=2, steps=("创建订单", "查询订单", "补充步骤-2"))
    return {
        "base_plan_revision_id": "plan-1",
        "base_plan_revision_no": 1,
        "base_plan_revision_digest": "sha256:plan-1",
        "observed_snapshot_cursor": facts["snapshot_cursor"],
        "case_changes": [{"next_case": next_case}],
        "reason": "上游变更，需要调整未执行步骤",
        "operator_ref": "operator-1",
    }


def _running_facts() -> dict[str, object]:
    """由交付夹具派生的"运行中"快照（交付夹具无 running 取值）。"""
    payload = json.loads((FIXTURES / "failure.json").read_text(encoding="utf-8"))
    payload["run"]["control_state"] = "running"
    return cast("dict[str, object]", payload)


def test_revise_pending_steps_accepts_pending_change(workspace_root: Path) -> None:
    facts = _running_facts()
    api = LocalAPI(instance_id="core-ab001", workspace_id=WORKSPACE_ID, handlers={})
    register_b_use_cases(api, _deps(workspace_root))
    response = api.dispatch(
        _command(
            "revise_pending_steps",
            {
                "plan": _plan_payload(),
                "cases": [_case_payload()],
                "confirmations": [],
                "request": _request_payload(facts),
                "facts": facts,
            },
        ),
        _session(),
    )
    assert response.error is None
    assert response.result is not None
    assert response.result["accepted"] is True
    assert response.result["refusals"] == []
    assert isinstance(response.result["revision_no"], int)


def test_revise_pending_steps_rejects_stale_snapshot(workspace_root: Path) -> None:
    """快照游标不一致即拒绝（不凭旧快照修订）。"""
    facts = _running_facts()
    request = _request_payload(facts)
    request["observed_snapshot_cursor"] = 999
    api = LocalAPI(instance_id="core-ab001", workspace_id=WORKSPACE_ID, handlers={})
    register_b_use_cases(api, _deps(workspace_root))
    response = api.dispatch(
        _command(
            "revise_pending_steps",
            {
                "plan": _plan_payload(),
                "cases": [_case_payload()],
                "confirmations": [],
                "request": request,
                "facts": facts,
            },
        ),
        _session(),
    )
    assert response.error is None
    assert response.result is not None
    assert response.result["accepted"] is False
    refusals = cast("list[dict[str, object]]", response.result["refusals"])
    codes = {str(item["code"]) for item in refusals}
    assert "stale_snapshot" in codes


def test_revise_pending_steps_rejects_malformed_case(workspace_root: Path) -> None:
    facts = _running_facts()
    bad_case = _case_payload()
    bad_case["expected"] = ""
    api = LocalAPI(instance_id="core-ab001", workspace_id=WORKSPACE_ID, handlers={})
    register_b_use_cases(api, _deps(workspace_root))
    response = api.dispatch(
        _command(
            "revise_pending_steps",
            {
                "plan": _plan_payload(),
                "cases": [bad_case],
                "confirmations": [],
                "request": _request_payload(facts),
                "facts": facts,
            },
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"


# ------------------------------------------------------------------ 1. 默认装配


def test_default_assembly_injects_model_ports(tmp_path: Path) -> None:
    """默认装配：三端口进入 B 用例依赖，能力声明登记 `request_model_draft`。"""
    assembly = assemble_workspace_core(tmp_path, instance_id="core-ab001-model")
    try:
        gate = assembly.gate
        assert gate is not None
        # 能力声明：模型出站依赖真实模型调用与凭据解析（§8.16.3 第 4 条）。
        assert gate.dependencies_of("request_model_draft") == frozenset(
            {"model", "secret"}
        )
        # 纯规则动作不声明外部能力依赖。
        assert gate.dependencies_of("revise_pending_steps") == frozenset()
        assert gate.dependencies_of("narrow_driver") == frozenset()

        # 模型能力未配置：动作级降级（不是 INTERNAL_ERROR），其余动作不受影响。
        response = assembly.api.dispatch(
            Command(
                request_id="req-model",
                action="request_model_draft",
                project_id=PROJECT_ID,
                expected_revision=0,
                intent_id="intent-model",
                parameters={},
            ),
            Session(session_id="cli-ab001", entry_kind=EntryKind.HUMAN_UI),
        )
        assert response.error is not None
        assert response.error.code == "CAPABILITY_DEGRADED"

        doctor = assembly.api.dispatch(
            Command(request_id="req-doctor", action="doctor"),
            Session(session_id="cli-ab001", entry_kind=EntryKind.HUMAN_UI),
        )
        assert doctor.error is None
        assert doctor.result is not None
        raw_supported = cast("list[object]", doctor.result["supported_actions"])
        supported = {str(item) for item in raw_supported}
        assert {
            "request_model_draft",
            "revise_pending_steps",
            "narrow_driver",
        } <= supported
    finally:
        assembly.lifetime_lock.release()


def test_default_assembly_marks_model_not_configured(tmp_path: Path) -> None:
    assembly = assemble_workspace_core(tmp_path, instance_id="core-ab001-gate")
    try:
        gate = assembly.gate
        assert gate is not None
        # SECRET 默认就绪（环境变量/凭据管理器来源），MODEL 未显式配置。
        assert gate.condition("model").state is CapabilityState.NOT_CONFIGURED
        decision = gate.check("request_model_draft")
        assert decision.allowed is False
    finally:
        assembly.lifetime_lock.release()
