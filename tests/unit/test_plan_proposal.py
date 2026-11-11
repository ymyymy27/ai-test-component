"""Structured plan proposal: parse, coverage and server-owned materialisation."""

from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from aitest.application.planning.proposal import (
    PlanProposalError,
    materialize_coverage_contract,
    materialize_plan_proposal,
    parse_plan_proposal,
    proposal_from_mapping,
    proposal_preview,
    validate_coverage_contract_against_understanding,
    validate_plan_proposal,
)
from aitest.application.project.understanding import scan_sources
from aitest.application.review.function_coverage_preview import (
    build_plan_function_coverage,
)
from aitest.contracts.plan_proposal import PlanProposal
from aitest.domain.planning.plans import validate_coverage_contract
from aitest.domain.planning.understanding import (
    CapabilityGap,
    CoverageClaimStatus,
    ProjectUnderstanding,
)


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


def test_ai_expected_facts_become_the_machine_readable_basis() -> None:
    payload = _proposal()
    first = copy.deepcopy(payload["cases"][0])
    first["expected_facts"] = {"sum": 5}
    payload["cases"][0] = first
    proposal = PlanProposal.model_validate(payload)
    _scope, materialised = materialize_plan_proposal(
        proposal,
        project_id="demo",
        environment_id="env-demo",
        scope_id="scope-demo",
    )
    edited = next(case for case in materialised if case.case_id == "case-syntax")
    assert json.loads(edited.assertion_basis.text) == {
        "schema_version": "aitest.pure-function-basis/1",
        "expected_facts": {"sum": 5},
    }


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
    assert preview["coverage_preview"] == []
    assert "understanding_revision" not in preview["proposal"]


# ------------------------------------------------------ 1.1 understanding link


def _understanding() -> ProjectUnderstanding:
    return scan_sources(
        files={"app/lib.py": b"def create_order(amount):\n    return amount\n"},
        source_snapshot_id="snap-1",
        understanding_id="understanding-1",
        revision=2,
        scope="project",
    )


def _intent_and_function(understanding: ProjectUnderstanding) -> tuple[str, str, str]:
    intent = understanding.test_intents[0]
    function_id = sorted(intent.function_ids)[0]
    function = next(
        item
        for item in understanding.catalog.functions
        if item.function_id == function_id
    )
    return intent.intent_id, function_id, function.required_dimensions[0]


def _v11_payload(understanding: ProjectUnderstanding) -> dict[str, object]:
    intent_id, function_id, dimension = _intent_and_function(understanding)
    payload = _proposal()
    payload["schema_version"] = "aitest.plan-proposal/1.1"
    payload["understanding_revision"] = understanding.revision
    payload["goal_intent_ids"] = [intent_id]
    payload["template_dispositions"] = [
        {
            "template_item_id": item,
            "applicability": "applicable",
            "coverage": "covered",
        }
        for item in ("syntax", "normal", "boundary", "error")
    ]
    payload["coverage_claims"] = [
        {
            "intent_id": intent_id,
            "function_id": function_id,
            "dimension": dimension,
            "case_ids": ["case-normal"],
        }
    ]
    return payload


def test_v11_proposal_accepts_understanding_link_and_claims() -> None:
    understanding = _understanding()
    proposal = PlanProposal.model_validate(_v11_payload(understanding))
    assert proposal.schema_version == "aitest.plan-proposal/1.1"
    assert proposal.understanding_revision == 2
    assert proposal.goal_intent_ids == [understanding.test_intents[0].intent_id]
    assert proposal.coverage_claims[0].case_ids == ["case-normal"]


def test_v11_claim_materialises_a_server_owned_coverage_contract() -> None:
    understanding = _understanding()
    proposal = PlanProposal.model_validate(_v11_payload(understanding))
    contract = materialize_coverage_contract(
        proposal, understanding, plan_revision=3
    )

    validate_coverage_contract(contract)
    assert contract.understanding_revision == understanding.revision
    assert contract.contract_id.startswith("plan-coverage-contract:3:")
    functions = {
        requirement.function_id: requirement.required_dimensions
        for requirement in contract.function_requirements
    }
    function = understanding.catalog.functions[0]
    assert functions == {function.function_id: function.required_dimensions}

    claim = contract.coverage_claims[0]
    assert claim.claim_id.startswith("plan-claim:3:")
    assert claim.status is CoverageClaimStatus.NOT_PROVEN
    assert claim.plan_revision == 3
    assert claim.case_ids == frozenset({"case-normal"})
    assert claim.intent_id == understanding.test_intents[0].intent_id
    assert contract.template_dispositions == ()


