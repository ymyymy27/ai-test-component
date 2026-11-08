"""计划域对象的落盘形状：`Case` / `AcceptanceScope` / `ConfirmationRecord` /
`CoverageContract`。

为什么需要这一层
----------------

`publish.py` 落 `plan` 记录时嵌入的是**用例摘要**（`case_id`、`revision`、`layer`、
依据三态、独立核验、是否在范围内），足以让"计划当时冻结了哪些用例、哪些在范围内"
事后可核对；但它**不是用例本身**：

- 需要按**准确修订**读回某一条用例的完整内容（步骤、预期、核验方式、Mock 范围、
  关联项、依据文本与摘要）时，只有摘要读不出来；
- `CHANGELOG` 与接口文档都要求"独立 Case"可作为独立记录保存与查询。

因此本模块给出 `case` / `acceptance_scope` / `confirmation` 三类记录的**落盘形状**；
`CoverageContract` 是计划 payload 中的可选子对象，旧计划没有该键时读回 `None`。

记录标识约定（与既有 `rule_version` 一致）
----------------------------------------

| 对象 | `aggregate_kind` | `record_id` | 修订 |
| --- | --- | --- | --- |
| `Case` | `case` | `case_id` | `case.revision` |
| `AcceptanceScope` | `acceptance_scope` | `scope_id` | `scope.revision` |
| `ConfirmationRecord` | `case_link` | `confirmation_id` | 1（确认不再改写） |

即"同一对象的修订递增、旧修订永不覆盖"，与 `rule_version` 用 `rule_id` 当记录标识同一模式。

形状约定
--------

- **集合**（`frozenset` / `tuple`）在形状不适用时**真正省略该键**的规则只适用于
  `git`/`plain` 形态互斥；本模块的集合一律写成**排序后的 JSON 数组**，
  使"同一集合的不同书写顺序"得到同一字节；
- **可省略字符串**（`independent_verification`、`no_critical_path_reason`）为 `None` 时
  写 `null`，不写成空串——空串是有内容的字符串，二者语义不同；
- 读回时**缺字段一律抛错**，不填默认值：把读不懂的记录当成"没有记录"
  会让同键异输入覆盖既有材料（与 `preparation_record_from_payload()` 同一原则）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any

from aitest.domain.planning.plans import (
    AcceptanceScope,
    AssertionBasis,
    AssertionBasisState,
    Case,
    CaseImportance,
    CaseLayer,
    CaseLink,
    ConfirmationRecord,
    CoverageContract,
    FunctionRequirement,
    TemplateApplicability,
    TemplateCoverage,
    TemplateDisposition,
)
from aitest.domain.planning.understanding import CoverageClaim, CoverageClaimStatus

# ------------------------------------------------------------------ 读取辅助


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _text(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_text(payload: Mapping[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def _revision(payload: Mapping[str, Any], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if value < 1:
        raise ValueError(f"{name} must be >= 1")
    return value


def _text_tuple(payload: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = payload.get(name, [])
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list")
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name}[{index}] must be a non-empty string")
        out.append(item)
    return tuple(out)


def _text_set(payload: Mapping[str, Any], name: str) -> frozenset[str]:
    return frozenset(_text_tuple(payload, name))


def _pairs(payload: Mapping[str, Any], name: str) -> tuple[tuple[str, str], ...]:
    value = payload.get(name, [])
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list")
    out: list[tuple[str, str]] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ValueError(f"{name}[{index}] must be a [key, reason] pair")
        key, reason = entry
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{name}[{index}] needs a non-empty key")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{name}[{index}] needs a non-empty reason")
        out.append((key, reason))
    return tuple(out)


def _enum(enum: type[Any], payload: Mapping[str, Any], name: str) -> Any:
    raw = payload.get(name)
    if not isinstance(raw, str):
        raise ValueError(f"{name} must be a string")
    try:
        return enum(raw)
    except ValueError as error:
        raise ValueError(f"{name} has an unknown value: {raw!r}") from error


def _enum_text(enum: type[Any], value: object, name: str) -> str:
    """写盘时把 StrEnum 成员或字符串值统一成字符串值；非法值拒绝。"""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    try:
        parsed = enum(value)
    except ValueError as error:
        raise ValueError(f"{name} has an unknown value: {value!r}") from error
    text = parsed.value
    if not isinstance(text, str):
        raise ValueError(f"{name} does not use a string value")
    return text


def _required_text_tuple(payload: Mapping[str, Any], name: str) -> tuple[str, ...]:
    """必需的字符串数组；缺键与空元素都拒绝（不同于可省略集合的默认空）。"""
    if name not in payload:
        raise ValueError(f"{name} is required")
    return _text_tuple(payload, name)


def _mapping_list(
    payload: Mapping[str, Any], name: str
) -> list[Mapping[str, Any]]:
    value = payload.get(name)
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list")
    return [
        _require_mapping(item, f"{name}[{index}]")
        for index, item in enumerate(value)
    ]


def _optional_mapping_list(
    payload: Mapping[str, Any], name: str
) -> list[Mapping[str, Any]]:
    """可选对象数组；缺键时为空（只用于旧记录的兼容字段）。"""
    if name not in payload:
        return []
    return _mapping_list(payload, name)


# ------------------------------------------------------------------ CaseLink


def case_link_to_payload(link: CaseLink) -> dict[str, object]:
    return {
        "acceptance_item_ids": sorted(link.acceptance_item_ids),
        "module_ids": sorted(link.module_ids),
        "environment_ids": sorted(link.environment_ids),
        "critical_path_ids": sorted(link.critical_path_ids),
        "no_critical_path_reason": link.no_critical_path_reason,
    }


def case_link_from_payload(payload: Mapping[str, Any]) -> CaseLink:
    return CaseLink(
        acceptance_item_ids=_text_set(payload, "acceptance_item_ids"),
        module_ids=_text_set(payload, "module_ids"),
        environment_ids=_text_set(payload, "environment_ids"),
        critical_path_ids=_text_set(payload, "critical_path_ids"),
        no_critical_path_reason=_optional_text(payload, "no_critical_path_reason"),
    )


# ------------------------------------------------------------------ AssertionBasis


def assertion_basis_to_payload(basis: AssertionBasis) -> dict[str, object]:
    return {
        "revision": basis.revision,
        "state": basis.state.value,
        "text": basis.text,
        "text_digest": basis.text_digest,
    }


def assertion_basis_from_payload(payload: Mapping[str, Any]) -> AssertionBasis:
    state = _enum(AssertionBasisState, payload, "state")
    if state is AssertionBasisState.MISSING:
        # 缺失态不得携带正文；这里显式读一次，让"写了正文的缺失态"在读回时也被拒。
        if payload.get("text") or payload.get("text_digest"):
            raise ValueError("a missing assertion basis must not carry text")
        return AssertionBasis(revision=_revision(payload, "revision"), state=state)
    return AssertionBasis(
        revision=_revision(payload, "revision"),
        state=state,
        text=_text(payload, "text"),
        text_digest=_text(payload, "text_digest"),
    )


# ------------------------------------------------------------------ Case


def case_to_payload(case: Case, *, project_id: str) -> dict[str, object]:
    if not project_id.strip():
        raise ValueError("project_id must not be empty")
    return {
        "project_id": project_id,
        "case_id": case.case_id,
        "revision": case.revision,
        "layer": case.layer.value,
        "objective": case.objective,
        "preconditions": list(case.preconditions),
        "inputs": list(case.inputs),
        "steps": list(case.steps),
        "expected": case.expected,
        "verification_method": case.verification_method,
        "links": case_link_to_payload(case.links),
        "assertion_basis": assertion_basis_to_payload(case.assertion_basis),
        "independent_verification": case.independent_verification,
        "mock_scope": sorted(case.mock_scope),
        "importance": case.importance.value,
    }


def case_from_payload(payload: Mapping[str, Any]) -> Case:
    return Case(
        case_id=_text(payload, "case_id"),
        revision=_revision(payload, "revision"),
        layer=_enum(CaseLayer, payload, "layer"),
        objective=_text(payload, "objective"),
        preconditions=_text_tuple(payload, "preconditions"),
        inputs=_text_tuple(payload, "inputs"),
        steps=_text_tuple(payload, "steps"),
        expected=_text(payload, "expected"),
        verification_method=_text(payload, "verification_method"),
        links=case_link_from_payload(_require_mapping(payload.get("links"), "links")),
        assertion_basis=assertion_basis_from_payload(
            _require_mapping(payload.get("assertion_basis"), "assertion_basis")
        ),
        independent_verification=_optional_text(payload, "independent_verification"),
        mock_scope=_text_tuple(payload, "mock_scope"),
        importance=_enum(CaseImportance, payload, "importance"),
    )


# ------------------------------------------------------------------ AcceptanceScope


def acceptance_scope_to_payload(
    scope: AcceptanceScope, *, project_id: str
) -> dict[str, object]:
    if not project_id.strip():
        raise ValueError("project_id must not be empty")
    return {
        "project_id": project_id,
        "scope_id": scope.scope_id,
        "revision": scope.revision,
        "name": scope.name,
        "required_case_ids": sorted(scope.required_case_ids),
        "template_case_ids": sorted(scope.template_case_ids),
        "objective": scope.objective,
        "excluded_case_ids": sorted(scope.excluded_case_ids),
        "exclusion_reasons": [list(entry) for entry in scope.exclusion_reasons],
        "dependency_closure_ids": sorted(scope.dependency_closure_ids),
        "applicability_exclusions": [
            list(entry) for entry in scope.applicability_exclusions
        ],
    }


def acceptance_scope_from_payload(payload: Mapping[str, Any]) -> AcceptanceScope:
    return AcceptanceScope(
        scope_id=_text(payload, "scope_id"),
        revision=_revision(payload, "revision"),
        name=_text(payload, "name"),
        required_case_ids=_text_set(payload, "required_case_ids"),
        template_case_ids=_text_set(payload, "template_case_ids"),
        objective=_optional_text(payload, "objective") or "",
        excluded_case_ids=_text_set(payload, "excluded_case_ids"),
        exclusion_reasons=_pairs(payload, "exclusion_reasons"),
        dependency_closure_ids=_text_set(payload, "dependency_closure_ids"),
        applicability_exclusions=_pairs(payload, "applicability_exclusions"),
    )


# ------------------------------------------------------------------ ConfirmationRecord


def confirmation_to_payload(
    confirmation: ConfirmationRecord, *, project_id: str
) -> dict[str, object]:
    if not project_id.strip():
        raise ValueError("project_id must not be empty")
    return {
        "project_id": project_id,
        "confirmation_id": confirmation.confirmation_id,
        "case_id": confirmation.case_id,
        "basis_revision": confirmation.basis_revision,
        "basis_text_digest": confirmation.basis_text_digest,
        "confirmed_at_commit": confirmation.confirmed_at_commit,
    }


def confirmation_from_payload(payload: Mapping[str, Any]) -> ConfirmationRecord:
    return ConfirmationRecord(
        confirmation_id=_text(payload, "confirmation_id"),
        case_id=_text(payload, "case_id"),
        basis_revision=_revision(payload, "basis_revision"),
        basis_text_digest=_text(payload, "basis_text_digest"),
        confirmed_at_commit=_text(payload, "confirmed_at_commit"),
    )


# ------------------------------------------------------------------ CoverageContract


def coverage_contract_to_payload(contract: CoverageContract) -> dict[str, object]:
    """覆盖契约的落盘形状；集合顺序保持对象给定顺序。

    `payload_digest()` 对字典键做规范排序，因此同一份契约得到稳定摘要；
    `required_dimensions` / `critical_path_ids` 的**元素顺序**仍是契约内容的一部分，
    本函数不做重排，保证正常构造后可以逐值 round-trip。

    `coverage_claims` **只在非空时**追加：旧契约的空 claims 形状与旧 payload
    完全一致，旧计划摘要不变。
    """
    payload: dict[str, object] = {
        "contract_id": contract.contract_id,
        "understanding_revision": contract.understanding_revision,
        "function_requirements": [
            {
                "function_id": requirement.function_id,
                "required_dimensions": list(requirement.required_dimensions),
            }
            for requirement in contract.function_requirements
        ],
        "template_dispositions": [
            {
                "template_item_id": disposition.template_item_id,
                "applicability": _enum_text(
                    TemplateApplicability,
                    disposition.applicability,
                    "template disposition applicability",
                ),
                "coverage": _enum_text(
                    TemplateCoverage,
                    disposition.coverage,
                    "template disposition coverage",
                ),
            }
            for disposition in contract.template_dispositions
        ],
        "critical_path_ids": list(contract.critical_path_ids),
    }
    if contract.coverage_claims:
        payload["coverage_claims"] = [
            {
                "claim_id": claim.claim_id,
                "intent_id": claim.intent_id,
                "function_id": claim.function_id,
                "dimension": claim.dimension,
                "plan_revision": claim.plan_revision,
                "status": _enum_text(
                    CoverageClaimStatus, claim.status, "coverage claim status"
                ),
                "case_ids": sorted(claim.case_ids),
            }
            for claim in contract.coverage_claims
        ]
    return payload


def coverage_contract_from_payload(payload: Mapping[str, Any]) -> CoverageContract:
    """读回覆盖契约；枚举按字符串值解析，缺必需数组键则拒绝。"""
    function_requirements = tuple(
        FunctionRequirement(
            function_id=_text(item, "function_id"),
            required_dimensions=_required_text_tuple(item, "required_dimensions"),
        )
        for item in _mapping_list(payload, "function_requirements")
    )
    template_dispositions = tuple(
        TemplateDisposition(
            template_item_id=_text(item, "template_item_id"),
            applicability=_enum(TemplateApplicability, item, "applicability"),
            coverage=_enum(TemplateCoverage, item, "coverage"),
        )
        for item in _mapping_list(payload, "template_dispositions")
    )
    coverage_claims = tuple(
        CoverageClaim(
            claim_id=_text(item, "claim_id"),
            intent_id=_text(item, "intent_id"),
            function_id=_text(item, "function_id"),
            dimension=_text(item, "dimension"),
            plan_revision=_revision(item, "plan_revision"),
            status=_enum(CoverageClaimStatus, item, "status"),
            case_ids=_text_set(item, "case_ids"),
        )
        for item in _optional_mapping_list(payload, "coverage_claims")
    )
    return CoverageContract(
        contract_id=_text(payload, "contract_id"),
        understanding_revision=_revision(payload, "understanding_revision"),
        function_requirements=function_requirements,
        template_dispositions=template_dispositions,
        critical_path_ids=_required_text_tuple(payload, "critical_path_ids"),
        coverage_claims=coverage_claims,
    )


def plan_coverage_contract_from_payload(
    payload: Mapping[str, Any],
) -> CoverageContract | None:
    """从计划 payload 读取可选覆盖契约。

    旧计划记录没有 `coverage_contract` 键（或显式写 `null`）时返回 `None`；
    键存在但不是对象、或契约内容不完整时拒绝，不把损坏记录当成"没有契约"。
    """
    raw = payload.get("coverage_contract")
    if raw is None:
        return None
    return coverage_contract_from_payload(
        _require_mapping(raw, "coverage_contract")
    )


#: 本模块负责的形状；供合同测试核对覆盖面。
SERIALIZED_KINDS: tuple[str, ...] = (
    "case",
    "acceptance_scope",
    "case_link",
    "coverage_contract",
)


def case_content_digest(case: Case, *, project_id: str) -> str:
    """**单条**用例的内容身份摘要。

    与 `case_revision_digest()` 同一套规范形态，只是作用在一条用例上；
    供 `Plan.case_revisions[].digest` 使用——该值必须由服务端按用例内容算出，
    不接受调用方传入（否则等于允许伪造内容身份）。
    """
    encoded = json.dumps(
        case_to_payload(case, project_id=project_id),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def case_revision_digest(cases: Sequence[Case], *, project_id: str) -> str:
    """一组用例的规范摘要；供引用方核对"读到的是不是同一批用例"。"""
    canonical = sorted(
        (case_to_payload(case, project_id=project_id) for case in cases),
        key=lambda item: (str(item["case_id"]), item["revision"]),
    )
    encoded = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()
