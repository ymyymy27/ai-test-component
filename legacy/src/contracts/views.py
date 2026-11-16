"""DTOs serialize core facts; views must not calculate business outcomes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .responses import Response as Response

__all__ = ["CoverageDTO", "FunctionCoverageDTO", "Response"]

#: 功能 × 维度覆盖状态的全部合法取值（与领域 `CoverageStatus` 的值一致）。
#: 契约层不导入领域模块，只序列化领域已算好的事实，不在视图重算结论。
#: `planned` 只用于计划确认页的覆盖预览，表示“已承诺、尚未执行”。
_ALLOWED_COVERAGE_STATUSES = frozenset(
    {
        "proven",
        "partial",
        "not_proven",
        "unsupported",
        "unknown",
        "not_applicable",
        "planned",
    }
)


def _require_nonblank(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_status(value: str, name: str) -> None:
    if value not in _ALLOWED_COVERAGE_STATUSES:
        raise ValueError(
            f"{name} must be one of {', '.join(sorted(_ALLOWED_COVERAGE_STATUSES))}"
        )


class FunctionCoverageDTO(BaseModel):
    """单个业务功能的覆盖投影（只承载事实，不派生业务结论）。

    ``dimensions`` 是“必要维度 → 覆盖状态”，不能为空，否则“没有缺口”与
    “没有维度”无法区分；``overall_status`` 由核心按唯一判定口径算好，
    视图不重算。``gap_reason`` 说明缺口，不替代状态。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    function_id: str
    dimensions: dict[str, str]
    overall_status: str
    gap_reason: str | None = None
    case_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_function_coverage(self) -> FunctionCoverageDTO:
        _require_nonblank(self.function_id, "function_id")
        if not self.dimensions:
            raise ValueError("dimensions must not be empty")
        for dimension, status in self.dimensions.items():
            _require_nonblank(dimension, "coverage dimension")
            _require_status(status, f"coverage status for {dimension}")
        _require_status(self.overall_status, "overall_status")
        if self.gap_reason is not None:
            _require_nonblank(self.gap_reason, "gap_reason")
        for name, refs in (
            ("case_ids", self.case_ids),
            ("evidence_refs", self.evidence_refs),
            ("verification_refs", self.verification_refs),
        ):
            for ref in refs:
                _require_nonblank(ref, name)
        return self


class CoverageDTO(BaseModel):
    selected_applicable_count: int = Field(ge=0)
    required_applicable_count: int = Field(ge=0)
    executed_count: int = Field(ge=0)
    reused_count: int = Field(ge=0)
    verified_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    unverified_count: int = Field(ge=0)
    required_verified_count: int = Field(ge=0)
    #: 功能级覆盖收据；旧载荷不传时为空元组，保持既有形状兼容。
    function_coverage: tuple[FunctionCoverageDTO, ...] = ()

    @model_validator(mode="after")
    def validate_function_coverage(self) -> CoverageDTO:
        seen: set[str] = set()
        for item in self.function_coverage:
            if item.function_id in seen:
                raise ValueError(
                    "function_id must be unique in function_coverage: "
                    f"{item.function_id}"
                )
            seen.add(item.function_id)
        return self