def test_v11_contract_accepts_caller_template_dispositions() -> None:
    from aitest.domain.planning.plans import (
        TemplateApplicability,
        TemplateCoverage,
        TemplateDisposition,
    )

    understanding = _understanding()
    proposal = PlanProposal.model_validate(_v11_payload(understanding))
    disposition = TemplateDisposition(
        "syntax", TemplateApplicability.APPLICABLE, TemplateCoverage.COVERED
    )
    contract = materialize_coverage_contract(
        proposal,
        understanding,
        plan_revision=1,
        template_dispositions=(disposition,),
    )
    assert contract.template_dispositions == (disposition,)


def test_v11_claim_intent_must_be_one_of_goal_intents() -> None:
    understanding = _understanding()
    payload = _v11_payload(understanding)
    payload["coverage_claims"] = [
        {
            **_v11_payload(understanding)["coverage_claims"][0],  # type: ignore[index]
            "intent_id": "intent-not-selected",
        }
    ]
    with pytest.raises(PlanProposalError) as error:
        proposal_from_mapping(payload)
    assert any("goal_intent_ids" in issue.message for issue in error.value.issues)


def test_v11_goal_intent_must_exist_in_the_understanding() -> None:
    understanding = _understanding()
    payload = _v11_payload(understanding)
    payload["goal_intent_ids"] = ["intent-from-another-understanding"]
    payload["coverage_claims"] = [
        {
            **_v11_payload(understanding)["coverage_claims"][0],  # type: ignore[index]
            "intent_id": "intent-from-another-understanding",
        }
    ]
    proposal = PlanProposal.model_validate(payload)
    with pytest.raises(PlanProposalError) as error:
        materialize_coverage_contract(proposal, understanding, plan_revision=1)
    assert any("unknown test intent" in issue.message for issue in error.value.issues)


def test_v11_claim_with_unknown_function_is_rejected() -> None:
    understanding = _understanding()
    payload = _v11_payload(understanding)
    payload["coverage_claims"] = [
        {
            **_v11_payload(understanding)["coverage_claims"][0],  # type: ignore[index]
            "function_id": "fn-unknown",
        }
    ]
    proposal = PlanProposal.model_validate(payload)
    with pytest.raises(PlanProposalError) as error:
        materialize_coverage_contract(proposal, understanding, plan_revision=1)
    assert any(
        "unknown business function" in issue.message for issue in error.value.issues
    )


def test_v11_claim_function_must_be_linked_to_the_intent() -> None:
    understanding = scan_sources(
        files={
            "app/lib.py": (
                b"def create_order(amount):\n    return amount\n"
                b"\ndef cancel_order(order_id):\n    return order_id\n"
            )
        },
        source_snapshot_id="snap-1",
        understanding_id="understanding-1",
        revision=2,
        scope="project",
    )
    payload = _v11_payload(understanding)
    intent_id = payload["goal_intent_ids"][0]  # type: ignore[index]
    intent = next(
        item for item in understanding.test_intents if item.intent_id == intent_id
    )
    other = next(
        item
        for item in understanding.catalog.functions
        if item.function_id not in intent.function_ids
    )
    payload["coverage_claims"] = [
        {
            **_v11_payload(understanding)["coverage_claims"][0],  # type: ignore[index]
            "function_id": other.function_id,
            "dimension": other.required_dimensions[0],
        }
    ]
    proposal = PlanProposal.model_validate(payload)
    with pytest.raises(PlanProposalError) as error:
        materialize_coverage_contract(proposal, understanding, plan_revision=1)
    assert any(
        "not linked to intent" in issue.message for issue in error.value.issues
    )


def test_v11_claim_dimension_must_be_required_by_the_function() -> None:
    understanding = _understanding()
    payload = _v11_payload(understanding)
    payload["coverage_claims"] = [
        {
            **_v11_payload(understanding)["coverage_claims"][0],  # type: ignore[index]
            "dimension": "concurrency",
        }
    ]
    proposal = PlanProposal.model_validate(payload)
    with pytest.raises(PlanProposalError) as error:
        materialize_coverage_contract(proposal, understanding, plan_revision=1)
    assert any("not required" in issue.message for issue in error.value.issues)


