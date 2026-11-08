"""功能覆盖 DTO：形状、校验与旧载荷兼容。

依据：设计文档 03 第 8.2—8.3 节（功能 × 维度状态、覆盖收据）与架构文档
《03-报告与缺陷》第 2 节（FunctionCoverage 记录字段）。本批只定义契约形状，
不接入报告生成，也不在视图重算覆盖结论。
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from aitest.contracts.views import CoverageDTO, FunctionCoverageDTO


def _coverage_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "selected_applicable_count": 4,
        "required_applicable_count": 3,
        "executed_count": 2,
        "reused_count": 1,
        "verified_count": 2,
        "passed_count": 2,
        "failed_count": 0,
        "unverified_count": 1,
        "required_verified_count": 2,
    }
    return payload | overrides


def _function_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "function_id": "order.create",
        "dimensions": {"normal": "proven", "error": "partial"},
        "overall_status": "partial",
        "gap_reason": "error 维度尚未独立核验",
        "case_ids": ("case-1", "case-2"),
        "evidence_refs": ("evidence-1",),
        "verification_refs": ("verification-1",),
    }
    return payload | overrides


def _function_coverage(**overrides: object) -> FunctionCoverageDTO:
    return FunctionCoverageDTO(**_function_payload(**overrides))  # type: ignore[arg-type]


# ------------------------------------------------------------ 旧载荷兼容


def test_legacy_coverage_dto_without_function_coverage_still_constructs() -> None:
    dto = CoverageDTO(**_coverage_payload())  # type: ignore[arg-type]
    assert dto.function_coverage == ()


def test_legacy_payload_without_function_coverage_validates() -> None:
    dto = CoverageDTO.model_validate(_coverage_payload())
    assert dto.required_verified_count == 2
    assert dto.function_coverage == ()


def test_legacy_payload_round_trips_through_json() -> None:
    original = CoverageDTO.model_validate(_coverage_payload())
    restored = CoverageDTO.model_validate_json(original.model_dump_json())
    assert restored == original


def test_function_coverage_is_the_last_field_with_an_empty_default() -> None:
    """新字段追加在末尾，旧关键字构造不受影响。"""
    field = CoverageDTO.model_fields["function_coverage"]
    assert field.default == ()
    assert tuple(CoverageDTO.model_fields)[-1] == "function_coverage"


# ------------------------------------------------------------ 新字段形状


def test_coverage_dto_accepts_function_coverage() -> None:
    dto = CoverageDTO(
        **_coverage_payload(function_coverage=(_function_coverage(),))  # type: ignore[arg-type]
    )
    assert len(dto.function_coverage) == 1
    function = dto.function_coverage[0]
    assert function.function_id == "order.create"
    assert function.dimensions == {"normal": "proven", "error": "partial"}
    assert function.overall_status == "partial"
    assert function.case_ids == ("case-1", "case-2")
    assert function.evidence_refs == ("evidence-1",)
    assert function.verification_refs == ("verification-1",)


def test_function_coverage_defaults_for_optional_refs() -> None:
    function = FunctionCoverageDTO(
        function_id="order.create",
        dimensions={"normal": "proven"},
        overall_status="proven",
    )
    assert function.gap_reason is None
    assert function.case_ids == ()
    assert function.evidence_refs == ()
    assert function.verification_refs == ()


def test_function_coverage_accepts_every_status_value() -> None:
    statuses = (
        "proven",
        "partial",
        "not_proven",
        "unsupported",
        "unknown",
        "not_applicable",
        "planned",
    )
    function = _function_coverage(
        dimensions={status: status for status in statuses},
        overall_status="unsupported",
    )
    assert set(function.dimensions.values()) == set(statuses)


def test_function_coverage_accepts_planned_overall_status() -> None:
    """计划预览态 `planned` 是合法状态；DTO 只承载事实，不重算汇总。"""

    function = _function_coverage(
        dimensions={"normal": "planned", "error": "planned"},
        overall_status="planned",
    )
    assert function.overall_status == "planned"


def test_coverage_dto_accepts_multiple_distinct_functions() -> None:
    dto = CoverageDTO(
        **_coverage_payload(  # type: ignore[arg-type]
            function_coverage=(
                _function_coverage(),
                _function_coverage(function_id="order.query", dimensions={"normal": "proven"}),
            )
        )
    )
    assert [item.function_id for item in dto.function_coverage] == [
        "order.create",
        "order.query",
    ]


def test_function_coverage_dto_round_trips_through_json() -> None:
    dto = CoverageDTO(
        **_coverage_payload(function_coverage=(_function_coverage(),))  # type: ignore[arg-type]
    )
    restored = CoverageDTO.model_validate_json(dto.model_dump_json())
    assert restored == dto


def test_function_coverage_json_preserves_wire_shape() -> None:
    data = json.loads(
        CoverageDTO(
            **_coverage_payload(function_coverage=(_function_coverage(),))  # type: ignore[arg-type]
        ).model_dump_json()
    )
    assert data["function_coverage"] == [
        {
            "function_id": "order.create",
            "dimensions": {"normal": "proven", "error": "partial"},
            "overall_status": "partial",
            "gap_reason": "error 维度尚未独立核验",
            "case_ids": ["case-1", "case-2"],
            "evidence_refs": ["evidence-1"],
            "verification_refs": ["verification-1"],
        }
    ]


# ------------------------------------------------------------ 校验拒绝


def test_duplicate_function_id_is_rejected() -> None:
    with pytest.raises(ValidationError, match="function_id must be unique"):
        CoverageDTO(
            **_coverage_payload(  # type: ignore[arg-type]
                function_coverage=(_function_coverage(), _function_coverage())
            )
        )


def test_duplicate_function_id_is_rejected_from_payload() -> None:
    with pytest.raises(ValidationError, match="function_id must be unique"):
        CoverageDTO.model_validate(
            _coverage_payload(
                function_coverage=[
                    _function_payload(),
                    _function_payload(dimensions={"normal": "proven"}),
                ]
            )
        )


def test_invalid_overall_status_is_rejected() -> None:
    with pytest.raises(ValidationError, match="overall_status must be one of"):
        _function_coverage(overall_status="covered")


def test_invalid_dimension_status_is_rejected() -> None:
    with pytest.raises(ValidationError, match="coverage status for normal must be one of"):
        _function_coverage(dimensions={"normal": "covered"})


def test_empty_dimensions_are_rejected() -> None:
    with pytest.raises(ValidationError, match="dimensions must not be empty"):
        _function_coverage(dimensions={})


def test_blank_function_id_is_rejected() -> None:
    with pytest.raises(ValidationError, match="function_id must not be empty"):
        _function_coverage(function_id="  ")


def test_blank_dimension_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="coverage dimension must not be empty"):
        _function_coverage(dimensions={" ": "proven"})


def test_blank_gap_reason_is_rejected() -> None:
    with pytest.raises(ValidationError, match="gap_reason must not be empty"):
        _function_coverage(gap_reason=" ")


@pytest.mark.parametrize("field", ["case_ids", "evidence_refs", "verification_refs"])
def test_blank_reference_is_rejected(field: str) -> None:
    with pytest.raises(ValidationError, match=f"{field} must not be empty"):
        _function_coverage(**{field: ("ref-1", " ")})


def test_unknown_function_coverage_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _function_coverage(unexpected="value")


def test_coverage_dto_rejects_non_negative_count_violations() -> None:
    """既有计数约束不因追加字段而丢失。"""
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        CoverageDTO(**_coverage_payload(executed_count=-1))  # type: ignore[arg-type]
