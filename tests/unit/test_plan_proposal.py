"""Structured plan proposal: parse, coverage and server-owned materialisation."""

from __future__ import annotations

import copy
import json

import pytest

from aitest.application.planning.proposal import (
    PlanProposalError,
    materialize_plan_proposal,
    parse_plan_proposal,
    proposal_preview,
)
from aitest.contracts.plan_proposal import PlanProposal


def _case(item: str, layer: str, case_id: str) -> dict[str, object]:
    return {
        "case_id": case_id,
        "template_item_id": item,
        "layer": layer,
        "objective": f"验证 {item}",
        "preconditions": ["已登记解释器与入口"],
        "inputs": ["输入示例"],
        "steps": ["运行已登记入口"],
        "expected": "与独立预期一致",
        "verification_method": "独立比对保存输出",
        "links": {
            "acceptance_item_ids": [item],
            "module_ids": [],
            "critical_path_ids": [],
            "no_critical_path_reason": "纯函数用例无跨系统链路",
        },
        "assertion_basis_text": f"{item} 的断言依据：独立预期由需求给出，不从运行结果反推",
        "independent_verification": "比较保存输出与冻结预期",
        "mock_scope": [],
        "importance": "P1",
    }


def _proposal() -> dict[str, object]:
    return {
        "schema_version": "aitest.plan-proposal/1.0",
        "template_id": "python-library",
        "template_version": "1.0.0",
        "scope": {
            "name": "mathx 库验证",
            "objective": "验证 clamp 的正常、边界和异常行为",
            "required_case_ids": [],
            "excluded_case_ids": [],
            "exclusion_reasons": [],
            "dependency_closure_ids": [],
            "applicability_exclusions": [],
        },
        "cases": [
            _case("syntax", "L1", "case-syntax"),
            _case("normal", "L2", "case-normal"),
            _case("boundary", "L2", "case-boundary"),
            _case("error", "L2", "case-error"),
        ],
    }


def _materialize(payload: dict[str, object]) -> tuple[object, object]:
    proposal = PlanProposal.model_validate(payload)
    return materialize_plan_proposal(
        proposal,
        project_id="demo",
        environment_id="env-demo",
        scope_id="scope-demo",
    )


def test_valid_proposal_materialises_template_coverage() -> None:
    scope, cases = _materialize(_proposal())
    assert sorted(scope.required_case_ids) == [
        "case-boundary",
        "case-error",
        "case-normal",
        "case-syntax",
    ]
    assert scope.template_case_ids == scope.required_case_ids
    assert [case.case_id for case in cases] == [
        "case-boundary",
        "case-error",
        "case-normal",
        "case-syntax",
    ]
    assert all(case.revision == 1 for case in cases)
    assert all(case.assertion_basis.state.value == "present_unconfirmed" for case in cases)
    assert all(
        case.links.environment_ids == frozenset({"env-demo"}) for case in cases
    )


def test_code_fence_is_tolerated_but_other_prose_is_not() -> None:
    payload = _proposal()
    fenced = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    assert parse_plan_proposal(fenced).template_id == "python-library"
    with pytest.raises(PlanProposalError) as error:
        parse_plan_proposal("Here is the plan:\n" + json.dumps(payload))
    assert error.value.code == "PLAN_PROPOSAL_INVALID_JSON"


def test_missing_template_item_is_rejected_with_issue() -> None:
    payload = _proposal()
    cases = list(payload["cases"])  # type: ignore[arg-type]
    payload["cases"] = cases[:-1]
    with pytest.raises(PlanProposalError) as error:
        _materialize(payload)
    assert any("error" in issue.message for issue in error.value.issues)


def test_wrong_layer_is_rejected() -> None:
    payload = _proposal()
    cases = list(payload["cases"])  # type: ignore[arg-type]
    first = copy.deepcopy(cases[0])
    first["layer"] = "L2"
    cases[0] = first
    payload["cases"] = cases
    with pytest.raises(PlanProposalError) as error:
        _materialize(payload)
    assert any("layer" in issue.path for issue in error.value.issues)


def test_excluding_required_template_item_is_rejected() -> None:
    payload = _proposal()
    scope = dict(payload["scope"])  # type: ignore[arg-type]
    scope["excluded_case_ids"] = ["case-syntax"]
    scope["exclusion_reasons"] = [["case-syntax", "不需要"]]
    payload["scope"] = scope
    with pytest.raises(PlanProposalError) as error:
        _materialize(payload)
    assert any("required template" in issue.message for issue in error.value.issues)


def test_exclusion_without_reason_is_rejected() -> None:
    payload = _proposal()
    scope = dict(payload["scope"])  # type: ignore[arg-type]
    scope["excluded_case_ids"] = ["case-error"]
    payload["scope"] = scope
    with pytest.raises(PlanProposalError) as error:
        _materialize(payload)
    assert any("reason" in issue.message for issue in error.value.issues)


def test_model_cannot_supply_server_owned_digest() -> None:
    payload = _proposal()
    cases = list(payload["cases"])  # type: ignore[arg-type]
    first = copy.deepcopy(cases[0])
    first["assertion_basis"] = {"text_digest": "sha256:" + "0" * 64}
    cases[0] = first
    payload["cases"] = cases
    with pytest.raises(Exception) as error:
        PlanProposal.model_validate(payload)
    assert "extra" in str(error.value).lower() or "assertion_basis" in str(error.value)


def test_edited_proposal_keeps_edits_and_recomputes_digest() -> None:
    payload = _proposal()
    cases = list(payload["cases"])  # type: ignore[arg-type]
    first = copy.deepcopy(cases[0])
    first["expected"] = "手工修改后的预期"
    first["assertion_basis_text"] = "手工修改后的依据"
    cases[0] = first
    payload["cases"] = cases
    proposal = PlanProposal.model_validate(payload)
    _scope, materialised = materialize_plan_proposal(
        proposal,
        project_id="demo",
        environment_id="env-demo",
        scope_id="scope-demo",
    )
    edited = next(case for case in materialised if case.case_id == "case-syntax")
    assert edited.expected == "手工修改后的预期"
    assert edited.assertion_basis.text == "手工修改后的依据"
    assert edited.assertion_basis.text_digest.startswith("sha256:")


def test_required_scope_cannot_omit_a_non_excluded_case() -> None:
    payload = _proposal()
    scope = dict(payload["scope"])  # type: ignore[arg-type]
    scope["required_case_ids"] = ["case-syntax", "case-normal", "case-boundary"]
    payload["scope"] = scope
    with pytest.raises(PlanProposalError) as error:
        _materialize(payload)
    assert any("non-excluded" in issue.message for issue in error.value.issues)


def test_preview_returns_full_case_payloads_without_writing() -> None:
    preview = proposal_preview(
        PlanProposal.model_validate(_proposal()),
        project_id="demo",
        environment_id="env-demo",
        scope_id="scope-demo",
    )
    assert preview["coverage"]["covered_items"] == 4
    assert len(preview["cases"]) == 4  # type: ignore[arg-type]
    assert preview["proposal"]["schema_version"] == "aitest.plan-proposal/1.0"