def test_v11_claim_case_must_exist_in_the_proposal_cases() -> None:
    understanding = _understanding()
    payload = _v11_payload(understanding)
    payload["coverage_claims"] = [
        {
            **_v11_payload(understanding)["coverage_claims"][0],  # type: ignore[index]
            "case_ids": ["case-missing"],
        }
    ]
    with pytest.raises(PlanProposalError) as error:
        proposal_from_mapping(payload)
    assert any("outside this proposal" in issue.message for issue in error.value.issues)


def test_v11_duplicate_intent_function_dimension_claim_is_rejected() -> None:
    understanding = _understanding()
    payload = _v11_payload(understanding)
    payload["coverage_claims"] = [
        *_v11_payload(understanding)["coverage_claims"],  # type: ignore[misc]
        *_v11_payload(understanding)["coverage_claims"],  # type: ignore[misc]
    ]
    with pytest.raises(PlanProposalError) as error:
        proposal_from_mapping(payload)
    assert any("duplicate coverage claim" in issue.message for issue in error.value.issues)


def test_v11_requires_understanding_revision() -> None:
    understanding = _understanding()
    payload = _v11_payload(understanding)
    payload.pop("understanding_revision")
    with pytest.raises(PlanProposalError) as error:
        proposal_from_mapping(payload)
    assert any(
        "understanding_revision" in issue.message for issue in error.value.issues
    )


def test_v11_requires_at_least_one_unique_goal_intent() -> None:
    understanding = _understanding()
    payload = _v11_payload(understanding)
    payload["goal_intent_ids"] = []
    with pytest.raises(PlanProposalError):
        proposal_from_mapping(payload)

    payload = _v11_payload(understanding)
    intent_id = payload["goal_intent_ids"][0]  # type: ignore[index]
    payload["goal_intent_ids"] = [intent_id, intent_id]
    with pytest.raises(PlanProposalError) as error:
        proposal_from_mapping(payload)
    assert any("unique" in issue.message for issue in error.value.issues)


def test_v10_with_v11_fields_is_rejected_even_at_defaults() -> None:
    payload = _proposal()
    payload["understanding_revision"] = None
    with pytest.raises(PlanProposalError) as error:
        proposal_from_mapping(payload)
    assert any("1.0" in issue.message for issue in error.value.issues)


def test_v11_claim_cannot_supply_server_owned_fields() -> None:
    understanding = _understanding()
    payload = _v11_payload(understanding)
    claim = dict(payload["coverage_claims"][0])  # type: ignore[index]
    claim["claim_id"] = "model-chosen-id"
    payload["coverage_claims"] = [claim]
    with pytest.raises(Exception) as error:
        PlanProposal.model_validate(payload)
    assert "extra" in str(error.value).lower() or "claim_id" in str(error.value)


def test_v11_preview_coverage_matches_the_materialised_contract() -> None:
    understanding = _understanding()
    proposal = PlanProposal.model_validate(_v11_payload(understanding))
    preview = proposal_preview(
        proposal,
        project_id="demo",
        environment_id="env-demo",
        scope_id="scope-demo",
        understanding=understanding,
    )
    contract = materialize_coverage_contract(
        proposal, understanding, plan_revision=1
    )
    expected = [
        {
            "function_id": item.function_id,
            "overall_status": item.overall_coverage.value,
            "dimensions": {
                dimension: status.value
                for dimension, status in item.coverage_by_dimension.items()
            },
            "gap_reason": item.gap_reason,
        }
        for item in build_plan_function_coverage(contract, plan_revision=1)
    ]
    assert preview["coverage_preview"] == expected
    assert expected
    assert expected[0]["gap_reason"] == "no coverage claim"
    assert preview["proposal"]["schema_version"] == "aitest.plan-proposal/1.1"


def test_v11_preview_without_understanding_does_not_fake_coverage() -> None:
    understanding = _understanding()
    proposal = PlanProposal.model_validate(_v11_payload(understanding))
    preview = proposal_preview(
        proposal,
        project_id="demo",
        environment_id="env-demo",
        scope_id="scope-demo",
    )
    assert preview["coverage_preview"] == []


