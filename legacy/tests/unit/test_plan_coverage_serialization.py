"""覆盖契约接入计划 payload、反序列化、摘要与发布门禁。

依据：设计文档 03 第 4.5、5.2、5.5 节；需求 P1-FR04、P1-FR06、P1-AC24；
架构文档《01-项目与计划》第 3 节。仅覆盖发布/序列化/摘要，不接入 full 门禁、
prepare、basis_validation 或报告。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from aitest.application.planning.publish import (
    _plan_payload,
    payload_digest,
    plan_publication_digest,
    publish_plan,
)
from aitest.application.planning.serialization import (
    coverage_contract_from_payload,
    coverage_contract_to_payload,
    plan_coverage_contract_from_payload,
)
from aitest.domain.planning.plans import (
    FunctionRequirement,
    TemplateApplicability,
    TemplateCoverage,
    validate_plan_publication,
)
from aitest.domain.planning.understanding import CoverageClaimStatus
from tests.support.memory_substrate import MemoryReader, MemoryStore, MemoryUnitOfWork
from tests.unit.test_plan_coverage_contract import _case, _claim, _contract, _plan


def _world() -> tuple[MemoryUnitOfWork, MemoryReader]:
    store = MemoryStore()
    return MemoryUnitOfWork(store), MemoryReader(store)


# ------------------------------------------------------------ 旧 payload


def test_old_plan_payload_reads_as_no_contract() -> None:
    payload = _plan_payload(_plan(), [_case()], project_id="project-1")

    assert "coverage_contract" not in payload
    assert plan_coverage_contract_from_payload(payload) is None


def test_explicit_null_contract_reads_as_no_contract() -> None:
    assert plan_coverage_contract_from_payload({"coverage_contract": None}) is None


def test_malformed_contract_in_plan_payload_is_rejected() -> None:
    with pytest.raises(ValueError, match="coverage_contract must be an object"):
        plan_coverage_contract_from_payload({"coverage_contract": "not-an-object"})


# ------------------------------------------------------------ round-trip


def test_new_plan_payload_round_trips_the_contract() -> None:
    contract = _contract()
    plan = _plan(coverage_contract=contract)

    payload = _plan_payload(plan, [_case()], project_id="project-1")

    assert "coverage_contract" in payload
    assert payload["coverage_contract"] == coverage_contract_to_payload(contract)
    assert plan_coverage_contract_from_payload(payload) == contract
    assert coverage_contract_from_payload(payload["coverage_contract"]) == contract


def test_plan_type_adapter_round_trips_contract() -> None:
    """冻结计划/执行路径用 TypeAdapter(Plan) 读回子对象，不能丢契约。"""
    from pydantic import TypeAdapter

    from aitest.domain.planning.plans import Plan

    plan = _plan(coverage_contract=_contract())
    parsed = TypeAdapter(Plan).validate_json(
        TypeAdapter(Plan).dump_json(plan), strict=True
    )
    assert parsed.coverage_contract == plan.coverage_contract


def test_contract_with_claims_round_trips() -> None:
    contract = _contract(
        coverage_claims=(
            _claim(),
            _claim(
                claim_id="claim-2",
                function_id="billing.refund",
                dimension="boundary",
                case_ids=frozenset({"case-2"}),
            ),
        )
    )

    payload = coverage_contract_to_payload(contract)

    claims = payload["coverage_claims"]
    assert isinstance(claims, list)
    assert [item["claim_id"] for item in claims] == ["claim-1", "claim-2"]
    assert coverage_contract_from_payload(payload) == contract


def test_plan_payload_round_trips_claims() -> None:
    contract = _contract(coverage_claims=(_claim(),))
    payload = _plan_payload(
        _plan(coverage_contract=contract), [_case()], project_id="project-1"
    )

    assert plan_coverage_contract_from_payload(payload) == contract


def test_contract_without_claims_keeps_old_payload_shape() -> None:
    """空 claims 不写键：旧契约 payload 的键集合与摘要保持原样。"""
    payload = coverage_contract_to_payload(_contract(coverage_claims=()))

    assert set(payload) == {
        "contract_id",
        "understanding_revision",
        "function_requirements",
        "template_dispositions",
        "critical_path_ids",
    }
    assert "coverage_claims" not in payload
    assert coverage_contract_from_payload(payload).coverage_claims == ()


def test_claim_status_string_values_are_read_back() -> None:
    raw = {
        "contract_id": "contract-1",
        "understanding_revision": 2,
        "function_requirements": [
            {
                "function_id": "billing.create",
                "required_dimensions": ["normal", "error"],
            }
        ],
        "template_dispositions": [],
        "critical_path_ids": [],
        "coverage_claims": [
            {
                "claim_id": "claim-1",
                "intent_id": "intent-1",
                "function_id": "billing.create",
                "dimension": "normal",
                "plan_revision": 1,
                "status": "partial",
                "case_ids": ["case-1"],
            }
        ],
    }

    contract = coverage_contract_from_payload(raw)

    assert contract.coverage_claims[0].status is CoverageClaimStatus.PARTIAL
    assert contract.coverage_claims[0].case_ids == frozenset({"case-1"})


def test_enum_string_values_are_read_back() -> None:
    raw = {
        "contract_id": "contract-1",
        "understanding_revision": 2,
        "function_requirements": [
            {
                "function_id": "billing.create",
                "required_dimensions": ["normal", "error"],
            }
        ],
        "template_dispositions": [
            {
                "template_item_id": "tpl-http-status",
                "applicability": "applicable",
                "coverage": "covered",
            }
        ],
        "critical_path_ids": ["path-write"],
    }

    contract = coverage_contract_from_payload(raw)

    assert (
        contract.template_dispositions[0].applicability
        is TemplateApplicability.APPLICABLE
    )
    assert contract.template_dispositions[0].coverage is TemplateCoverage.COVERED


def test_missing_required_array_is_rejected() -> None:
    raw = {
        "contract_id": "contract-1",
        "understanding_revision": 2,
        "function_requirements": [
            {"function_id": "billing.create"}
        ],
        "template_dispositions": [],
        "critical_path_ids": [],
    }

    with pytest.raises(ValueError, match="required_dimensions is required"):
        coverage_contract_from_payload(raw)


# ------------------------------------------------------------ 摘要


def test_digest_binds_only_contract_differences() -> None:
    case = _case()
    plain = _plan()
    with_contract = replace(plain, coverage_contract=_contract())
    other_contract = replace(
        plain, coverage_contract=_contract(contract_id="contract-2")
    )

    plain_digest = plan_publication_digest(plain, [case], project_id="p1")
    with_contract_digest = plan_publication_digest(
        with_contract, [case], project_id="p1"
    )
    other_digest = plan_publication_digest(other_contract, [case], project_id="p1")

    assert with_contract_digest != plain_digest
    assert with_contract_digest != other_digest


def test_digest_binds_claim_differences() -> None:
    case = _case()
    without_claims = replace(_plan(), coverage_contract=_contract())
    with_claims = replace(
        _plan(), coverage_contract=_contract(coverage_claims=(_claim(),))
    )

    assert plan_publication_digest(
        without_claims, [case], project_id="p1"
    ) != plan_publication_digest(with_claims, [case], project_id="p1")


def test_contract_payload_digest_is_key_order_independent() -> None:
    payload = coverage_contract_to_payload(_contract())
    reversed_payload = dict(reversed(list(payload.items())))

    assert payload_digest(reversed_payload) == payload_digest(payload)


# ------------------------------------------------------------ 发布门禁


def test_publication_gate_rejects_an_invalid_contract() -> None:
    invalid = _contract(
        function_requirements=(FunctionRequirement("billing.create", ()),)
    )
    plan = _plan(coverage_contract=invalid)

    with pytest.raises(ValueError, match="required_dimensions must not be empty"):
        validate_plan_publication(plan, [_case()])


def test_publish_plan_refuses_an_invalid_contract_without_writing() -> None:
    unit_of_work, reader = _world()
    invalid = _contract(
        function_requirements=(FunctionRequirement("billing.create", ()),)
    )
    result = publish_plan(
        _plan(coverage_contract=invalid),
        project_id="p1",
        cases=[_case()],
        unit_of_work=unit_of_work,
        reader=reader,
    )

    assert result.value is None
    assert any("required_dimensions" in reason for reason in result.blocked_by)
    assert unit_of_work.commit_seq() == "commit-0"


def test_published_plan_records_and_returns_the_contract() -> None:
    unit_of_work, reader = _world()
    contract = _contract()
    result = publish_plan(
        _plan(coverage_contract=contract),
        project_id="p1",
        cases=[_case()],
        unit_of_work=unit_of_work,
        reader=reader,
    )

    assert result.value is not None
    assert result.value.coverage_contract == contract
    record = reader.read(aggregate_kind="plan", record_id="plan-1", revision=1)
    assert plan_coverage_contract_from_payload(record.payload) == contract
