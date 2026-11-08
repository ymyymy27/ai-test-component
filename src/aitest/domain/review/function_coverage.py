"""业务功能 × 覆盖维度的投影记录。

`FunctionCoverage` 是计划/运行的投影，不是业务功能目录的一部分；覆盖事实
不反写目录。合同来源：设计文档《03-AI主导与非开发者体验设计草案》4.4、
4.8、8.2—8.3，以及架构文档《03-报告与缺陷》第 2 节。

`overall_coverage` 是**派生属性**，不是 dataclass 字段：构造时无法传入，
只能由 `coverage_by_dimension` 汇总，避免手填一个“已覆盖”掩盖维度缺口。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_items(values: Iterable[str], name: str) -> None:
    for value in values:
        if not value.strip():
            raise ValueError(f"{name} must not contain empty values")


class CoverageStatus(StrEnum):
    """功能 × 维度的覆盖状态，也是功能级汇总的取值。

    ``planned`` 只用于计划预览：计划已承诺该功能 × 维度、尚未执行，因此
    它既不是执行结果，也不能当作证据。报告与门禁不得把它写成 ``proven``。
    """

    PROVEN = "proven"
    PARTIAL = "partial"
    NOT_PROVEN = "not_proven"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    PLANNED = "planned"


def derive_overall_coverage(statuses: Iterable[CoverageStatus]) -> CoverageStatus:
    """把各维度状态汇总为功能级总体覆盖；空集合拒绝。

    ``not_applicable`` 维度不构成缺口、也不计入分母，先被忽略。忽略后：

    1. 没有剩余的可适用维度（全部为 ``not_applicable``）→ ``not_applicable``；
    2. 任一 ``unsupported`` → ``unsupported``；
    3. 任一 ``unknown`` → ``unknown``；
    4. 存在 ``planned`` 且还有非 ``planned`` 的 ``not_proven`` → ``not_proven``；
    5. 全部为 ``planned`` → ``planned``；
    6. 全部 ``proven`` → ``proven``；
    7. 全部 ``not_proven`` → ``not_proven``；
    8. 其余（含 proven/partial 但未全覆盖）→ ``partial``。

    ``unsupported`` / ``unknown`` 高于 ``partial``、也高于其他已覆盖维度：
    功能级汇总不能把“已知能力缺口”或“未识别区域”掩盖成普通的“部分覆盖”。
    同时存在 ``unsupported`` 与 ``unknown`` 时取 ``unsupported``——它是已登记
    `CapabilityGap` 的确定限制，而 ``unknown`` 是尚未判定的不确定性。

    ``planned`` 是计划预览态，只能在不掩盖缺口时单独成立：任一已登记缺口
    优先于它。纯既知事实（不含 ``planned``）仍按第 6—8 条汇总，避免改变
    已发布投影的既有语义（``proven`` + ``not_proven`` 仍是 ``partial``）。
    """

    observed: set[CoverageStatus] = set()
    for status in statuses:
        try:
            observed.add(CoverageStatus(str(status)))
        except ValueError as exc:
            raise ValueError(f"unmapped coverage status: {status!r}") from exc
    if not observed:
        raise ValueError("coverage requires at least one dimension status")
    applicable = observed - {CoverageStatus.NOT_APPLICABLE}
    if not applicable:
        return CoverageStatus.NOT_APPLICABLE
    if CoverageStatus.UNSUPPORTED in applicable:
        return CoverageStatus.UNSUPPORTED
    if CoverageStatus.UNKNOWN in applicable:
        return CoverageStatus.UNKNOWN
    if CoverageStatus.PLANNED in applicable:
        remainder = applicable - {CoverageStatus.PLANNED}
        if CoverageStatus.NOT_PROVEN in remainder:
            return CoverageStatus.NOT_PROVEN
        if not remainder:
            return CoverageStatus.PLANNED
    if applicable == {CoverageStatus.PROVEN}:
        return CoverageStatus.PROVEN
    if applicable == {CoverageStatus.NOT_PROVEN}:
        return CoverageStatus.NOT_PROVEN
    return CoverageStatus.PARTIAL


@dataclass(frozen=True, slots=True)
class FunctionCoverage:
    """某个业务功能在本次计划/运行中的覆盖投影。

    `plan_revision` 与 `run_id` 至少提供一个：投影要么来自计划（覆盖收据
    预览），要么来自运行。`coverage_by_dimension` 不能为空，否则“没有缺口”
    与“没有维度”无法区分。
    """

    coverage_id: str
    function_id: str
    plan_revision: int | None
    run_id: str | None
    coverage_by_dimension: Mapping[str, CoverageStatus]
    updated_at: datetime
    case_ids: frozenset[str] = frozenset()
    evidence_refs: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()
    gap_reason: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.coverage_id, "coverage_id")
        _require_text(self.function_id, "function_id")
        if self.plan_revision is None and self.run_id is None:
            raise ValueError("coverage requires a plan revision or a run id")
        if self.plan_revision is not None and self.plan_revision < 1:
            raise ValueError("plan_revision must be >= 1")
        if self.run_id is not None:
            _require_text(self.run_id, "run_id")
        if not self.coverage_by_dimension:
            raise ValueError("coverage_by_dimension must not be empty")

        normalized: dict[str, CoverageStatus] = {}
        for dimension, status in self.coverage_by_dimension.items():
            _require_text(dimension, "coverage dimension")
            try:
                normalized[dimension] = CoverageStatus(str(status))
            except ValueError as exc:
                raise ValueError(
                    f"unmapped coverage status for {dimension}: {status!r}"
                ) from exc
        object.__setattr__(
            self, "coverage_by_dimension", MappingProxyType(normalized)
        )

        _require_items(self.case_ids, "case_ids")
        _require_items(self.evidence_refs, "evidence_refs")
        _require_items(self.verification_refs, "verification_refs")
        if self.gap_reason is not None:
            _require_text(self.gap_reason, "gap_reason")

    @property
    def overall_coverage(self) -> CoverageStatus:
        """功能级汇总；由维度状态派生，构造时不能手填。"""

        return derive_overall_coverage(self.coverage_by_dimension.values())