def test_v11_not_applicable_and_ai_added_case_are_first_class():
    understanding = _understanding()
    payload = _v11_payload(understanding)
    payload["cases"] = [item for item in payload["cases"] if item["case_id"] != "case-syntax"]
    for disposition in payload["template_dispositions"]:
        if disposition["template_item_id"] == "syntax":
            disposition.update(
                applicability="not_applicable", coverage="uncovered", reason="无语法入口"
            )
    extra = _case("unused", "L2", "case-extra")
    extra["template_item_id"] = None
    extra["origin"] = "ai"
    extra["links"]["acceptance_item_ids"] = []
    payload["cases"].append(extra)
    intent_id, function_id, dimension = _intent_and_function(understanding)
    function = next(
        item for item in understanding.catalog.functions if item.function_id == function_id
    )
    other_dimension = next(
        item for item in function.required_dimensions if item != dimension
    )
    payload["coverage_claims"].append(
        {
            "intent_id": intent_id,
            "function_id": function_id,
            "dimension": other_dimension,
            "case_ids": ["case-extra"],
        }
    )
    payload["cases"][0]["data_mutation_units"] = 5
    proposal = proposal_from_mapping(payload)
    scope, cases = materialize_plan_proposal(
        proposal,
        project_id="demo",
        environment_id="env-demo",
        scope_id="scope-demo",
    )
    assert "case-normal" in {case.case_id for case in cases}
    assert "case-syntax" not in {case.case_id for case in cases}
    extra_case = next(case for case in cases if case.case_id == "case-extra")
    assert extra_case.template_item_id is None
    changed = next(
        case for case in cases if case.case_id == payload["cases"][0]["case_id"]
    )
    assert changed.data_mutation_units == 5
    assert "case-extra" not in scope.template_case_ids


def test_v11_multiple_ai_cases_may_share_a_missing_template_item():
    payload = _v11_payload(_understanding())
    for suffix in ("one", "two"):
        extra = _case("unused", "L2", "case-ai-" + suffix)
        extra["template_item_id"] = None
        extra["origin"] = "ai"
        extra["links"]["acceptance_item_ids"] = []
        payload["cases"].append(extra)
    proposal = PlanProposal.model_validate(payload)
    assert [case.template_item_id for case in proposal.cases].count(None) == 2


def test_v11_missing_template_disposition_is_rejected():
    understanding = _understanding()
    payload = _v11_payload(understanding)
    payload["template_dispositions"] = payload["template_dispositions"][:-1]
    proposal = PlanProposal.model_validate(payload)
    with pytest.raises(PlanProposalError) as error:
        validate_plan_proposal(proposal)
    assert any("missing disposition" in issue.message for issue in error.value.issues)


def test_full_blocks_unknown_and_unsupported_understanding():
    understanding = _understanding()
    proposal = PlanProposal.model_validate(_v11_payload(understanding))
    contract = materialize_coverage_contract(
        proposal,
        understanding,
        plan_revision=1,
        template_dispositions=(),
    )
    unknown = replace(understanding, unknown_areas=("legacy job runner",))
    with pytest.raises(PlanProposalError, match="unknown project areas"):
        validate_coverage_contract_against_understanding(
            contract, unknown, plan_revision=1, require_all_functions=True
        )
    gap = CapabilityGap(
        gap_id="gap-1",
        scope="project",
        missing_capability="legacy runner",
        capability_revision="cap-1",
        reason="not supported",
        workaround=None,
        blocks_full=True,
        source_refs=("src",),
    )
    blocked = replace(understanding, capability_gaps=(gap,))
    with pytest.raises(PlanProposalError, match="unsupported capabilities"):
        validate_coverage_contract_against_understanding(
            contract, blocked, plan_revision=1, require_all_functions=True
        )


def test_v11_proposal_without_dispositions_remains_readable_for_ondemand():
    understanding = _understanding()
    payload = _v11_payload(understanding)
    payload.pop("template_dispositions")
    proposal = proposal_from_mapping(payload)
    scope, cases = materialize_plan_proposal(
        proposal,
        project_id="demo",
        environment_id="env-demo",
        scope_id="scope-demo",
    )
    assert len(cases) == 4
    assert scope.template_case_ids == frozenset()
